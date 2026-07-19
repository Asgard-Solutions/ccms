"""
Regression: legacy `Payment.status="completed"` rows used to crash
GET /api/billing/payments because the Pydantic `PaymentPublic`
model's Literal enum didn't include "completed". Now the model
coerces the legacy alias to "captured" on read.
"""
from __future__ import annotations

from services.billing.models import PaymentPublic


def _sample(status: str) -> dict:
    return {
        "id": "pay-1",
        "tenant_id": "t1",
        "patient_id": "p1",
        "method": "cash",
        "status": status,
        "amount_cents": 1000,
        "currency": "USD",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def test_payment_public_accepts_modern_statuses():
    for s in ("pending", "authorized", "captured", "settled",
              "refunded", "partially_refunded", "failed", "void"):
        m = PaymentPublic.model_validate(_sample(s))
        assert m.status == s


def test_payment_public_coerces_legacy_completed_to_captured():
    m = PaymentPublic.model_validate(_sample("completed"))
    assert m.status == "captured"


def test_payment_public_rejects_garbage_status():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PaymentPublic.model_validate(_sample("garbage-status"))
