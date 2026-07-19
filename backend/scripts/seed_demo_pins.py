"""Demo PIN seeder — sets a known 6-digit PIN on every demo user.

Idempotent: re-running re-hashes the same PIN, never changes the
mapping. Safe to run on a live demo environment because it only
touches accounts that map to a fictional Riverbend / Sunrise persona.

Usage::

    python -m scripts.seed_demo_pins

The PINs are documented in ``/app/memory/test_credentials.md`` so the
testing agent and devs know which PIN to type.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# Make sibling packages importable when invoked as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv("/app/backend/.env")

from core.security import hash_password  # noqa: E402

# email -> 6-digit PIN. Distinct per persona; easy to remember.
DEMO_PINS: dict[str, str] = {
    "admin@ccms.app":                  "100001",
    "doctor@ccms.app":                 "200002",
    "staff@ccms.app":                  "300003",
    "patient@ccms.app":                "400004",
    "platform-admin@ccms.app":         "500005",
    "group-admin@sunrise.ccms.app":    "600006",
    "downtown-doc@sunrise.ccms.app":   "700007",
    "floater-doc@sunrise.ccms.app":    "800008",
    "eastside-staff@sunrise.ccms.app": "900009",
}


async def main() -> None:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    now = datetime.now(timezone.utc).isoformat()
    set_count = 0
    skip_count = 0
    missing_count = 0

    for email, pin in DEMO_PINS.items():
        user = await db.users.find_one({"email": email}, {"_id": 0, "id": 1, "pin_hash": 1})
        if not user:
            print(f"  · {email}: NOT FOUND (skipped)")
            missing_count += 1
            continue
        # Always set the demo PIN. The user explicitly asked to *know*
        # the PIN per user for testing — preserving an unknown PIN
        # would defeat the purpose. This is fine because every account
        # in DEMO_PINS maps to a fictional persona.
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {
                "pin_hash": hash_password(pin),
                "pin_created_at": user.get("pin_created_at") or now,
                "pin_updated_at": now,
                "pin_failed_attempts": 0,
                "pin_locked_until": None,
                "updated_at": now,
            }},
        )
        action = "rotated" if user.get("pin_hash") else "created"
        print(f"  · {email}: PIN {pin} ({action})")
        set_count += 1
        if user.get("pin_hash"):
            skip_count += 0  # we still wrote — track only the action
    print(
        f"\nSeed summary: {set_count} PINs written, "
        f"{missing_count} accounts missing.",
    )
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
