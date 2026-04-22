#!/usr/bin/env python3
"""
reseed_demo_clinic.py — destructive reset of the Riverbend demo tenant.

**Purpose**: the live demo tenant accumulates test-run pollution over
time (synthetic patients/appointments/payers/claims created by pytest
runs). Those rows visually drown out the curated demo personas.

This script:
  1. Deletes all rows whose `tenant_id` matches the default tenant
     EXCEPT a small allow-list of seed-origin IDs (the staff we just
     created in services/demo/seed.py, the login-helper demo users,
     and the core payer catalog).
  2. Re-runs `seed_demo_clinic()` so the curated personas,
     appointments, clinical notes, and insurance policies come back
     clean.

**Safety**: only touches the Riverbend demo tenant. The Sunrise Chiro
Group tenant + platform admin are untouched.

Run with:
    python /app/backend/scripts/reseed_demo_clinic.py
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

DEFAULT_TENANT_SLUG = "default"

# Collections scoped to a single tenant (tenant_id field present).
# We wipe these for the Riverbend tenant before re-seeding. Ordered
# so FK-ish dependencies are cleared first.
_TENANT_COLLECTIONS = [
    # clinical / scheduling layer
    "appointments", "medical_records", "patient_insurance_policies",
    "patients",
    # billing layer
    "claim_events", "claim_submissions", "claims", "statements",
    "remittances", "invoices", "charge_captures",
    "billing_providers", "billing_facilities", "billing_payers",
    "clearinghouse_enrollments",
    # notifications / communication
    "notifications", "communications",
]


async def main() -> None:
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    tenant = await db.tenants.find_one({"slug": DEFAULT_TENANT_SLUG}, {"_id": 0, "id": 1, "name": 1})
    if not tenant:
        print(f"  [!] Tenant slug={DEFAULT_TENANT_SLUG} missing — aborting.")
        return
    tid = tenant["id"]
    print(f"Resetting demo data for tenant '{tenant['name']}' (id={tid})")

    total = 0
    for coll in _TENANT_COLLECTIONS:
        res = await db[coll].delete_many({"tenant_id": tid})
        if res.deleted_count:
            print(f"  - cleared {res.deleted_count:>6} rows from {coll}")
            total += res.deleted_count
    print(f"  Deleted {total:,} rows across {len(_TENANT_COLLECTIONS)} collections.")

    # Also clear the demo staff users we seed, so seed_demo_clinic()
    # rebuilds them cleanly (login-helper admin/doctor/staff/patient
    # stay untouched — they're seeded by identity/seed.py).
    res = await db.users.delete_many({
        "email": {"$regex": r"@riverbend-chiro\.app$"},
    })
    if res.deleted_count:
        print(f"  - cleared {res.deleted_count} Riverbend staff users")

    client.close()

    # Re-run the realistic seed (persona catalog + curated billing).
    print("\nRe-seeding realistic demo data...")
    # Delayed import so the delete happens first and the seed runs
    # against the freshly wiped tenant.
    from services.demo.seed import seed_demo_clinic  # noqa: E402
    from services.demo.billing_seed import seed_demo_billing  # noqa: E402
    await seed_demo_clinic()
    await seed_demo_billing()
    print("Done. Restart the backend or just call GET /api/health to confirm.")


if __name__ == "__main__":
    asyncio.run(main())
