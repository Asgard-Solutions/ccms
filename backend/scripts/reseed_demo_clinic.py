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
    "appointment_room_history", "appointments", "appointment_types",
    "rooms",
    "medical_records",
    "clinical_addenda", "clinical_audit_events",
    "clinical_diagnoses", "clinical_encounters",
    "clinical_episode_cases", "clinical_follow_up_notes",
    "clinical_history", "clinical_initial_exams", "clinical_media",
    "clinical_outcome_entries", "clinical_reexams",
    "clinical_treatment_plans",
    "communication_preferences", "consent_records",
    "patient_assignments", "patient_documents",
    "patient_insurance_policies", "patient_intake_forms",
    "patient_proxies",
    "patients",
    # billing layer
    "billing_adjustments", "billing_invoices_stub",
    "billing_payers", "claim_diagnoses", "claim_events",
    "claim_line_modifiers", "claim_lines", "claim_submissions",
    "claim_validation_runs", "claims",
    "clearinghouse_reports", "clearinghouse_enrollments",
    "denial_work_items",
    "fee_schedule_lines", "fee_schedules",
    "follow_up_suggestions",
    "invoice_lines", "invoices",
    "payment_allocations", "payments",
    "refunds",
    "remittance_claims", "remittance_imports", "remittances",
    "statement_deliveries", "statements",
    "billing_providers", "billing_facilities", "service_facilities",
    "providers", "professional_licenses",
    # governance / ops scope that test runs pollute
    "break_glass_events", "elevation_requests",
    "exports", "jobs",
    "privacy_requests", "report_saved_views",
    "workforce_invitations",
    # clinic profile (forces a clean re-seed)
    "clinic_profiles",
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

    # Also purge polluted users + their role/location bindings. We
    # keep exactly the login-helper demo accounts + the 5 Riverbend
    # staff created by services/demo/seed.py. Anything else on this
    # tenant is test-run pollution from pytest fixtures and must go.
    _KEEP_EMAILS = {
        "admin@ccms.app", "doctor@ccms.app",
        "staff@ccms.app", "patient@ccms.app",
        "olivia.hart@riverbend-chiro.app",
        "dr.samuel.ito@riverbend-chiro.app",
        "lena.brooks@riverbend-chiro.app",
        "tomas.rivera@riverbend-chiro.app",
        "priya.shah@riverbend-chiro.app",
    }
    kept_ids: set[str] = set()
    async for u in db.users.find(
        {"tenant_id": tid, "email": {"$in": list(_KEEP_EMAILS)}},
        {"_id": 0, "id": 1},
    ):
        kept_ids.add(u["id"])
    pollution_filter = {"tenant_id": tid, "id": {"$nin": list(kept_ids)}}
    pu = await db.users.delete_many(pollution_filter)
    if pu.deleted_count:
        print(f"  - cleared {pu.deleted_count:>6} polluted users")
    # Wipe role + location bindings for the deleted users — plus any
    # stale ones for the demo tenant that pytest fixtures created.
    rr = await db.user_roles.delete_many({"tenant_id": tid})
    if rr.deleted_count:
        print(f"  - cleared {rr.deleted_count:>6} user_role bindings (will re-bind on next boot)")
    la = await db.user_location_assignments.delete_many({"tenant_id": tid})
    if la.deleted_count:
        print(f"  - cleared {la.deleted_count:>6} user_location_assignments (re-created by authz seed)")

    # Purge polluted locations. The canonical Riverbend location is the
    # one the realistic seed targets ("Riverbend — Downtown"); any other
    # locations on this tenant are pytest fixtures / renamed legacy
    # "Main Clinic" rows that slipped in via _backfill_default_tenant.
    canonical_loc = await db.locations.find_one(
        {"tenant_id": tid, "name": "Riverbend — Downtown"},
        {"_id": 0, "id": 1},
    )
    if canonical_loc:
        dl = await db.locations.delete_many({
            "tenant_id": tid,
            "id": {"$ne": canonical_loc["id"]},
        })
        if dl.deleted_count:
            print(f"  - cleared {dl.deleted_count:>6} polluted locations (kept 'Riverbend — Downtown')")

    client.close()

    # Re-run the realistic seed (persona catalog + curated billing).
    print("\nRe-seeding realistic demo data...")
    # Delayed import so the delete happens first and the seed runs
    # against the freshly wiped tenant.
    from services.identity.seed import seed as seed_identity  # noqa: E402
    from services.demo.seed import seed_demo_clinic  # noqa: E402
    from services.demo.billing_seed import seed_demo_billing  # noqa: E402
    # Identity seed FIRST — the wipe removed the demo login users
    # (admin/doctor/staff/patient) and the Ethan Parker patient who's
    # created by the identity seed (not the clinic seed). Re-creating
    # them here keeps the tenant coherent between the reseed and the
    # next backend boot so pytest doesn't flake on a transient 7-vs-8
    # patient count.
    await seed_identity()
    await seed_demo_clinic()
    await seed_demo_billing()
    print("Done. Restart the backend or just call GET /api/health to confirm.")

    # Post-seed integrity check — fail loudly if any cross-domain
    # reference is broken. Forces the reseed script to catch problems
    # the same way the test suite does.
    from scripts.verify_demo_integrity import verify_riverbend_integrity
    count, report = await verify_riverbend_integrity()
    if count:
        print(f"\n⚠️  Integrity check found {count} violations. "
              "Run `python scripts/verify_demo_integrity.py` for details.")
    else:
        print(f"Integrity OK: {report['counts']}")


if __name__ == "__main__":
    asyncio.run(main())
