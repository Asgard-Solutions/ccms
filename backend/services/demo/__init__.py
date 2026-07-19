"""Demo/showcase data for Riverbend Chiropractic & Wellness.

See `services/demo/seed.py` for the realistic persona catalog and
`services/demo/billing_seed.py` for the curated billing artifacts.
Everything here is idempotent and safe to re-run on every boot.
"""
from .seed import seed_demo_clinic  # noqa: F401
from .billing_seed import seed_demo_billing  # noqa: F401
