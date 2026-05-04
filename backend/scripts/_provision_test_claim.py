"""One-off helper to provision a quick-submitted CHC sandbox claim and print the id."""
from __future__ import annotations
import asyncio
import os
import sys
import requests
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from core.tenancy import reset_router_for_tests

API = (os.environ["REACT_APP_BACKEND_URL"]).rstrip("/") + "/api"


async def find():
    reset_router_for_tests()
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    u = await db.users.find_one({"email": "doctor@ccms.app"}, {"_id": 0, "tenant_id": 1})
    n = await db.clinical_follow_up_notes.find_one(
        {"tenant_id": u["tenant_id"]},
        {"_id": 0, "id": 1, "status": 1},
    )
    if n and n.get("status") == "signed":
        await db.clinical_follow_up_notes.update_one({"id": n["id"]}, {"$set": {"status": "draft"}})
    chc = await db.billing_payers.find_one(
        {"tenant_id": u["tenant_id"], "clearinghouse_route": "change_healthcare"},
        {"_id": 0, "id": 1},
    )
    c.close()
    return n, chc


note, chc = asyncio.run(find())
assert note and chc, "missing seed data"

s = requests.Session()
s.post(f"{API}/auth/login", json={"email": "doctor@ccms.app", "password": "Doctor@ComplianceClinic1"})
r = s.post(
    f"{API}/scribe/encounters/follow_up/{note['id']}/send-to-claim",
    json={"cpt": [{"code": "98941", "units": 1}],
          "icd": [{"code": "M54.5", "is_primary": True}],
          "payer_id": chc["id"]},
)
claim_id = r.json()["claim_id"]

a = requests.Session()
a.post(f"{API}/auth/login", json={"email": "admin@ccms.app", "password": "Admin@ComplianceClinic1"})
r2 = a.post(f"{API}/billing/claims/{claim_id}/quick-submit", json={})
print(f"CLAIM_ID={claim_id}")
print(f"QUICK_SUBMIT={r2.status_code}")
print(f"BODY={r2.text[:200]}")
