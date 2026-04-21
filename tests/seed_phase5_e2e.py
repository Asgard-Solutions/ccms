"""Seed a fresh patient + future appointment + in-progress follow_up encounter
for Sunrise tenant, then print ids so Playwright can drive UI."""
import os, sys, random, uuid
from datetime import datetime, timedelta, timezone
import requests

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")
EMAIL = "group-admin@sunrise.ccms.app"
PWD = "Sunrise@ComplianceClinic1"


def login():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": EMAIL, "password": PWD}, timeout=15)
    r.raise_for_status()
    s.headers["Authorization"] = f"Bearer {r.cookies.get('access_token')}"
    r = s.post(f"{API}/auth/reauth", json={"password": PWD}, timeout=10)
    r.raise_for_status()
    tok = r.cookies.get("reauth_token")
    if tok:
        s.headers["x-reauth-token"] = tok
    return s


def main():
    s = login()
    # provider id
    pr = s.get(f"{API}/auth/providers", timeout=10).json()
    provider_id = pr[0]["id"] if isinstance(pr, list) and pr else pr["providers"][0]["id"]
    suffix = uuid.uuid4().hex[:8]
    patient = {
        "first_name": "Phase5E2E",
        "last_name": f"Patient{suffix}",
        "email": f"phase5_{suffix}@example.com",
        "phone": "+15551230000",
        "date_of_birth": "1990-01-15",
        "gender": "female",
    }
    r = s.post(f"{API}/patients", json=patient, timeout=15)
    r.raise_for_status()
    patient_id = r.json()["id"]

    # future appointment - retry for overlap
    import time
    for _ in range(6):
        start = datetime.now(timezone.utc).replace(microsecond=0, second=0) + timedelta(
            days=random.randint(7, 60),
            hours=random.randint(0, 8),
            minutes=random.choice([0, 15, 30, 45]),
        )
        end = start + timedelta(minutes=20)
        appt = {
            "patient_id": patient_id,
            "provider_id": provider_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "reason": "Follow-up visit",
        }
        r = s.post(f"{API}/appointments", json=appt, timeout=15)
        if r.status_code == 201:
            appt_id = r.json()["id"]
            break
    else:
        raise SystemExit(f"could not book appt: {r.status_code} {r.text}")

    # launch encounter as follow_up
    r = s.post(
        f"{API}/appointments/{appt_id}/clinical/encounters",
        json={"encounter_type": "follow_up"},
        timeout=15,
    )
    r.raise_for_status()
    encounter_id = r.json()["encounter"]["id"]

    print(f"PATIENT_ID={patient_id}")
    print(f"APPOINTMENT_ID={appt_id}")
    print(f"ENCOUNTER_ID={encounter_id}")


if __name__ == "__main__":
    main()
