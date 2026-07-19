"""
Reorder appointment-types endpoint.
"""
from __future__ import annotations

import os
import uuid

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token") or r.json().get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    return s


def _mk_type(s: requests.Session, name: str) -> dict:
    r = s.post(f"{API}/appointment-types", json={
        "name": name,
        "default_duration_minutes": 30,
    }, timeout=10)
    assert r.status_code == 201, r.text
    return r.json()


def test_reorder_persists_new_sort_order():
    s = _login(*DEFAULT_ADMIN)
    suffix = uuid.uuid4().hex[:6]
    a = _mk_type(s, f"Reorder A {suffix}")
    b = _mk_type(s, f"Reorder B {suffix}")
    c = _mk_type(s, f"Reorder C {suffix}")
    created = [a["id"], b["id"], c["id"]]
    try:
        # Reorder C → A → B
        desired = [c["id"], a["id"], b["id"]]
        r = s.post(f"{API}/appointment-types/reorder",
                   json={"ordered_ids": desired}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert r.json()["reordered"] == 3

        # Fetch back — the three types should appear in the requested order
        rows = s.get(f"{API}/appointment-types", timeout=10).json()
        positions = {}
        for row in rows:
            if row["id"] in created:
                positions[row["id"]] = row["sort_order"]
        assert positions[c["id"]] < positions[a["id"]] < positions[b["id"]]
    finally:
        for tid in created:
            s.delete(f"{API}/appointment-types/{tid}", timeout=5)


def test_reorder_ignores_foreign_ids():
    s = _login(*DEFAULT_ADMIN)
    suffix = uuid.uuid4().hex[:6]
    a = _mk_type(s, f"Ignore A {suffix}")
    try:
        r = s.post(f"{API}/appointment-types/reorder", json={
            "ordered_ids": [a["id"], "bogus-id-not-exist"],
        }, timeout=10)
        assert r.status_code == 200, r.text
        # Only a was applied
        assert r.json()["reordered"] == 1
    finally:
        s.delete(f"{API}/appointment-types/{a['id']}", timeout=5)


def test_reorder_requires_admin():
    r = requests.post(f"{API}/appointment-types/reorder",
                      json={"ordered_ids": ["x"]}, timeout=10)
    assert r.status_code in (401, 403)


def test_reorder_rejects_empty_list():
    s = _login(*DEFAULT_ADMIN)
    r = s.post(f"{API}/appointment-types/reorder", json={"ordered_ids": []}, timeout=10)
    assert r.status_code == 422  # Pydantic min_length=1
