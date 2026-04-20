"""
PHI masking helpers.

Default to the HIPAA "minimum necessary" principle: return masked forms of
email, phone, address, DOB and free-text fields unless the caller has
explicitly requested `unmask=true` AND has a role that allows unmasking AND
the access is logged to the audit trail.

Mask shapes:
  email   "morgan@ccms.app"    -> "m*****@ccms.app"
  phone   "+1-555-0104"        -> "+*-***-****-04"
  name    "Morgan Lee"         -> "M. L."
  dob     "1990-04-12"         -> "19**-**-**"
  address "124 Willow Lane..."  -> "***"
  free-text "chronic back..."   -> "[redacted]"
"""
import re

PATIENT_SENSITIVE_FIELDS = [
    "email", "phone", "address", "emergency_contact", "date_of_birth",
    "notes",
]


def mask_email(value: str | None) -> str | None:
    if not value:
        return value
    m = re.match(r"([^@]+)@(.+)", value)
    if not m:
        return "***"
    local, domain = m.group(1), m.group(2)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 1)}@{domain}"


def mask_phone(value: str | None) -> str | None:
    if not value:
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) < 4:
        return "***"
    return f"***-***-{digits[-4:]}"


def mask_name(first: str | None, last: str | None) -> str:
    fi = (first or "")[:1].upper()
    li = (last or "")[:1].upper()
    parts = [p for p in [fi and f"{fi}.", li and f"{li}."] if p]
    return " ".join(parts) if parts else "—"


def mask_date(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) >= 4 and value[:4].isdigit():
        return f"{value[:2]}**-**-**"
    return "****-**-**"


def mask_freetext(value: str | None) -> str | None:
    if value is None or value == "":
        return value
    return "[redacted]"


def mask_patient(doc: dict) -> dict:
    out = dict(doc)
    out["email"] = mask_email(out.get("email"))
    out["phone"] = mask_phone(out.get("phone"))
    out["date_of_birth"] = mask_date(out.get("date_of_birth"))
    out["address"] = mask_freetext(out.get("address"))
    out["emergency_contact"] = mask_freetext(out.get("emergency_contact"))
    out["notes"] = mask_freetext(out.get("notes"))
    # Keep first/last initials only in the display layer; the full name stays in
    # `first_name`/`last_name` so operators can still identify the record but
    # we expose a dedicated masked display:
    out["display_name_masked"] = mask_name(out.get("first_name"), out.get("last_name"))
    return out


def mask_notification(doc: dict) -> dict:
    out = dict(doc)
    if out.get("channel") == "email":
        out["to_address"] = mask_email(out.get("to_address"))
    elif out.get("channel") == "sms":
        out["to_address"] = mask_phone(out.get("to_address"))
    # Never return the full body in masked mode — it contains patient name + time.
    out["body"] = mask_freetext(out.get("body"))
    return out
