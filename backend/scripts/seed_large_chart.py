"""Large-chart fixture seeder — generates a synthetic patient with a
production-shaped Clinical timeline (250 / 500 / 1000 events) so the
G2 performance measurements can exercise the redesigned Clinical tab
against something bigger than the demo seed offers.

Design constraints (per the release-gate closeout brief):
  * Hard-block execution when APP_ENV=production.
  * Require an explicit ``--confirm-non-production`` flag every run.
  * Generate synthetic data only. Nothing is copied or transformed
    from a real record.
  * Idempotent: rerun on the same tenant + fixture id → no dupes.
  * One deterministic patient with >= --events timeline entries.
  * Realistic relationship graph so grouped-encounter + grouped-
    timeline endpoints behave normally (each encounter carries an
    appointment_id, each note carries encounter_id, etc.).
  * Stable seed identifier (``LARGE_CHART_FIXTURE_ID``) so tests +
    the cleanup path can find the fixture again.
  * Cleanup path (``--cleanup``) deletes only rows tagged with the
    fixture marker; nothing else.
  * The patient id is printed to the operator console only. No
    telemetry emission.

CLI::

    python -m scripts.seed_large_chart --confirm-non-production
    python -m scripts.seed_large_chart --confirm-non-production --events 500
    python -m scripts.seed_large_chart --confirm-non-production --cleanup

Every document created by this script carries the marker field::

    fixture_source = "large_chart_seed"
    fixture_id     = LARGE_CHART_FIXTURE_ID

which is what the cleanup path filters on.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

# Make sibling packages importable when invoked as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv("/app/backend/.env")

# --------------------------------------------------------------------
# Stable identifiers — the whole point of "idempotent + cleanable".
# Do NOT change these between releases.
# --------------------------------------------------------------------
LARGE_CHART_FIXTURE_ID = "large-chart-fixture-v1"
FIXTURE_PATIENT_ID = "fixture-large-chart-patient-0001"
FIXTURE_PROVIDER_ID = "fixture-large-chart-provider-0001"
FIXTURE_EPISODE_ID = "fixture-large-chart-episode-0001"
FIXTURE_MARKER = {
    "fixture_source": "large_chart_seed",
    "fixture_id": LARGE_CHART_FIXTURE_ID,
}

# Collections that participate in the fixture. Cleanup and integrity
# tests walk this list.
FIXTURE_COLLECTIONS: tuple[str, ...] = (
    "patients",
    "appointments",
    "clinical_episode_cases",
    "clinical_encounters",
    "clinical_follow_up_notes",
    "clinical_initial_exams",
    "clinical_reexams",
    "clinical_diagnoses",
    "clinical_treatment_plans",
    "clinical_outcome_entries",
    "clinical_media",
    "clinical_billing_readiness",
)


# --------------------------------------------------------------------
# Production guard — refuse to run unless APP_ENV is non-production
# AND the operator opted in with --confirm-non-production.
# --------------------------------------------------------------------
def _is_production() -> bool:
    val = (os.environ.get("APP_ENV") or "").strip().lower()
    return val in {"production", "prod"}


def _enforce_guard(confirm_non_production: bool) -> None:
    if _is_production():
        raise SystemExit(
            "REFUSING TO RUN: APP_ENV=production. This seeder generates "
            "synthetic PHI-shaped data and is not permitted in production."
        )
    if not confirm_non_production:
        raise SystemExit(
            "REFUSING TO RUN: pass --confirm-non-production to acknowledge "
            "this environment is not production. This flag is mandatory on "
            "every run — never bake it into a service unit."
        )


# --------------------------------------------------------------------
# Deterministic helpers — every id / string derived from the fixture
# constants so a rerun matches the previous run byte-for-byte.
# --------------------------------------------------------------------
def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _det_id(prefix: str, n: int) -> str:
    return f"fixture-{prefix}-{n:05d}"


def _rng() -> random.Random:
    # Seeded RNG so any random selection stays reproducible across runs.
    return random.Random(f"{LARGE_CHART_FIXTURE_ID}::rng")


async def _default_tenant_id(db: AsyncIOMotorDatabase) -> str:
    tenant = await db.tenants.find_one({"slug": "default"}, {"_id": 0, "id": 1})
    if not tenant:
        raise SystemExit(
            "No default tenant found. Run the demo seed first (backend boot "
            "hooks handle this automatically)."
        )
    return tenant["id"]


# --------------------------------------------------------------------
# Cleanup — before every seed AND when --cleanup is passed. Deletes
# only rows tagged with the fixture marker; nothing else can match.
# --------------------------------------------------------------------
async def cleanup(db: AsyncIOMotorDatabase, tenant_id: str) -> dict[str, int]:
    """Delete every row tagged with the fixture marker in this tenant.

    Uses the marker fields (``fixture_source`` + ``fixture_id``) so the
    query cannot accidentally match a curated demo row.
    """
    counts: dict[str, int] = {}
    match = {"tenant_id": tenant_id, **FIXTURE_MARKER}
    for coll in FIXTURE_COLLECTIONS:
        res = await db[coll].delete_many(match)
        counts[coll] = res.deleted_count
    return counts


# --------------------------------------------------------------------
# Seed — builds a deterministic patient chart with the requested
# number of timeline events. Events are distributed across appointments
# (each linked to encounter + note + billing readiness), plus
# standalone artifacts (initial exam, treatment plan, re-exams,
# outcomes, imaging, diagnoses).
# --------------------------------------------------------------------
async def seed(
    db: AsyncIOMotorDatabase,
    tenant_id: str,
    event_count: int,
) -> dict[str, Any]:
    """Idempotent seed. Every rerun cleans first then rewrites.

    ``event_count`` is the minimum number of timeline events the
    resulting patient chart contains — we count each of:
      * appointment
      * encounter
      * note (signed / draft / amended mix)
      * treatment plan
      * diagnosis
      * outcome entry
      * media row
      * re-exam
      * initial exam
    """
    if event_count < 25:
        raise SystemExit("event_count must be >= 25 (small profile lower bound)")

    now = datetime.now(timezone.utc)
    now_iso = _iso(now)
    rng = _rng()

    # Cleanup any previous fixture rows first so the run is idempotent.
    await cleanup(db, tenant_id)

    # -----------------------------
    # Patient
    # -----------------------------
    patient_doc = {
        "id": FIXTURE_PATIENT_ID,
        "tenant_id": tenant_id,
        "first_name": "Fixture",
        "last_name": "LargeChart",
        "dob": "1975-04-12",
        "gender": "unknown",
        "email": "fixture-large-chart@example.invalid",
        "phone": "+15550000000",
        "status": "active",
        "created_at": now_iso,
        "updated_at": now_iso,
        **FIXTURE_MARKER,
    }
    await db.patients.insert_one(patient_doc)

    # -----------------------------
    # Episode
    # -----------------------------
    episode_doc = {
        "id": FIXTURE_EPISODE_ID,
        "tenant_id": tenant_id,
        "patient_id": FIXTURE_PATIENT_ID,
        "case_type": "insurance",
        "status": "active",
        "title": "Chronic mid-back pain — synthetic",
        "chief_complaint": "Synthetic chart for performance testing only.",
        "onset_date": "2024-01-15",
        "start_date": _iso(now - timedelta(days=365)),
        "responsible_provider_id": FIXTURE_PROVIDER_ID,
        "tags": [],
        "created_at": _iso(now - timedelta(days=365)),
        "updated_at": now_iso,
        **FIXTURE_MARKER,
    }
    await db.clinical_episode_cases.insert_one(episode_doc)

    # -----------------------------
    # Diagnoses (2 active + 1 resolved)
    # -----------------------------
    diagnoses = [
        {
            "id": _det_id("dx", 1), "tenant_id": tenant_id,
            "patient_id": FIXTURE_PATIENT_ID, "episode_id": FIXTURE_EPISODE_ID,
            "icd10_code": "M54.6", "description": "Pain in thoracic spine",
            "status": "active", "ranking": 1, "is_primary": True,
            "onset_date": "2024-01-15",
            "created_at": _iso(now - timedelta(days=360)),
            "updated_at": now_iso, **FIXTURE_MARKER,
        },
        {
            "id": _det_id("dx", 2), "tenant_id": tenant_id,
            "patient_id": FIXTURE_PATIENT_ID, "episode_id": FIXTURE_EPISODE_ID,
            "icd10_code": "M62.830", "description": "Muscle spasm of back",
            "status": "active", "ranking": 2, "is_primary": False,
            "onset_date": "2024-01-15",
            "created_at": _iso(now - timedelta(days=350)),
            "updated_at": now_iso, **FIXTURE_MARKER,
        },
        {
            "id": _det_id("dx", 3), "tenant_id": tenant_id,
            "patient_id": FIXTURE_PATIENT_ID, "episode_id": FIXTURE_EPISODE_ID,
            "icd10_code": "M54.2", "description": "Cervicalgia",
            "status": "resolved", "ranking": 3, "is_primary": False,
            "onset_date": "2024-01-15",
            "created_at": _iso(now - timedelta(days=320)),
            "updated_at": _iso(now - timedelta(days=180)), **FIXTURE_MARKER,
        },
    ]
    if diagnoses:
        await db.clinical_diagnoses.insert_many(diagnoses)

    # -----------------------------
    # Treatment plan
    # -----------------------------
    plan_doc = {
        "id": _det_id("plan", 1), "tenant_id": tenant_id,
        "patient_id": FIXTURE_PATIENT_ID, "episode_id": FIXTURE_EPISODE_ID,
        "responsible_provider_id": FIXTURE_PROVIDER_ID,
        "plan_status": "active",
        "title": "12-week thoracic mobilization plan (synthetic)",
        "diagnosis_ids": [d["id"] for d in diagnoses[:2]],
        "target_body_regions": ["thoracic"],
        "frequency_visits_per_week": 3,
        "frequency_total_visits": 36,
        "expected_duration_weeks": 12,
        "start_date": _iso(now - timedelta(days=200)),
        "re_exam_date": (now - timedelta(days=30)).date().isoformat(),
        "planned_interventions": [],
        "goals": [], "baselines": {},
        "configured_outcome_measures": ["ndi", "oswestry"],
        "created_at": _iso(now - timedelta(days=200)),
        "updated_at": now_iso, **FIXTURE_MARKER,
    }
    await db.clinical_treatment_plans.insert_one(plan_doc)

    # -----------------------------
    # Initial exam (1)
    # -----------------------------
    initial_exam = {
        "id": _det_id("iexam", 1), "tenant_id": tenant_id,
        "patient_id": FIXTURE_PATIENT_ID, "episode_id": FIXTURE_EPISODE_ID,
        "provider_id": FIXTURE_PROVIDER_ID, "provider_name": "Fixture Provider",
        "date_of_service": _iso(now - timedelta(days=360)),
        "status": "signed",
        "chief_complaint": "Synthetic — thoracic pain",
        "signed_at": _iso(now - timedelta(days=360)),
        "created_at": _iso(now - timedelta(days=360)),
        "updated_at": _iso(now - timedelta(days=360)),
        **FIXTURE_MARKER,
    }
    await db.clinical_initial_exams.insert_one(initial_exam)

    # -----------------------------
    # Determine how many visit-blocks we need
    # Each visit-block contributes 3 timeline events (appointment +
    # encounter + note). Billing-readiness rows travel with the
    # encounter but are surfaced as a status dimension, not a timeline
    # event. Non-visit reserved artifacts:
    #   3 diagnoses + 1 plan + 1 initial exam + 4 reexams + 16 outcomes
    #   + 4 media = 29 events.
    # -----------------------------
    reserved_non_visit = 29
    remaining = max(event_count - reserved_non_visit, 21)
    # +2 buffer so integer division on tight requests still lands
    # above target.
    n_visits = max((remaining + 2) // 3, 7)

    # Distribute over the past 12 months
    start = now - timedelta(days=365)
    span_days = 365
    step_days = max(span_days // max(n_visits, 1), 1)

    appointments: list[dict[str, Any]] = []
    encounters: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    billing_rows: list[dict[str, Any]] = []

    # Note-status distribution: 70% signed, 20% draft, 10% amended
    def _note_status(i: int) -> str:
        r = i % 10
        if r < 7:
            return "signed"
        if r < 9:
            return "draft"
        return "amended"

    def _billing_status(i: int) -> str:
        r = i % 10
        if r < 7:
            return "ready"
        if r < 9:
            return "warning"
        return "blocked"

    for i in range(n_visits):
        appt_id = _det_id("appt", i + 1)
        enc_id = _det_id("enc", i + 1)
        note_id = _det_id("note", i + 1)
        readiness_id = _det_id("ready", i + 1)

        appt_start = start + timedelta(days=i * step_days)
        appt_end = appt_start + timedelta(minutes=30)

        appointments.append({
            "id": appt_id, "tenant_id": tenant_id,
            "patient_id": FIXTURE_PATIENT_ID,
            "provider_id": FIXTURE_PROVIDER_ID,
            "appointment_type": "follow_up",
            "start_time": _iso(appt_start), "end_time": _iso(appt_end),
            "status": "completed",
            "reason": "Synthetic visit",
            "created_at": _iso(appt_start - timedelta(days=1)),
            "updated_at": _iso(appt_end),
            **FIXTURE_MARKER,
        })

        encounters.append({
            "id": enc_id, "tenant_id": tenant_id,
            "patient_id": FIXTURE_PATIENT_ID,
            "appointment_id": appt_id,
            "provider_id": FIXTURE_PROVIDER_ID,
            "provider_name": "Fixture Provider",
            "episode_id": FIXTURE_EPISODE_ID,
            "encounter_type": "follow_up" if i > 0 else "new_patient_exam",
            "status": "completed",
            "date_of_service": _iso(appt_start),
            "scheduled_start": _iso(appt_start),
            "scheduled_end": _iso(appt_end),
            "scheduled_duration_min": 30,
            "actual_start": _iso(appt_start),
            "actual_end": _iso(appt_end),
            "appointment_snapshot": {
                "appointment_id": appt_id,
                "patient_id": FIXTURE_PATIENT_ID,
                "provider_id": FIXTURE_PROVIDER_ID,
                "start_time": _iso(appt_start),
                "end_time": _iso(appt_end),
                "status": "completed",
            },
            "appointment_status_at_launch": "scheduled",
            "is_exception": False,
            "completed_at": _iso(appt_end),
            "created_at": _iso(appt_start),
            "updated_at": _iso(appt_end),
            **FIXTURE_MARKER,
        })

        status = _note_status(i)
        notes.append({
            "id": note_id, "tenant_id": tenant_id,
            "patient_id": FIXTURE_PATIENT_ID,
            "encounter_id": enc_id, "appointment_id": appt_id,
            "provider_id": FIXTURE_PROVIDER_ID,
            "episode_id": FIXTURE_EPISODE_ID,
            "treatment_plan_id": plan_doc["id"],
            "date_of_service": _iso(appt_start),
            "status": status,
            "signed_at": _iso(appt_end) if status in {"signed", "amended"} else None,
            "signed_by": FIXTURE_PROVIDER_ID if status in {"signed", "amended"} else None,
            "subjective": {}, "objective": {}, "assessment": {}, "plan": {},
            "created_at": _iso(appt_start),
            "updated_at": _iso(appt_end + timedelta(days=1 if status == "amended" else 0)),
            **FIXTURE_MARKER,
        })

        b_status = _billing_status(i)
        billing_rows.append({
            "id": readiness_id, "tenant_id": tenant_id,
            "patient_id": FIXTURE_PATIENT_ID,
            "encounter_id": enc_id,
            "status": b_status,
            "warning_count": 1 if b_status == "warning" else 0,
            "blocked_count": 1 if b_status == "blocked" else 0,
            "checks": [
                {"key": "note_signed", "status": "pass" if status == "signed" else "warning",
                 "detail": "Synthetic"}
            ],
            "created_at": _iso(appt_end),
            "updated_at": _iso(appt_end),
            **FIXTURE_MARKER,
        })

    if appointments:
        await db.appointments.insert_many(appointments)
    if encounters:
        await db.clinical_encounters.insert_many(encounters)
    if notes:
        await db.clinical_follow_up_notes.insert_many(notes)
    if billing_rows:
        await db.clinical_billing_readiness.insert_many(billing_rows)

    # -----------------------------
    # Re-exams (4) — must link to distinct encounters (unique index on
    # tenant_id + encounter_id). We piggy-back on 4 of the seeded
    # encounters spaced across the timeline.
    # -----------------------------
    reexams: list[dict[str, Any]] = []
    reexam_slots = min(4, len(encounters))
    stride = max(len(encounters) // max(reexam_slots, 1), 1)
    for i in range(reexam_slots):
        enc = encounters[i * stride]
        d = datetime.fromisoformat(enc["date_of_service"])
        reexams.append({
            "id": _det_id("reexam", i + 1), "tenant_id": tenant_id,
            "patient_id": FIXTURE_PATIENT_ID, "episode_id": FIXTURE_EPISODE_ID,
            "encounter_id": enc["id"],
            "appointment_id": enc["appointment_id"],
            "provider_id": FIXTURE_PROVIDER_ID, "provider_name": "Fixture Provider",
            "treatment_plan_id": plan_doc["id"],
            "date_of_service": _iso(d),
            "status": "signed" if i > 0 else "draft",
            "created_at": _iso(d), "updated_at": _iso(d),
            **FIXTURE_MARKER,
        })
    if reexams:
        await db.clinical_reexams.insert_many(reexams)

    # -----------------------------
    # Outcome entries — NDI + Oswestry over 12 months (16 entries)
    # -----------------------------
    outcomes: list[dict[str, Any]] = []
    for instrument, base_score, max_score in (
        ("ndi", 42, 100),
        ("oswestry", 38, 100),
    ):
        for i in range(8):
            captured = now - timedelta(days=i * 45 + 20)
            score = max(base_score - i * 3 + rng.randint(-2, 2), 4)
            outcomes.append({
                "id": _det_id(f"out-{instrument}", i + 1), "tenant_id": tenant_id,
                "patient_id": FIXTURE_PATIENT_ID, "episode_id": FIXTURE_EPISODE_ID,
                "measure": instrument, "instrument_key": instrument,
                "score": score, "max_score": max_score,
                "captured_at": _iso(captured), "recorded_on": captured.date().isoformat(),
                "notes": None,
                "created_at": _iso(captured), "updated_at": _iso(captured),
                **FIXTURE_MARKER,
            })
    if outcomes:
        await db.clinical_outcome_entries.insert_many(outcomes)

    # -----------------------------
    # Media (4)
    # -----------------------------
    modalities = ["xray", "mri", "ct", "ultrasound"]
    media: list[dict[str, Any]] = []
    for i, m in enumerate(modalities):
        captured = now - timedelta(days=200 - i * 40)
        media.append({
            "id": _det_id("media", i + 1), "tenant_id": tenant_id,
            "patient_id": FIXTURE_PATIENT_ID, "episode_id": FIXTURE_EPISODE_ID,
            "storage_key": f"synthetic/{LARGE_CHART_FIXTURE_ID}/media-{i + 1}",
            "kind": "imaging",
            "imaging_modality": m,
            "label": f"Synthetic {m.upper()} — thoracic",
            "captured_at": _iso(captured),
            "created_at": _iso(captured), "updated_at": _iso(captured),
            **FIXTURE_MARKER,
        })
    if media:
        await db.clinical_media.insert_many(media)

    # -----------------------------
    # Event-count summary
    # -----------------------------
    counts = {
        "patients": 1,
        "clinical_episode_cases": 1,
        "clinical_diagnoses": len(diagnoses),
        "clinical_treatment_plans": 1,
        "clinical_initial_exams": 1,
        "appointments": len(appointments),
        "clinical_encounters": len(encounters),
        "clinical_follow_up_notes": len(notes),
        "clinical_billing_readiness": len(billing_rows),
        "clinical_reexams": len(reexams),
        "clinical_outcome_entries": len(outcomes),
        "clinical_media": len(media),
    }
    total_events = (
        len(appointments) + len(encounters) + len(notes)
        + len(diagnoses) + 1  # treatment plan
        + 1  # initial exam
        + len(reexams) + len(outcomes) + len(media)
    )
    counts["_total_timeline_events"] = total_events
    counts["_patient_id"] = FIXTURE_PATIENT_ID
    counts["_tenant_id"] = tenant_id
    return counts


# --------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Seed a synthetic large-chart patient for Clinical performance "
            "measurement. Non-production only."
        ),
    )
    p.add_argument(
        "--confirm-non-production", action="store_true",
        help="Mandatory acknowledgement that this environment is not "
             "production. Missing → refuse.",
    )
    p.add_argument(
        "--events", type=int, default=250,
        help="Target minimum timeline event count. Common: 250, 500, 1000.",
    )
    p.add_argument(
        "--cleanup", action="store_true",
        help="Remove all fixture rows and exit. Idempotent.",
    )
    return p.parse_args(argv)


async def _amain(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _enforce_guard(args.confirm_non_production)

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        db = client[os.environ["DB_NAME"]]
        tenant_id = await _default_tenant_id(db)

        if args.cleanup:
            counts = await cleanup(db, tenant_id)
            total = sum(counts.values())
            print(f"[large_chart_seed] cleanup complete — {total} rows removed")
            for coll, n in counts.items():
                if n:
                    print(f"  · {coll}: {n}")
            return 0

        counts = await seed(db, tenant_id, event_count=args.events)
        total = counts["_total_timeline_events"]
        pid = counts["_patient_id"]
        print(f"[large_chart_seed] seed complete — {total} timeline events")
        for coll in FIXTURE_COLLECTIONS:
            if coll in counts and counts[coll]:
                print(f"  · {coll}: {counts[coll]}")
        # Print patient id ONLY to local operator console. No telemetry.
        print(f"[large_chart_seed] fixture patient_id = {pid}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
