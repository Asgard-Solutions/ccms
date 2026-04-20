"""
Authorization policy engine — single source of truth for access decisions.

Default-deny. Resolves a user's effective permissions by walking:
  1. Active role grants (role_permissions joined via user_roles)
  2. Active elevation grants (elevation_requests where status=approved, unused, not expired)
  3. Legacy `users.role` string → baseline role mapping (shim for dual-run)

The engine returns a `Decision` object carrying the *scope* the caller has on
the target resource, plus any policy overlays (MFA / approval / break-glass).

Row-level filtering
-------------------
For list endpoints, call `scope_filter(user, resource, action)` to obtain the
Mongo query clause that restricts results to only the records the caller may
see (e.g. `{"user_id": user_id}` for self scope, `{"provider_id": user_id}`
for assigned_patients, etc). The router composes this with its own filters.

Usage
-----
    allowed = await evaluate(user, "patient", "read", resource_ctx={"patient_id": p})
    if not allowed.allow:
        raise HTTPException(403, allowed.reason or "Forbidden")

Or, for route guards:

    @router.get("/patients")
    async def list_patients(
        request: Request,
        user: dict = Depends(require_permission("patient", "read")),
    ):
        ...
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import HTTPException, Request, status

from core.audit import audit_failure, log_audit
from core.db import get_db_read, get_db_write
from core import metrics
from services.authz.constants import (
    LEGACY_ROLE_TO_KEY,
    PERMISSIONS,
    ROLE_GRANTS,
    permission_key,
)

logger = logging.getLogger("ccms.authz")

# If set (test / recovery), bypass the new policy engine. Audits are still written.
AUTHZ_BYPASS = (os.environ.get("AUTHZ_BYPASS") or "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Decision objects
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    allow: bool
    scope: str | None = None
    requires_mfa: bool = False
    requires_approval: bool = False
    break_glass_allowed: bool = False
    reason: str | None = None
    via_elevation: bool = False
    source_role: str | None = None


@dataclass
class EffectiveGrant:
    permission_key: str
    scope: str
    requires_mfa: bool = False
    requires_approval: bool = False
    break_glass_allowed: bool = False
    source_role: str = ""          # role_key
    via_elevation: bool = False


# Scope precedence — higher number = broader.
SCOPE_RANK = {
    None: -1,
    "self": 0,
    "no_phi": 1,
    "assigned_patients": 2,
    "assigned_location": 3,
    "all_location_patients": 4,
    "phi_limited": 5,
    "phi_full": 6,
    "all_org": 7,
}


def _broader(a: str, b: str) -> str:
    return a if SCOPE_RANK.get(a, -1) >= SCOPE_RANK.get(b, -1) else b


# ---------------------------------------------------------------------------
# Effective permission resolution
# ---------------------------------------------------------------------------

async def effective_grants(user: dict) -> list[EffectiveGrant]:
    """Returns the flattened, deduped list of grants for this user.

    Merges:
      - DB-backed `user_roles` x `role_permissions`
      - ACTIVE elevation_requests (status=approved, not expired, not used)
      - legacy `users.role` → baseline role fallback (when user has no user_roles rows)
    """
    db = get_db_read()
    uid = user["id"]

    grants: dict[str, EffectiveGrant] = {}

    # --- role grants (DB-backed) ---
    role_keys: list[str] = []
    async for ur in db.user_roles.find({"user_id": uid, "status": "active"}, {"_id": 0}):
        role_keys.append(ur["role_key"])

    # Fallback shim for the dual-run period: if the user has zero rows in
    # user_roles, use the legacy `users.role` string → baseline role.
    legacy_shim = False
    if not role_keys:
        legacy = (user.get("role") or "").lower()
        mapped = LEGACY_ROLE_TO_KEY.get(legacy)
        if mapped:
            role_keys.append(mapped)
            legacy_shim = True

    if role_keys:
        async for rp in db.role_permissions.find(
            {"role_key": {"$in": role_keys}}, {"_id": 0},
        ):
            key = rp["permission_key"]
            cur = grants.get(key)
            cand = EffectiveGrant(
                permission_key=key,
                scope=rp.get("scope") or "all_org",
                requires_mfa=bool(rp.get("requires_mfa")),
                requires_approval=bool(rp.get("requires_approval")),
                break_glass_allowed=bool(rp.get("break_glass_allowed")),
                source_role=rp.get("role_key", ""),
            )
            if cur is None or SCOPE_RANK.get(cand.scope, -1) > SCOPE_RANK.get(cur.scope, -1):
                grants[key] = cand
            else:
                # If same scope, pick the LESS restrictive (no MFA/APR wins)
                cur.requires_mfa = cur.requires_mfa and cand.requires_mfa
                cur.requires_approval = cur.requires_approval and cand.requires_approval
                cur.break_glass_allowed = cur.break_glass_allowed or cand.break_glass_allowed

    # --- active elevation grants ---
    now = datetime.now(timezone.utc)
    async for el in db.elevation_requests.find(
        {"requester_id": uid, "status": "approved", "used_at": None}, {"_id": 0},
    ):
        exp = el.get("expires_at")
        if isinstance(exp, str):
            try:
                exp_dt = datetime.fromisoformat(exp)
            except ValueError:
                continue
        else:
            exp_dt = exp
        if exp_dt and exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        if not exp_dt or exp_dt < now:
            continue
        key = el["permission_key"]
        grants[key] = EffectiveGrant(
            permission_key=key,
            scope=el.get("scope") or "all_org",
            requires_mfa=True,
            requires_approval=False,   # already approved
            break_glass_allowed=False,
            source_role="elevation:" + str(el["id"]),
            via_elevation=True,
        )

    # --- per-user permission overrides (exceptions) ---
    # These are stored in `permission_scopes` and always take precedence over
    # role grants. They can either GRANT a permission not in the role or
    # BROADEN a scope the role already covers.
    async for ov in db.permission_scopes.find(
        {"user_id": uid, "status": "active"}, {"_id": 0},
    ):
        exp = ov.get("expires_at")
        if exp:
            if isinstance(exp, str):
                try:
                    exp_dt = datetime.fromisoformat(exp)
                except ValueError:
                    continue
            else:
                exp_dt = exp
            if exp_dt and exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if not exp_dt or exp_dt < now:
                continue
        key = ov["permission_key"]
        cur = grants.get(key)
        cand = EffectiveGrant(
            permission_key=key,
            scope=ov.get("scope") or "all_org",
            requires_mfa=bool(ov.get("requires_mfa")),
            requires_approval=bool(ov.get("requires_approval")),
            break_glass_allowed=bool(ov.get("break_glass_allowed")),
            source_role="override:" + str(ov["id"]),
        )
        # Override always wins if it broadens scope OR if permission was
        # otherwise absent; if it would narrow an existing grant, the role
        # grant stays (least-astonishment).
        if cur is None or SCOPE_RANK.get(cand.scope, -1) >= SCOPE_RANK.get(cur.scope, -1):
            grants[key] = cand

    if legacy_shim and not AUTHZ_BYPASS:
        logger.debug("authz legacy-role shim used for user=%s role=%s", uid, user.get("role"))
    return list(grants.values())


async def effective_grants_map(user: dict) -> dict[str, EffectiveGrant]:
    return {g.permission_key: g for g in await effective_grants(user)}


# ---------------------------------------------------------------------------
# evaluate() — the core decision
# ---------------------------------------------------------------------------

async def evaluate(
    user: dict,
    resource: str,
    action: str,
    resource_ctx: dict | None = None,
) -> Decision:
    """Returns a Decision. Default-deny."""
    key = permission_key(resource, action)
    ctx = resource_ctx or {}

    if AUTHZ_BYPASS:
        return Decision(allow=True, scope="all_org", reason="authz_bypass")

    grants = await effective_grants_map(user)
    grant = grants.get(key)
    if not grant:
        try:
            metrics.authz_denials_total.labels(resource=resource, action=action).inc()
        except Exception:
            pass
        return Decision(allow=False, reason=f"no_grant_for_{key}")

    # Scope-specific resource-level check
    scope = grant.scope

    # "self" scope -> resource must be owned by current user
    if scope == "self":
        if not _matches_self(user, ctx):
            return Decision(
                allow=False, scope=scope, reason="scope_self_mismatch",
                source_role=grant.source_role,
            )

    # "assigned_patients" -> patient must be in user's assigned list
    if scope == "assigned_patients":
        pid = ctx.get("patient_id") or ctx.get("entity_id")
        if pid and not await _is_assigned_patient(user["id"], pid):
            return Decision(
                allow=False, scope=scope, reason="patient_not_assigned",
                source_role=grant.source_role, break_glass_allowed=grant.break_glass_allowed,
            )

    # "assigned_location" / "all_location_patients" -> location-based
    if scope in ("assigned_location", "all_location_patients"):
        loc_id = ctx.get("location_id")
        if loc_id and not await _is_user_in_location(user["id"], loc_id):
            return Decision(
                allow=False, scope=scope, reason="location_not_assigned",
                source_role=grant.source_role, break_glass_allowed=grant.break_glass_allowed,
            )

    try:
        metrics.authz_allows_total.labels(resource=resource, action=action).inc()
    except Exception:
        pass

    return Decision(
        allow=True,
        scope=scope,
        requires_mfa=grant.requires_mfa,
        requires_approval=grant.requires_approval,
        break_glass_allowed=grant.break_glass_allowed,
        source_role=grant.source_role,
        via_elevation=grant.via_elevation,
    )


def _matches_self(user: dict, ctx: dict) -> bool:
    # Patient-portal-style self check: the target entity must identify the user.
    if not ctx:
        return True   # no specific resource instance, defer to route-level checks
    if ctx.get("owner_user_id") and ctx["owner_user_id"] == user["id"]:
        return True
    if ctx.get("patient_user_id") and ctx["patient_user_id"] == user["id"]:
        return True
    return False


async def _is_assigned_patient(user_id: str, patient_id: str) -> bool:
    db = get_db_read()
    row = await db.patient_assignments.find_one(
        {"provider_id": user_id, "patient_id": patient_id, "status": "active"},
        {"_id": 0, "id": 1},
    )
    return bool(row)


async def _is_user_in_location(user_id: str, location_id: str) -> bool:
    db = get_db_read()
    row = await db.user_location_assignments.find_one(
        {"user_id": user_id, "location_id": location_id, "status": "active"},
        {"_id": 0, "id": 1},
    )
    return bool(row)


# ---------------------------------------------------------------------------
# scope_filter() — row-level query builder for list endpoints
# ---------------------------------------------------------------------------

async def scope_filter(user: dict, resource: str, action: str) -> dict:
    """Returns a Mongo query fragment that row-level restricts a collection
    according to the user's effective scope for (resource, action).

    An *empty* dict means "no additional restriction" (all_org).
    A `{"__deny__": True}` fragment means "nothing visible" and callers must
    short-circuit the query.
    """
    decision = await evaluate(user, resource, action)
    if not decision.allow:
        return {"__deny__": True}
    scope = decision.scope

    if scope in (None, "all_org"):
        return {}
    if scope == "self":
        # resource-specific mapping
        if resource in ("patient", "patient_chart", "soap_note", "treatment_plan",
                        "intake_form", "insurance", "document", "consent",
                        "privacy_request", "session", "message"):
            return {"user_id": user["id"]}
        if resource == "appointment":
            # appointment schema uses patient_id linked to patient.user_id
            return {"patient_user_id": user["id"]}
        return {"owner_user_id": user["id"]}
    if scope == "assigned_patients":
        pids = await _patient_ids_assigned_to(user["id"])
        return {"patient_id": {"$in": pids}} if pids else {"__deny__": True}
    if scope in ("assigned_location", "all_location_patients"):
        locs = await _user_location_ids(user["id"])
        if not locs:
            return {"__deny__": True}
        return {"location_id": {"$in": locs}}
    if scope == "no_phi":
        # caller should apply phi-masking; no row restriction.
        return {}
    if scope in ("phi_limited", "phi_full"):
        return {}
    return {}


async def _patient_ids_assigned_to(user_id: str) -> list[str]:
    db = get_db_read()
    cur = db.patient_assignments.find(
        {"provider_id": user_id, "status": "active"}, {"_id": 0, "patient_id": 1},
    )
    return [r["patient_id"] async for r in cur]


async def _user_location_ids(user_id: str) -> list[str]:
    db = get_db_read()
    cur = db.user_location_assignments.find(
        {"user_id": user_id, "status": "active"}, {"_id": 0, "location_id": 1},
    )
    return [r["location_id"] async for r in cur]


# ---------------------------------------------------------------------------
# FastAPI dependency: require_permission(resource, action)
# ---------------------------------------------------------------------------

def require_permission(
    resource: str,
    action: str,
    *,
    ctx_from_path: dict[str, str] | None = None,
    audit_allow: bool = True,
):
    """FastAPI dependency factory.

    Args:
        resource, action: permission key components.
        ctx_from_path: optional mapping of resource-context keys to path
            parameter names (e.g. `{"patient_id": "patient_id"}`).
        audit_allow: when False, skip the `authz.allow` audit row. Use this
            for routes that already emit a semantic audit (e.g. the patient
            list handler writes `patient.list_viewed`) so we don't double
            audit volume. Denials and MFA/approval gates are always audited.
    """
    from core.deps import get_current_user  # lazy import to avoid cycle

    async def _dep(request: Request) -> dict:
        user = await get_current_user(request)

        # Platform admin bypass — global support staff may act on any
        # resource. Every bypass is audited explicitly so cross-tenant
        # platform access is always traceable.
        if user.get("is_platform_admin") or user.get("role") == "platform_admin":
            from core.audit import audit_success
            await audit_success(
                user, "authz.platform_admin_bypass", request,
                entity_type=resource,
                metadata={"resource": resource, "action": action,
                          "platform_admin_access": True},
            )
            return user

        ctx: dict = {}
        if ctx_from_path:
            for k, path_param in ctx_from_path.items():
                val = request.path_params.get(path_param)
                if val is not None:
                    ctx[k] = val

        decision = await evaluate(user, resource, action, ctx)
        if not decision.allow:
            # Deny audit (high-signal for failed authorization report)
            await audit_failure(
                action="authz.denied",
                request=request,
                actor_email=user.get("email"),
                reason=decision.reason,
                metadata={
                    "actor_id": user.get("id"),
                    "role": user.get("role"),
                    "resource": resource,
                    "target_action": action,
                    "scope": decision.scope,
                    "break_glass_allowed": decision.break_glass_allowed,
                    **ctx,
                },
            )
            detail = "Break-glass available" if decision.break_glass_allowed else "Forbidden"
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail)

        # MFA gate for sensitive actions — require the reauth cookie.
        # Iteration 19: also enforce MFA when user.step_up_required is True
        # (e.g. suspicious login detected, or outstanding break-glass
        # attestation overdue). The gate is active for ANY permission
        # beyond the trivial self-read actions so the operator cannot
        # silently bypass it via a non-MFA-flagged permission.
        step_up = bool(user.get("step_up_required"))
        trivial = resource in ("self",) or (
            resource == "session" and action in ("read_self",)
        )
        if decision.requires_mfa or (step_up and not trivial):
            if not (request.headers.get("x-reauth-token") or request.cookies.get("reauth_token")):
                response_headers = {"X-Reauth-Required": "1"}
                await audit_failure(
                    action="authz.mfa_required",
                    request=request,
                    actor_email=user.get("email"),
                    reason="reauth_missing" + ("_step_up" if step_up and not decision.requires_mfa else ""),
                    metadata={
                        "resource": resource, "target_action": action,
                        "step_up_required": step_up,
                        **ctx,
                    },
                )
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "Re-authentication required for this action.",
                    headers=response_headers,
                )
            # Full reauth validation is done by core.reauth.require_reauth
            # inside the route when needed; we only assert presence here.

        # Approval gate: must be satisfied by an active elevation grant
        # (evaluate() already consumed elevation if present via via_elevation).
        if decision.requires_approval and not decision.via_elevation:
            await audit_failure(
                action="authz.approval_required",
                request=request,
                actor_email=user.get("email"),
                reason="elevation_required",
                metadata={
                    "resource": resource, "target_action": action,
                    **ctx,
                },
            )
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "This action requires approval. Please request an elevation.",
            )

        # Success audit — only emit when the route hasn't opted out. Most
        # migrated routes already write a semantic audit (e.g.
        # `patient.list_viewed`) so we skip the authz.allow row there.
        if audit_allow:
            await log_audit(
                action="authz.allow",
                actor_id=user.get("id"),
                actor_email=user.get("email"),
                actor_role=user.get("role"),
                entity_type=resource,
                entity_id=ctx.get("patient_id") or ctx.get("entity_id"),
                request=request,
                metadata={
                    "target_action": action,
                    "scope": decision.scope,
                    "via_elevation": decision.via_elevation,
                    "source_role": decision.source_role,
                },
            )
        # Stash decision on request.state for route-level inspection.
        request.state.authz = decision
        return user

    return _dep


# ---------------------------------------------------------------------------
# Consumption of one-shot elevation grant after the protected action succeeds
# ---------------------------------------------------------------------------

async def consume_elevation_if_used(user_id: str, permission_key_str: str) -> None:
    db = get_db_write()
    now = datetime.now(timezone.utc).isoformat()
    await db.elevation_requests.update_one(
        {"requester_id": user_id, "permission_key": permission_key_str,
         "status": "approved", "used_at": None},
        {"$set": {"status": "used", "used_at": now}},
    )
