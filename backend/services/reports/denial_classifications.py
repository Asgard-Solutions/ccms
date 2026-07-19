"""
services/reports/denial_classifications.py — tenant-managed CARC code
→ denial-category dictionary.

The heat map classifier ships with a pre-baked CARC map
(CO-11 → Eligibility, CO-16 → Coding, …). When a payer returns a code
that isn't baked in, the cell lands in `Uncategorised`. This module
lets operators teach the system on the fly by registering tenant-
scoped `(code → category)` mappings. Tenant overrides win over the
built-in map.

Collection: `denial_code_classifications`
Fields:    id, tenant_id, code, category, created_by, created_at,
           source ("tenant" — only tenant rows live here; built-ins
           are in Python).

Precedence (ascending): built-in → tenant.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.tenancy import TenantContext, require_tenant, tenant_db
from services.authz.policy import require_permission


router = APIRouter(prefix="/reports", tags=["reports", "denials"])


# ---------------------------------------------------------------------------
# Built-in CARC → category map (kept in sync with builtin_analytics.py).
# ---------------------------------------------------------------------------
BUILTIN_DENIAL_MAP: dict[str, str] = {
    "CO-11":  "Eligibility / coverage",
    "CO-27":  "Eligibility / coverage",
    "CO-29":  "Timely filing",
    "CO-197": "Authorization",
    "CO-198": "Authorization",
    "CO-97":  "Bundling / CCI",
    "CO-B15": "Bundling / CCI",
    "CO-16":  "Coding / documentation",
    "CO-50":  "Medical necessity",
    "CO-22":  "COB / primary payer",
    "PR-1":   "Patient deductible",
    "PR-2":   "Patient coinsurance",
    "PR-3":   "Patient copay",
    "PR-45":  "Allowed amount reduction",
    "OA-23":  "Other adjudication",
    "PI-45":  "Payer contract",
}


_PREFIX_BUCKETS = {
    "CO": "Contractual (CO)",
    "PR": "Patient responsibility (PR)",
    "OA": "Other adjustments (OA)",
    "PI": "Payer initiated (PI)",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_code(code: str | None) -> str:
    return (code or "").strip().upper()


async def load_tenant_overrides(db: Any, tenant_id: str) -> dict[str, str]:
    """Fetch every `(code → category)` override registered for the
    tenant into a plain dict the classifier can merge over built-ins."""
    out: dict[str, str] = {}
    async for row in db.denial_code_classifications.find(
        {"tenant_id": tenant_id}, {"_id": 0, "code": 1, "category": 1},
    ):
        out[_norm_code(row["code"])] = row["category"]
    return out


def classify_denial_code(
    code: str | None,
    tenant_overrides: dict[str, str] | None = None,
) -> str:
    """Resolve a CARC code to a human category.

    Lookup order: tenant override → built-in exact match → prefix
    bucket (`CO-*`, `PR-*`, …) → `Uncategorised`.
    """
    up = _norm_code(code)
    if not up:
        return "Uncategorised"
    if tenant_overrides and up in tenant_overrides:
        return tenant_overrides[up]
    if up in BUILTIN_DENIAL_MAP:
        return BUILTIN_DENIAL_MAP[up]
    for prefix, bucket in _PREFIX_BUCKETS.items():
        if up.startswith(prefix):
            return bucket
    return "Uncategorised"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
_CODE_PATTERN = r"^[A-Za-z0-9\-_.]{1,16}$"


class DenialClassificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=_CODE_PATTERN, min_length=1, max_length=16)
    category: str = Field(min_length=1, max_length=80)


class DenialClassificationPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    code: str
    category: str
    source: str = "tenant"
    created_by: str | None = None
    created_at: str


class DenialClassificationCatalogResponse(BaseModel):
    """UI bootstrap — returns the effective classifier the frontend
    should reason about when offering "Add to classifier" affordances.
    """
    builtins: dict[str, str]
    tenant_overrides: list[DenialClassificationPublic]
    known_categories: list[str]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/denial-classifications",
    response_model=DenialClassificationCatalogResponse,
)
async def list_denial_classifications(
    user: dict = Depends(require_permission("reporting", "read_financial")),
    ctx: TenantContext = Depends(require_tenant),
):
    """List tenant overrides + built-in map + de-duplicated category
    vocabulary (for the category <select> in the Add dialog)."""
    db = tenant_db(ctx.tenant_id)
    overrides: list[dict] = []
    async for row in db.denial_code_classifications.find(
        {"tenant_id": ctx.tenant_id}, {"_id": 0},
    ).sort("created_at", -1):
        overrides.append(row)
    known = sorted({
        *BUILTIN_DENIAL_MAP.values(),
        *(r["category"] for r in overrides),
    })
    return {
        "builtins": BUILTIN_DENIAL_MAP,
        "tenant_overrides": overrides,
        "known_categories": known,
    }


@router.post(
    "/denial-classifications",
    response_model=DenialClassificationPublic,
    status_code=status.HTTP_201_CREATED,
)
async def add_denial_classification(
    payload: DenialClassificationCreate,
    request: Request,
    user: dict = Depends(
        require_permission("reporting", "read_financial"),
    ),
    ctx: TenantContext = Depends(require_tenant),
):
    """Create or replace a tenant-scoped classification for `code`.

    The endpoint is idempotent — calling it twice with the same code
    updates the existing row's category + `created_by`. Any user who
    can read the financial report can teach the system new codes;
    every upsert/delete lands in the audit log with code + category.
    """
    db = tenant_db(ctx.tenant_id)
    code = _norm_code(payload.code)
    category = payload.category.strip()
    if not code:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Denial code is required",
        )
    if not category:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Category is required",
        )
    existing = await db.denial_code_classifications.find_one(
        {"tenant_id": ctx.tenant_id, "code": code}, {"_id": 0},
    )
    now = _now_iso()
    if existing:
        await db.denial_code_classifications.update_one(
            {"tenant_id": ctx.tenant_id, "id": existing["id"]},
            {"$set": {
                "category": category,
                "updated_at": now,
                "updated_by": user["id"],
            }},
        )
        existing["category"] = category
        doc = existing
    else:
        doc = {
            "id": str(uuid.uuid4()),
            "tenant_id": ctx.tenant_id,
            "code": code,
            "category": category,
            "created_by": user["id"],
            "created_at": now,
            "source": "tenant",
        }
        await db.denial_code_classifications.insert_one(doc)

    await audit_success(
        user, "reports.denial_classification.upserted", request,
        entity_type="denial_code_classification", entity_id=doc["id"],
        metadata={"code": code, "category": category,
                  "replaced_existing": bool(existing)},
    )
    return {k: v for k, v in doc.items() if k != "_id"}


@router.delete(
    "/denial-classifications/{classification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_denial_classification(
    classification_id: str,
    request: Request,
    user: dict = Depends(
        require_permission("reporting", "read_financial"),
    ),
    ctx: TenantContext = Depends(require_tenant),
):
    db = tenant_db(ctx.tenant_id)
    row = await db.denial_code_classifications.find_one(
        {"id": classification_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not row:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Classification not found",
        )
    await db.denial_code_classifications.delete_one(
        {"id": classification_id, "tenant_id": ctx.tenant_id},
    )
    await audit_success(
        user, "reports.denial_classification.removed", request,
        entity_type="denial_code_classification", entity_id=classification_id,
        metadata={"code": row["code"], "category": row["category"]},
    )
