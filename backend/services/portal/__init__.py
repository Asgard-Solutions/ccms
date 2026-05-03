"""Patient-portal API surface.

Everything under `/api/portal/*` is **patient-scoped** — we hard-require
`role=patient` and enforce that every read/write is against the patient
row linked to the current user (via `user.patient_id`).

Logically distinct from the staff identity router (which issues cookies
for `admin/doctor/staff/patient` via email + password). Portal users
authenticate via SMS OTP only.
"""
