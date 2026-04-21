"""Clinical Phase 7 — Media, Outcomes, expanded Care Timeline tests."""
from __future__ import annotations

import io
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")

# 1x1 PNG
PNG_BYTES = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D,
    0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
    0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53, 0xDE, 0x00, 0x00, 0x00,
    0x0C, 0x49, 0x44, 0x41, 0x54, 0x08, 0x99, 0x63, 0x60, 0x00, 0x00, 0x00,
    0x04, 0x00, 0x01, 0x27, 0x34, 0x27, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x49,
    0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
])
# Tiny PDF
PDF_BYTES = (
    b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 10 10]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000055 00000 n \n0000000103 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n157\n%%EOF\n"
)


def _login(email, password, *, reauth=True):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    s.headers["Authorization"] = f"Bearer {r.cookies.get('access_token')}"
    if reauth:
        r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
        assert r.status_code == 200, r.text
        tok = r.cookies.get("reauth_token")
        if tok:
            s.headers["x-reauth-token"] = tok
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(*GROUP_ADMIN)


@pytest.fixture(scope="module")
def default_admin():
    return _login(*DEFAULT_ADMIN)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _new_patient(s):
    r = s.post(f"{API}/patients", json={
        "first_name": "Media",
        "last_name": f"P{uuid.uuid4().hex[:6]}",
        "email": f"media_{uuid.uuid4().hex[:10]}@example.com",
        "phone": "+1-555-0700",
        "date_of_birth": "1978-01-01",
        "gender": "female",
    }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def _make_episode(s, patient_id):
    r = s.post(f"{API}/patients/{patient_id}/clinical/episodes",
               json={"title": "LBP", "case_type": "injury_episode"}, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def _make_plan(s, patient_id, episode_id):
    r = s.post(f"{API}/patients/{patient_id}/clinical/treatment-plans", json={
        "episode_id": episode_id, "title": "Plan",
        "goals": [{"description": "pain", "measure_type": "pain_scale"}],
    }, timeout=15)
    assert r.status_code == 201
    return r.json()


def _upload(s, patient_id, *, content=PNG_BYTES, mime="image/png",
            filename="xray.png", **form):
    data = {"category": "xray"}
    data.update(form)
    files = {"file": (filename, io.BytesIO(content), mime)}
    return s.post(
        f"{API}/patients/{patient_id}/clinical/media",
        data=data, files=files, timeout=30,
    )


# ---------------------------------------------------------------------------
# Media tests
# ---------------------------------------------------------------------------
def test_media_upload_and_download(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    r = _upload(
        admin, p["id"],
        body_region="lumbar", source="in_clinic",
        study_date="2026-02-10", impression_findings="L4/5 disc space narrowing",
        episode_id=ep["id"],
    )
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["category"] == "xray"
    assert m["mime_type"] == "image/png"
    assert m["body_region"] == "lumbar"
    assert m["episode_id"] == ep["id"]
    assert m["size_bytes"] == len(PNG_BYTES)

    # Download
    r = admin.get(f"{API}/patients/{p['id']}/clinical/media/{m['id']}/download", timeout=10)
    assert r.status_code == 200
    assert r.content == PNG_BYTES


def test_media_mime_mismatch_rejected(admin):
    p = _new_patient(admin)
    # Declare image/png but send PDF bytes
    r = _upload(
        admin, p["id"],
        content=PDF_BYTES, mime="image/png", filename="x.png",
    )
    assert r.status_code == 400, r.text
    assert "sniffed" in r.text.lower() or "match" in r.text.lower()


def test_media_cross_patient_linkage_rejected(admin):
    p1 = _new_patient(admin)
    p2 = _new_patient(admin)
    ep2 = _make_episode(admin, p2["id"])
    r = _upload(admin, p1["id"], episode_id=ep2["id"])
    assert r.status_code == 400


def test_media_patch_and_soft_delete(admin):
    p = _new_patient(admin)
    m = _upload(admin, p["id"]).json()
    # Patch metadata
    r = admin.patch(f"{API}/patients/{p['id']}/clinical/media/{m['id']}", json={
        "body_region": "thoracic",
        "impression_findings": "updated",
    }, timeout=10)
    assert r.status_code == 200
    assert r.json()["body_region"] == "thoracic"

    # Soft delete
    r = admin.delete(f"{API}/patients/{p['id']}/clinical/media/{m['id']}", timeout=10)
    assert r.status_code == 204
    # Default list hides soft-deleted
    rows = admin.get(f"{API}/patients/{p['id']}/clinical/media", timeout=10).json()
    assert not any(x["id"] == m["id"] for x in rows)
    # GET returns 404 on soft-deleted single
    r = admin.get(f"{API}/patients/{p['id']}/clinical/media/{m['id']}", timeout=10)
    # We allow GET of soft-deleted rows for audit purposes (deleted_at surfaced),
    # but download is blocked.
    assert r.status_code == 200
    r = admin.get(f"{API}/patients/{p['id']}/clinical/media/{m['id']}/download", timeout=10)
    assert r.status_code == 404


def test_media_permission_reauth_required(admin):
    p = _new_patient(admin)
    no_reauth = _login(*GROUP_ADMIN, reauth=False)
    r = _upload(no_reauth, p["id"])
    assert r.status_code == 401
    assert "re-auth" in r.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Outcomes tests
# ---------------------------------------------------------------------------
def test_outcome_create_list_and_trends(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    # 3 NDI entries over time
    for i, (date, score) in enumerate([
        ("2026-01-01", 40), ("2026-01-20", 28), ("2026-02-10", 15),
    ]):
        r = admin.post(f"{API}/patients/{p['id']}/clinical/outcomes", json={
            "measure_type": "ndi", "label": "NDI", "score": score, "max_score": 100,
            "captured_at": f"{date}T10:00:00Z", "episode_id": ep["id"],
        }, timeout=10)
        assert r.status_code == 201, r.text

    # List
    r = admin.get(f"{API}/patients/{p['id']}/clinical/outcomes", timeout=10)
    assert r.status_code == 200
    assert len(r.json()) == 3

    # Trends
    r = admin.get(f"{API}/patients/{p['id']}/clinical/outcomes/trends", timeout=10)
    assert r.status_code == 200
    trends = r.json()["trends"]
    assert len(trends) == 1
    s = trends[0]["series"]
    assert len(s) == 3
    # Ascending by captured_at for chartability
    assert [e["score"] for e in s] == [40, 28, 15]

    # Summary outcomes_snapshot latest-score
    summ = admin.get(f"{API}/patients/{p['id']}/clinical/summary", timeout=10).json()
    assert summ["outcomes"]["total"] == 3
    snap = next((x for x in summ["outcomes_snapshot"] if x["measure_type"] == "ndi"), None)
    assert snap is not None
    assert snap["latest_score"] == 15


def test_outcome_trends_filter_by_episode(admin):
    p = _new_patient(admin)
    ep1 = _make_episode(admin, p["id"])
    ep2_title = admin.post(f"{API}/patients/{p['id']}/clinical/episodes", json={
        "title": "Other", "case_type": "maintenance",
    }, timeout=10)
    ep2 = ep2_title.json()
    admin.post(f"{API}/patients/{p['id']}/clinical/outcomes", json={
        "measure_type": "pain_vas", "label": "VAS", "score": 6, "max_score": 10,
        "episode_id": ep1["id"],
    }, timeout=10)
    admin.post(f"{API}/patients/{p['id']}/clinical/outcomes", json={
        "measure_type": "pain_vas", "label": "VAS", "score": 2, "max_score": 10,
        "episode_id": ep2["id"],
    }, timeout=10)
    r = admin.get(
        f"{API}/patients/{p['id']}/clinical/outcomes/trends",
        params={"episode_id": ep1["id"]}, timeout=10,
    )
    assert r.status_code == 200
    series = r.json()["trends"][0]["series"]
    assert len(series) == 1 and series[0]["score"] == 6


def test_outcome_reexam_source_rejected_on_public_create(admin):
    p = _new_patient(admin)
    r = admin.post(f"{API}/patients/{p['id']}/clinical/outcomes", json={
        "measure_type": "ndi", "label": "NDI", "score": 10, "source": "reexam",
    }, timeout=10)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Re-Exam sign auto-emit tests (reuses Phase 6 setup)
# ---------------------------------------------------------------------------
def _book(s, patient_id, provider_id, reason="Re-evaluation"):
    for _ in range(5):
        base = datetime.now(timezone.utc) + timedelta(days=random.randint(7, 60))
        start = base.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=random.randint(0, 8), minutes=random.choice([0, 15, 30, 45]),
        )
        end = start + timedelta(minutes=30)
        r = s.post(f"{API}/appointments", json={
            "patient_id": patient_id, "provider_id": provider_id,
            "start_time": start.isoformat(), "end_time": end.isoformat(),
            "reason": reason,
        }, timeout=15)
        if r.status_code == 201:
            return r.json()
    raise AssertionError("could not book")


def _provider(s):
    return s.get(f"{API}/auth/providers", timeout=10).json()[0]["id"]


def test_reexam_sign_emits_outcome_entries(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    plan = _make_plan(admin, p["id"], ep["id"])

    prov = _provider(admin)
    appt = _book(admin, p["id"], prov)
    enc = admin.post(f"{API}/appointments/{appt['id']}/clinical/encounters",
                     json={"encounter_type": "re_evaluation"}, timeout=15).json()["encounter"]
    admin.patch(f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
                json={"episode_id": ep["id"]}, timeout=15)

    rx = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams",
                    json={"encounter_id": enc["id"]}, timeout=15).json()
    admin.patch(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}", json={
        "outcome_updates": [
            {"measure_type": "ndi", "label": "NDI", "score": 22, "max_score": 100},
            {"measure_type": "pain_vas", "label": "VAS", "score": 4, "max_score": 10},
        ],
        "recommendation_decision": "continue",
        "recommendation_reason": "stay the course",
    }, timeout=15)
    admin.post(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}/sign",
               json={}, timeout=10)

    # Two standalone outcome entries appear, source=reexam, linked_reexam_id set
    rows = admin.get(f"{API}/patients/{p['id']}/clinical/outcomes", timeout=10).json()
    reexam_rows = [r for r in rows if r["source"] == "reexam"]
    assert len(reexam_rows) == 2
    for r in reexam_rows:
        assert r["linked_reexam_id"] == rx["id"]
        assert r["linked_treatment_plan_id"] == plan["id"]

    # PATCH/DELETE on reexam-sourced entries → 409
    r = admin.patch(f"{API}/patients/{p['id']}/clinical/outcomes/{reexam_rows[0]['id']}",
                    json={"score": 99}, timeout=10)
    assert r.status_code == 409
    r = admin.delete(f"{API}/patients/{p['id']}/clinical/outcomes/{reexam_rows[0]['id']}",
                     timeout=10)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Care timeline + regression
# ---------------------------------------------------------------------------
def test_care_timeline_includes_phase7_kinds(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    _upload(admin, p["id"], episode_id=ep["id"], study_date="2026-02-01T00:00:00Z")
    admin.post(f"{API}/patients/{p['id']}/clinical/outcomes", json={
        "measure_type": "pain_vas", "label": "VAS", "score": 3, "max_score": 10,
        "episode_id": ep["id"],
    }, timeout=10)
    # Create + resolve a diagnosis to emit diagnosis change events
    dx = admin.post(f"{API}/patients/{p['id']}/clinical/diagnoses", json={
        "icd10_code": "M54.5", "label": "LBP", "body_region": "lumbar",
        "episode_id": ep["id"], "is_primary": True,
    }, timeout=10).json()
    _ = dx
    r = admin.get(f"{API}/patients/{p['id']}/clinical/care-timeline", timeout=10)
    assert r.status_code == 200
    kinds = [e["kind"] for e in r.json()["entries"]]
    assert "clinical_media" in kinds
    assert "outcome_entry" in kinds
    assert "diagnosis_change" in kinds
    # date-desc ordering
    iso = [e.get("date_of_service") or "" for e in r.json()["entries"]]
    assert iso == sorted(iso, reverse=True)


def test_tenant_isolation_and_reauth(admin, default_admin):
    p = _new_patient(admin)
    m = _upload(admin, p["id"]).json()
    r = default_admin.get(f"{API}/patients/{p['id']}/clinical/media/{m['id']}", timeout=10)
    assert r.status_code == 404
    # Outcome create without reauth
    no_reauth = _login(*GROUP_ADMIN, reauth=False)
    r = no_reauth.post(f"{API}/patients/{p['id']}/clinical/outcomes", json={
        "measure_type": "pain_vas", "label": "VAS", "score": 3,
    }, timeout=10)
    assert r.status_code == 401
