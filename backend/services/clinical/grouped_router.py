"""
Grouped clinical read endpoints — Phase 2 Wave A (presentation-layer joins).

These endpoints DO NOT mutate, migrate, or duplicate any source record.
They join existing appointment / encounter / follow-up-note / billing-
readiness rows by their existing authoritative keys and return a shaped
response with:

  * `schema_version: "1.0"` — clients pin to this and fail-open when the
    server ships a newer major version.
  * Every source record's authoritative id preserved on the group
    (`appointment_id`, `encounter_id`, `note_ids[]`, `billing_readiness_id`).
  * Orphaned records surfaced as their own single-source groups — never
    dropped.
  * Grouping keyed on relationships (`appointment_id`, `encounter.appointment_id`).
    Never on timestamps alone.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter
from services.clinical.router import _load_patient

router = APIRouter(prefix="/patients", tags=["clinical"])

SCHEMA_VERSION = "1.0"
# Phase 3 Slice 2 — timeline filter surface. Bumped independently from
# the encounters grouping so encounters clients don't need to redeploy
# when timeline filters change.
TIMELINE_SCHEMA_VERSION = "1.1"

# Allow-listed filter vocabularies. MUST match backend `identity/models.py`
# `TimelineEventKind` / `TimelineSource` and frontend
# `pages/clinical/timelinePresetsSchema.js`.
_TIMELINE_KINDS: set[str] = {
    "visit", "initial_exam", "treatment_plan", "clinical_media", "outcome_entry",
}
_TIMELINE_SOURCES: set[str] = {
    "appointment", "encounter", "note", "initial_exam", "reexam", "outcome", "media",
}
_TIMELINE_DATE_WINDOWS: dict[str, int] = {
    "last_7d": 7,
    "last_30d": 30,
    "last_90d": 90,
    "last_180d": 180,
    "last_365d": 365,
    "all": 0,
}

# Approved (allow-listed) billing-readiness messages surfaced on the
# chart-wide aggregate. Any check key not in this map is COUNTED (for
# warning_count / blocked_count) but never contributes a human-readable
# `top_message`. This gives ops a controlled vocabulary for the Current
# Care Status row and prevents free-form check details from leaking.
#
# The listing order below IS the deterministic priority used to pick
# `top_message` — blocked (fail) keys win over warning keys, and within
# each severity band the first matching key in this iteration order is
# chosen. Keep this list in lockstep with billing_readiness_router.py.
_FAIL_KEYS_PRIORITY: dict[str, str] = {
    "eligibility_verified":  "Insurance eligibility not verified",
    "diagnosis_linked":      "Diagnosis linkage incomplete",
    "note_signed":           "Note not signed",
    "signature_present":     "Provider signature missing",
    "note_exists":           "Chart note missing",
    "treatment_documented":  "Treatment not documented",
    "provider_present":      "Provider missing on encounter",
    "patient_present":       "Patient missing on encounter",
    "dos_present":           "Date of service missing",
    "plan_linkage":          "Treatment plan linkage incomplete",
}
_WARN_KEYS_PRIORITY: dict[str, str] = {
    "objective_findings":    "Objective findings not captured",
    "response_documented":   "Response to care not documented",
    "encounter_completed":   "Encounter not marked completed",
    "appointment_linked":    "Appointment not linked",
    "reexam_not_overdue":    "Re-exam overdue",
    "eligibility_verified":  "Insurance eligibility not verified",
}


# ----- helpers ------------------------------------------------------

def _encounter_workflow(enc: dict) -> str:
    """Map an encounter row's status to the Phase 2 Workflow vocabulary."""
    if not enc:
        return "scheduled"
    s = enc.get("status")
    return {
        "in_progress": "in_progress",
        "completed": "completed",
        "cancelled": "cancelled",
    }.get(s, "scheduled")


def _appointment_workflow(appt: dict) -> str:
    """Map appointment.status to Workflow vocabulary when there's no encounter yet."""
    if not appt:
        return "scheduled"
    s = (appt.get("status") or "").lower()
    return {
        "scheduled": "scheduled",
        "checked_in": "checked_in",
        "in_progress": "in_progress",
        "completed": "completed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "no_show": "cancelled",
    }.get(s, "scheduled")


def _doc_status(encounter: Optional[dict], notes: list[dict]) -> str:
    """Documentation vocabulary — signed / draft / amended / missing."""
    # Amended addenda live off signed notes; treat presence of signed as
    # winning here — the note badge itself carries "amended" via its own
    # UI later.
    if any(n.get("sign_status") == "signed" for n in notes):
        return "signed"
    if encounter and encounter.get("sign_status") == "signed":
        return "signed"
    if any(n.get("sign_status") in {"draft", "sign_ready"} for n in notes):
        return "draft"
    if encounter and encounter.get("sign_status") in {"draft", "sign_ready"}:
        return "draft"
    if encounter or notes:
        # There's an encounter/note but no draft or signed sign_status — treat
        # as draft (in-flight documentation).
        return "draft"
    return "missing"


def _billing_status(readiness: Optional[dict]) -> str:
    if not readiness:
        return "not_evaluated"
    checks = readiness.get("checks") or []
    if not checks:
        return "not_evaluated"
    if any(c.get("severity") == "fail" and not c.get("passed") for c in checks):
        return "blocked"
    if any(not c.get("passed") for c in checks):
        return "warning"
    return "ready"


def _clinical_response(notes: list[dict], encounter: Optional[dict]) -> str:
    # The follow-up note carries `patient_response` in {improving, stable,
    # worsening}. Pull the latest recorded one for this visit.
    candidates = list(notes)
    if encounter:
        candidates.append(encounter)
    for row in sorted(candidates, key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True):
        v = row.get("patient_response")
        if v in {"improving", "stable", "worsening"}:
            return v
    return "not_recorded"


# ----- /clinical/encounters/grouped ---------------------------------

@router.get("/{patient_id}/clinical/encounters/grouped")
async def list_grouped_encounters(
    patient_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)

    scope_appt = scoped_filter({"patient_id": patient_id}, ctx, location_scoped=True)
    scope_enc = scoped_filter({"patient_id": patient_id}, ctx, location_scoped=False)
    scope_note = scoped_filter({"patient_id": patient_id}, ctx, location_scoped=False)

    if scope_appt.get("__deny__") or scope_enc.get("__deny__"):
        return {"schema_version": SCHEMA_VERSION, "groups": []}

    appointments = [d async for d in db.appointments.find(scope_appt, {"_id": 0})]
    encounters = [d async for d in db.clinical_encounters.find(scope_enc, {"_id": 0})]
    notes = [d async for d in db.clinical_follow_up_notes.find(scope_note, {"_id": 0})]
    readiness = [
        d async for d in db.clinical_billing_readiness.find(scope_enc, {"_id": 0})
    ]

    # Index for O(1) lookup by authoritative key.
    enc_by_appt: dict[str, dict] = {}
    enc_by_id: dict[str, dict] = {}
    orphan_encounters: list[dict] = []
    for e in encounters:
        enc_by_id[e["id"]] = e
        if e.get("appointment_id"):
            enc_by_appt[e["appointment_id"]] = e
        else:
            orphan_encounters.append(e)

    notes_by_encounter: dict[str, list[dict]] = {}
    orphan_notes: list[dict] = []
    for n in notes:
        eid = n.get("encounter_id")
        if eid and eid in enc_by_id:
            notes_by_encounter.setdefault(eid, []).append(n)
        else:
            orphan_notes.append(n)

    readiness_by_encounter: dict[str, dict] = {}
    for r in readiness:
        eid = r.get("encounter_id")
        if eid:
            readiness_by_encounter[eid] = r

    groups: list[dict] = []

    def _group_from_appt_and_enc(appt: Optional[dict], enc: Optional[dict]):
        note_list = notes_by_encounter.get(enc["id"], []) if enc else []
        rdy = readiness_by_encounter.get(enc["id"]) if enc else None
        # visit_key: appointment_id if available (authoritative for visits),
        # else encounter_id (orphan encounter), else note_id (orphan note).
        key = None
        if appt:
            key = f"appt:{appt['id']}"
        elif enc:
            key = f"enc:{enc['id']}"
        # ISO datetime for sorting; explicit source, never invented.
        visit_at = None
        if appt:
            visit_at = appt.get("start_time")
        elif enc:
            visit_at = enc.get("date_of_service") or enc.get("created_at")
        elif note_list:
            visit_at = note_list[0].get("date_of_service") or note_list[0].get("created_at")

        return {
            "group_key": key,
            "visit_at": visit_at,
            "appointment_type": (appt or {}).get("appointment_type") or (enc or {}).get("encounter_type"),
            "visit_number": (enc or {}).get("visit_number"),
            "episode_id": (enc or {}).get("episode_id") or (appt or {}).get("episode_id"),
            "provider_id": (enc or {}).get("provider_id") or (appt or {}).get("provider_id"),
            "provider_name": (enc or {}).get("provider_name") or (appt or {}).get("provider_name"),
            "status": {
                "workflow": _encounter_workflow(enc) if enc else _appointment_workflow(appt),
                "documentation": _doc_status(enc, note_list),
                "clinical_response": _clinical_response(note_list, enc),
                "billing": _billing_status(rdy),
            },
            "source_ids": {
                "appointment_id": (appt or {}).get("id"),
                "encounter_id": (enc or {}).get("id"),
                "note_ids": [n["id"] for n in note_list],
                "billing_readiness_id": (rdy or {}).get("id"),
            },
            "orphaned": appt is None,  # true when we couldn't tie back to a scheduled appointment
        }

    # 1. Appointment-anchored groups (canonical: one visit = one appt).
    for a in appointments:
        e = enc_by_appt.get(a["id"])
        groups.append(_group_from_appt_and_enc(a, e))

    # 2. Orphan encounters (encounter without appointment link).
    for e in orphan_encounters:
        groups.append(_group_from_appt_and_enc(None, e))

    # 3. Orphan notes (note without a persisted encounter).
    for n in orphan_notes:
        groups.append({
            "group_key": f"note:{n['id']}",
            "visit_at": n.get("date_of_service") or n.get("created_at"),
            "appointment_type": None,
            "visit_number": None,
            "episode_id": n.get("episode_id"),
            "provider_id": n.get("provider_id"),
            "provider_name": n.get("provider_name"),
            "status": {
                "workflow": "completed",
                "documentation": _doc_status(None, [n]),
                "clinical_response": _clinical_response([n], None),
                "billing": "not_evaluated",
            },
            "source_ids": {
                "appointment_id": None,
                "encounter_id": None,
                "note_ids": [n["id"]],
                "billing_readiness_id": None,
            },
            "orphaned": True,
        })

    # Newest first; None sorts to the end deterministically.
    groups.sort(key=lambda g: g.get("visit_at") or "", reverse=True)

    await audit_success(
        user, "clinical.encounters.grouped_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={
            "schema_version": SCHEMA_VERSION,
            "group_count": len(groups),
            "source_counts": {
                "appointments": len(appointments),
                "encounters": len(encounters),
                "notes": len(notes),
            },
        },
    )

    return {"schema_version": SCHEMA_VERSION, "groups": groups}


# ----- /clinical/timeline/grouped -----------------------------------

@router.get("/{patient_id}/clinical/timeline/grouped")
async def list_grouped_timeline(
    patient_id: str,
    request: Request,
    kinds: Optional[str] = Query(default=None, description="csv of kinds to include (legacy alias for event_kinds)"),
    event_kinds: Optional[str] = Query(
        default=None,
        description="csv of TimelineEventKind slugs; unknown values dropped and echoed in filter_meta.ignored_slugs",
    ),
    sources: Optional[str] = Query(
        default=None,
        description="csv of TimelineSource slugs; unknown values dropped and echoed in filter_meta.ignored_slugs",
    ),
    provider_ids: Optional[str] = Query(
        default=None,
        description="csv of provider ids; ids the caller cannot see are dropped and echoed in filter_meta.ignored_provider_ids",
    ),
    episode_ids: Optional[str] = Query(
        default=None,
        description="csv of episode ids; ids not on this patient are dropped and echoed in filter_meta.ignored_episode_ids",
    ),
    date_window: Optional[str] = Query(default=None, description="allow-listed relative window (last_7d, last_30d, …)"),
    date_from: Optional[str] = Query(default=None, description="ISO date; overrides date_window"),
    date_to: Optional[str] = Query(default=None, description="ISO date; overrides date_window"),
    q: Optional[str] = Query(
        default=None, max_length=80,
        description="Transient free-text search — NEVER persisted server-side; matched against titles + provider names.",
    ),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Groups related timeline artefacts (appointment + encounter + note +
    initial-exam) into one event per visit. Non-visit-linked artefacts
    (imaging, outcomes, addenda, diagnosis changes, intake) are emitted
    as their own standalone events so nothing is dropped.

    Phase 3 Slice 2: adds a filter surface. All params are optional and
    a caller passing none receives the exact response the pre-Slice-2
    endpoint returned (schema_version 1.0). When any filter is supplied
    the response bumps to `TIMELINE_SCHEMA_VERSION` ('1.1') and includes
    a `filter_meta` object echoing what was applied and what was
    ignored (stale preset detection).
    """
    from datetime import datetime, timedelta, timezone

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)

    # Reuse grouped encounter logic for visit anchors.
    enc_grouped = await list_grouped_encounters(
        patient_id=patient_id, request=request, user=user, ctx=ctx,
    )
    visit_events = [
        {
            "kind": "visit",
            "visit_at": g["visit_at"],
            "title": g.get("appointment_type") or "Visit",
            "provider_id": g.get("provider_id"),
            "provider_name": g.get("provider_name"),
            "episode_id": g.get("episode_id"),
            "status": g["status"],
            "source_ids": g["source_ids"],
            "orphaned": g["orphaned"],
        }
        for g in enc_grouped["groups"]
    ]

    scope = scoped_filter({"patient_id": patient_id}, ctx, location_scoped=False)
    if scope.get("__deny__"):
        return {"schema_version": SCHEMA_VERSION, "events": []}

    non_visit_events: list[dict] = []
    async for d in db.clinical_initial_exams.find(scope, {"_id": 0}):
        non_visit_events.append({
            "kind": "initial_exam",
            "visit_at": d.get("date_of_service") or d.get("created_at"),
            "title": "Initial exam",
            "provider_id": d.get("provider_id"),
            "provider_name": d.get("provider_name"),
            "episode_id": d.get("episode_id"),
            "status": {"documentation": d.get("sign_status") or "draft"},
            "source_ids": {"initial_exam_id": d.get("id"), "encounter_id": d.get("encounter_id")},
            "orphaned": not d.get("encounter_id"),
        })
    async for d in db.clinical_treatment_plans.find(scope, {"_id": 0}):
        non_visit_events.append({
            "kind": "treatment_plan",
            "visit_at": d.get("created_at"),
            "title": d.get("plan_name") or "Treatment plan",
            "provider_id": d.get("responsible_provider_id"),
            "provider_name": d.get("provider_name"),
            "episode_id": d.get("episode_id"),
            "status": {"record_state": d.get("plan_status") or "active"},
            "source_ids": {"treatment_plan_id": d.get("id")},
            "orphaned": False,
        })
    async for d in db.clinical_media.find(scope, {"_id": 0}):
        non_visit_events.append({
            "kind": "clinical_media",
            "visit_at": d.get("created_at"),
            "title": d.get("kind") or "Imaging",
            "provider_id": d.get("uploaded_by"),
            "provider_name": d.get("uploaded_by_name"),
            "episode_id": d.get("episode_id"),
            "status": {"record_state": "active"},
            "source_ids": {"media_id": d.get("id")},
            "orphaned": False,
        })
    async for d in db.clinical_outcomes.find(scope, {"_id": 0}):
        non_visit_events.append({
            "kind": "outcome_entry",
            "visit_at": d.get("recorded_at") or d.get("created_at"),
            "title": d.get("measure_name") or "Outcome",
            "provider_id": d.get("recorded_by"),
            "provider_name": d.get("recorded_by_name"),
            "episode_id": d.get("episode_id"),
            "status": {"record_state": "active"},
            "source_ids": {"outcome_id": d.get("id")},
            "orphaned": False,
        })

    events = visit_events + non_visit_events
    total_before = len(events)

    # ---- Parse + validate filter inputs ----
    def _csv(v: Optional[str]) -> list[str]:
        return [x.strip() for x in (v or "").split(",") if x.strip()] if v else []

    requested_kinds = _csv(event_kinds) or _csv(kinds)  # legacy alias
    requested_sources = _csv(sources)
    requested_providers = _csv(provider_ids)
    requested_episodes = _csv(episode_ids)

    ignored_slugs: list[str] = []
    kinds_filter: set[str] = set()
    for k in requested_kinds:
        if k in _TIMELINE_KINDS:
            kinds_filter.add(k)
        else:
            ignored_slugs.append(k)
    sources_filter: set[str] = set()
    for s in requested_sources:
        if s in _TIMELINE_SOURCES:
            sources_filter.add(s)
        else:
            ignored_slugs.append(s)

    # Permission-aware provider filtering. Only tenant-visible providers
    # are honored; the rest fall into ignored_provider_ids (stale-preset
    # detection so the UI can prompt the user to update the preset).
    provider_filter: set[str] = set()
    ignored_provider_ids: list[str] = []
    if requested_providers:
        visible = set()
        async for p in db.users.find(
            {"tenant_id": ctx.tenant_id, "id": {"$in": requested_providers}},
            {"_id": 0, "id": 1},
        ):
            visible.add(p["id"])
        for pid in requested_providers:
            if pid in visible:
                provider_filter.add(pid)
            else:
                ignored_provider_ids.append(pid)

    # Episode filter — transient, patient-scoped. Drop any episode id
    # that doesn't belong to this patient (stale after episode delete).
    episode_filter: set[str] = set()
    ignored_episode_ids: list[str] = []
    if requested_episodes:
        visible_eps = set()
        async for ep in db.clinical_episode_cases.find(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id,
             "id": {"$in": requested_episodes}},
            {"_id": 0, "id": 1},
        ):
            visible_eps.add(ep["id"])
        for eid in requested_episodes:
            if eid in visible_eps:
                episode_filter.add(eid)
            else:
                ignored_episode_ids.append(eid)

    # Date window resolution.
    now = datetime.now(timezone.utc)
    resolved_from: Optional[str] = None
    resolved_to: Optional[str] = None
    if date_from:
        resolved_from = date_from
    if date_to:
        resolved_to = date_to
    date_window_applied: Optional[str] = None
    if date_window and date_window in _TIMELINE_DATE_WINDOWS:
        days = _TIMELINE_DATE_WINDOWS[date_window]
        date_window_applied = date_window
        if days > 0 and not (date_from or date_to):
            resolved_from = (now - timedelta(days=days)).date().isoformat()
    elif date_window and date_window not in _TIMELINE_DATE_WINDOWS:
        ignored_slugs.append(date_window)

    # ---- Source→kind mapping (a "source" is a data collection, a
    # "kind" is the timeline row's shape). Applying `sources` translates
    # into the kinds the row *carries*. Backward-compatible superset.
    SOURCE_TO_KIND = {
        "appointment": "visit",
        "encounter": "visit",
        "note": "visit",
        "initial_exam": "initial_exam",
        "reexam": "visit",  # emits under the visit row it was authored on
        "outcome": "outcome_entry",
        "media": "clinical_media",
    }
    source_kinds = {SOURCE_TO_KIND[s] for s in sources_filter} if sources_filter else None

    # ---- Free-text search — TRANSIENT ONLY. This value MUST NOT be
    # persisted in any saved preset (durable prefs disallow it via
    # Pydantic `extra="forbid"`). It's matched against title +
    # provider_name only; no PHI substrings surface through this path.
    q_lower = q.strip().lower() if q else None

    def _keep(e: dict) -> bool:
        if kinds_filter and e["kind"] not in kinds_filter:
            return False
        if source_kinds and e["kind"] not in source_kinds:
            return False
        if provider_filter and e.get("provider_id") not in provider_filter:
            return False
        if episode_filter and e.get("episode_id") not in episode_filter:
            return False
        if resolved_from and (not e.get("visit_at") or e["visit_at"] < resolved_from):
            return False
        if resolved_to and (not e.get("visit_at") or e["visit_at"] > f"{resolved_to}T23:59:59Z"):
            return False
        if q_lower:
            hay = " ".join(
                [str(e.get(k) or "") for k in ("title", "provider_name", "kind")]
            ).lower()
            if q_lower not in hay:
                return False
        return True

    any_filter_applied = bool(
        kinds_filter or sources_filter or provider_filter or episode_filter
        or resolved_from or resolved_to or q_lower or date_window_applied
    )
    # Also surface filter_meta whenever the caller *attempted* to filter
    # — even if every value ended up in `ignored_*`. This lets the UI
    # detect stale presets and prompt the user to repair them.
    filter_attempted = bool(
        any_filter_applied
        or requested_kinds or requested_sources or requested_providers
        or requested_episodes or date_window or date_from or date_to
    )
    filtered = [e for e in events if _keep(e)] if any_filter_applied else events
    filtered.sort(key=lambda e: e.get("visit_at") or "", reverse=True)

    await audit_success(
        user, "clinical.timeline.grouped_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={
            "schema_version": TIMELINE_SCHEMA_VERSION if filter_attempted else SCHEMA_VERSION,
            "event_count": len(filtered),
            "visit_count": len(visit_events),
            "filters_applied": bool(any_filter_applied),
        },
    )

    if not filter_attempted:
        # Pre-Slice-2 backward-compatible response shape.
        return {"schema_version": SCHEMA_VERSION, "events": filtered}

    return {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "events": filtered,
        "filter_meta": {
            "applied": {
                "event_kinds": sorted(kinds_filter),
                "sources": sorted(sources_filter),
                "provider_ids": sorted(provider_filter),
                "episode_ids": sorted(episode_filter),
                "date_window": date_window_applied,
                "date_from": resolved_from,
                "date_to": resolved_to,
                "q_present": bool(q_lower),
            },
            "ignored_slugs": sorted(set(ignored_slugs)),
            "ignored_provider_ids": ignored_provider_ids,
            "ignored_episode_ids": ignored_episode_ids,
            "total_before_filter": total_before,
            "total_after_filter": len(filtered),
        },
    }


# ----- /clinical/billing-readiness/aggregate -----------------------

@router.get("/{patient_id}/clinical/billing-readiness/aggregate")
async def get_billing_readiness_aggregate(
    patient_id: str,
    request: Request,
    # Only roles that can already see billing readiness may pull the
    # chart-wide count. Staff explicitly excluded — this mirrors the
    # per-encounter endpoint's permission model in billing_readiness_router.
    user: dict = Depends(require_role("admin", "doctor", "biller")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Chart-wide billing-readiness aggregate.

    Reuses the same tenant-scoped join keys as `encounters/grouped` —
    never duplicates the readiness rule engine. Counts only rows the
    caller is permitted to view. Free-form `detail` strings from
    ReadinessCheck are NEVER returned; only allow-listed messages via
    `_FAIL_KEYS_PRIORITY` / `_WARN_KEYS_PRIORITY`.
    """
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)

    scope = scoped_filter({"patient_id": patient_id}, ctx, location_scoped=False)
    if scope.get("__deny__"):
        return {
            "schema_version": SCHEMA_VERSION,
            "warning_count": 0,
            "blocked_count": 0,
            "top_message": None,
            "status": "ready",
        }

    # Pull encounter ids first so we can associate readiness rows and
    # surface orphans (readiness that references an encounter the caller
    # can no longer read).
    encounters = [d async for d in db.clinical_encounters.find(scope, {"_id": 0, "id": 1})]
    visible_enc_ids = {e["id"] for e in encounters}

    readiness_rows: list[dict] = [
        d async for d in db.clinical_billing_readiness.find(scope, {"_id": 0})
    ]

    warning_count = 0
    blocked_count = 0
    # Track first-seen (highest priority) allow-listed key per severity band.
    top_fail_key: Optional[str] = None
    top_warn_key: Optional[str] = None
    orphan_count = 0

    for row in readiness_rows:
        # Orphaned readiness: encounter no longer visible / linked.
        if row.get("encounter_id") and row["encounter_id"] not in visible_enc_ids:
            orphan_count += 1
        checks = row.get("checks") or []
        row_has_fail = False
        row_has_warn = False
        for c in checks:
            if c.get("passed"):
                continue
            sev = c.get("severity")
            key = c.get("key")
            if sev == "fail":
                row_has_fail = True
                if key in _FAIL_KEYS_PRIORITY:
                    # Priority: keep the first key we see in the FAIL
                    # dict iteration order (Python 3.7+ preserves insert
                    # order — that IS the priority).
                    if top_fail_key is None:
                        top_fail_key = key
                    else:
                        # Replace only if the current key has a lower
                        # (i.e. later) position in the priority list.
                        keys = list(_FAIL_KEYS_PRIORITY.keys())
                        if keys.index(key) < keys.index(top_fail_key):
                            top_fail_key = key
            elif sev == "warn":
                row_has_warn = True
                if key in _WARN_KEYS_PRIORITY:
                    if top_warn_key is None:
                        top_warn_key = key
                    else:
                        keys = list(_WARN_KEYS_PRIORITY.keys())
                        if keys.index(key) < keys.index(top_warn_key):
                            top_warn_key = key
        if row_has_fail:
            blocked_count += 1
        elif row_has_warn:
            warning_count += 1

    if blocked_count > 0:
        overall_status = "blocked"
    elif warning_count > 0:
        overall_status = "warning"
    else:
        overall_status = "ready"

    if top_fail_key:
        top_message = _FAIL_KEYS_PRIORITY[top_fail_key]
    elif top_warn_key:
        top_message = _WARN_KEYS_PRIORITY[top_warn_key]
    else:
        top_message = None

    await audit_success(
        user, "clinical.billing_readiness.aggregate_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=False,
        metadata={
            "schema_version": SCHEMA_VERSION,
            "warning_count": warning_count,
            "blocked_count": blocked_count,
            "orphan_count": orphan_count,
            "status": overall_status,
        },
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "warning_count": warning_count,
        "blocked_count": blocked_count,
        "top_message": top_message,
        "status": overall_status,
    }
