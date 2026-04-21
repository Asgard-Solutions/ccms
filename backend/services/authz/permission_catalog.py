"""
Human-readable permission catalogue.

The backend `PERMISSIONS` list (constants.py) is the source of truth for
what permissions exist. This module adds a second, admin-facing layer on
top of that list:

  * groups every permission into one of 11 product-facing modules
    (Dashboard, Scheduling, Patients, Clinical, Billing, Claims,
    Reports, Compliance & Audit, Settings, User Management,
    Administration) so the UI can render an accordion instead of a
    flat matrix;
  * attaches a plain-English label + optional short description so the
    admin never has to parse `resource.action`;
  * exposes a compact `grouped_catalog()` helper that the new Role
    Editor + Effective-Access preview consume.

This module does NOT introduce new permissions — it only decorates the
existing ones. If a permission ever exists in `constants.PERMISSIONS`
that is not mapped here, the catalogue falls back to a sensible
"Administration" module + a titleized label so the admin still sees
it (never silently dropped).

Safe to import from any router — pure data, no DB calls.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.authz.constants import PERMISSIONS, PRIVILEGED_PERMISSIONS


# ---------------------------------------------------------------------------
# Module taxonomy — 11 product-facing groups. Order is the display order
# the admin UI renders from top to bottom.
# ---------------------------------------------------------------------------

MODULES: list[dict] = [
    {
        "key": "dashboard",
        "label": "Dashboard",
        "description": "Home metrics and quick links.",
    },
    {
        "key": "scheduling",
        "label": "Scheduling & Appointments",
        "description": "Calendar, booking, check-in, flow board, and checkout.",
    },
    {
        "key": "patients",
        "label": "Patients",
        "description": "Patient demographics, profiles, and records access.",
    },
    {
        "key": "clinical",
        "label": "Clinical",
        "description": "Charts, SOAP notes, exams, treatment plans, and intake.",
    },
    {
        "key": "billing",
        "label": "Billing",
        "description": "Charges, payments, adjustments, and refunds.",
    },
    {
        "key": "claims",
        "label": "Claims",
        "description": "Insurance claims, remittance, denials, and coding.",
    },
    {
        "key": "reports",
        "label": "Reports",
        "description": "Operational, clinical, and financial reporting.",
    },
    {
        "key": "compliance_audit",
        "label": "Compliance & Audit",
        "description": "Audit log, PHI access review, consent, privacy requests.",
    },
    {
        "key": "settings",
        "label": "Settings",
        "description": "Clinic configuration, templates, appointment types, rooms.",
    },
    {
        "key": "user_management",
        "label": "User Management",
        "description": "Users, roles, permissions, and access assignments.",
    },
    {
        "key": "administration",
        "label": "Administration",
        "description": "Integrations, API keys, service accounts, and advanced controls.",
    },
]

MODULE_KEYS = {m["key"] for m in MODULES}


# ---------------------------------------------------------------------------
# Per-permission labels and grouping.
#
# Keyed by `resource.action` (matches constants.permission_key). Each
# entry is (module_key, label, short_help).
# ---------------------------------------------------------------------------

# Plain-English verbs for action fallback
_ACTION_LABEL_FALLBACK: dict[str, str] = {
    "read": "View",
    "create": "Create",
    "update": "Edit",
    "delete": "Delete",
    "export": "Export",
    "assign": "Assign",
    "approve": "Approve",
    "submit": "Submit",
    "sign": "Sign",
    "manage": "Manage",
    "disable": "Disable",
    "enable": "Enable",
    "invite": "Invite",
    "unlock": "Unlock",
    "reset_mfa": "Reset MFA",
    "lock": "Lock",
    "revoke": "Revoke",
    "capture": "Capture",
    "override_rules": "Override rules",
    "merge_duplicate": "Merge duplicate",
    "archive": "Archive",
    "hard_delete": "Hard delete",
    "purge": "Purge",
    "collect": "Collect",
    "refund": "Refund",
    "writeoff": "Write off",
    "void": "Void",
    "correct_resubmit": "Correct & resubmit",
    "activate": "Activate",
    "update_metadata": "Edit metadata",
    "check": "Check",
    "verify_identity": "Verify identity",
    "fulfill_export": "Fulfill export",
    "fulfill_delete_anonymize": "Fulfill delete",
    "deliver": "Deliver",
    "rotate": "Rotate",
    "change": "Change",
    "revoke_self": "Revoke own",
    "revoke_other": "Revoke other users'",
    "read_self": "View own",
    "read_financial": "View financial",
    "read_clinical": "View clinical",
    "export_phi": "Export PHI",
    "work": "Work",
    "post": "Post",
}

# Plain-English nouns for resource fallback
_RESOURCE_LABEL_FALLBACK: dict[str, str] = {
    "patient": "patients",
    "patient_chart": "patient charts",
    "soap_note": "SOAP notes",
    "treatment_plan": "treatment plans",
    "appointment": "appointments",
    "waitlist": "waitlist",
    "intake_form": "intake forms",
    "insurance": "insurance policies",
    "eligibility": "eligibility",
    "billing": "billing",
    "charge": "charges",
    "payment": "payments",
    "adjustment": "adjustments",
    "claim": "claims",
    "remit": "remittance",
    "denial": "denials",
    "coding": "coding",
    "document": "documents",
    "message": "messages",
    "broadcast": "broadcasts",
    "secure_message": "secure messages",
    "consent": "consents",
    "privacy_request": "privacy requests",
    "retention_policy": "retention policy",
    "release_of_information": "release of information",
    "audit_log": "audit log",
    "phi_access_report": "PHI access report",
    "access_review": "access reviews",
    "security_event": "security events",
    "user": "users",
    "service_account": "service accounts",
    "role": "roles",
    "permission": "permissions",
    "org_settings": "organization settings",
    "clinic_settings": "clinic settings",
    "template": "templates",
    "reporting": "reports",
    "dashboard": "dashboard",
    "integration": "integrations",
    "api_key": "API keys",
    "webhook": "webhooks",
    "self": "account",
    "session": "sessions",
    "break_glass": "break-glass access",
    "settings": "settings",
}

# Explicit overrides — clearer phrasing than the fallback composition.
# Anything not listed falls back to "{action_verb} {resource_noun}".
# Format: "resource.action": (module_key, label, help)
_EXPLICIT: dict[str, tuple[str, str, str]] = {
    # -------------------------------------------------------------------
    # Dashboard
    # -------------------------------------------------------------------
    "dashboard.read":            ("dashboard", "View dashboard",
                                  "See the home page and quick metrics."),
    # -------------------------------------------------------------------
    # Scheduling
    # -------------------------------------------------------------------
    "appointment.read":          ("scheduling", "View appointments",
                                  "See the calendar and appointment details."),
    "appointment.create":        ("scheduling", "Create appointments", ""),
    "appointment.update":        ("scheduling", "Edit / reschedule appointments", ""),
    "appointment.delete":        ("scheduling", "Cancel appointments", ""),
    "appointment.override_rules":("scheduling", "Override scheduling conflicts",
                                  "Book over conflicts or outside clinic hours."),
    "waitlist.manage":           ("scheduling", "Manage waitlist", ""),
    # -------------------------------------------------------------------
    # Patients
    # -------------------------------------------------------------------
    "patient.read":              ("patients", "View patients",
                                  "See the patient directory (masked PHI by default)."),
    "patient.create":            ("patients", "Create patient records", ""),
    "patient.update":            ("patients", "Edit patient demographics", ""),
    "patient.delete":            ("patients", "Delete patients",
                                  "Soft-delete with 7-year retention."),
    "patient.merge_duplicate":   ("patients", "Merge duplicate patients", ""),
    "patient.export":            ("patients", "Export patient data",
                                  "Full JSON export — right-to-access."),
    "patient.archive":           ("patients", "Archive patients", ""),
    "patient.hard_delete":       ("patients", "Permanently delete patients",
                                  "Destructive. Requires compliance sign-off."),
    # -------------------------------------------------------------------
    # Clinical
    # -------------------------------------------------------------------
    "patient_chart.read":        ("clinical", "View patient charts", ""),
    "patient_chart.create":      ("clinical", "Create chart entries", ""),
    "patient_chart.update":      ("clinical", "Edit chart entries", ""),
    "patient_chart.delete":      ("clinical", "Delete chart entries", ""),
    "patient_chart.manage":      ("clinical", "Manage charts", ""),
    "soap_note.read":            ("clinical", "View SOAP notes", ""),
    "soap_note.create":          ("clinical", "Create SOAP notes", ""),
    "soap_note.update":          ("clinical", "Edit SOAP notes", ""),
    "soap_note.sign":            ("clinical", "Sign SOAP notes",
                                  "Terminal — signed notes are immutable."),
    "treatment_plan.read":       ("clinical", "View treatment plans", ""),
    "treatment_plan.create":     ("clinical", "Create treatment plans", ""),
    "treatment_plan.update":     ("clinical", "Edit treatment plans", ""),
    "treatment_plan.approve":    ("clinical", "Approve treatment plans", ""),
    "intake_form.read":          ("clinical", "View intake forms", ""),
    "intake_form.create":        ("clinical", "Create intake forms", ""),
    "intake_form.update":        ("clinical", "Edit intake forms", ""),
    "intake_form.lock":          ("clinical", "Lock intake forms", ""),
    "document.read":             ("clinical", "View documents", ""),
    "document.create":           ("clinical", "Upload documents", ""),
    "document.update_metadata":  ("clinical", "Edit document metadata", ""),
    "document.delete":           ("clinical", "Delete documents", ""),
    "document.sign":             ("clinical", "Sign documents", ""),
    "document.export":           ("clinical", "Export documents", ""),
    "document.purge":            ("clinical", "Permanently purge documents",
                                  "Destructive. Requires compliance sign-off."),
    # -------------------------------------------------------------------
    # Billing
    # -------------------------------------------------------------------
    "billing.read":              ("billing", "View billing", ""),
    "billing.export":            ("billing", "Export billing data", ""),
    "billing.void":              ("billing", "Void transactions",
                                  "Destructive financial action."),
    "charge.create":             ("billing", "Create charges", ""),
    "payment.collect":           ("billing", "Collect payments", ""),
    "payment.refund":            ("billing", "Refund payments", ""),
    "adjustment.writeoff":       ("billing", "Write off balances", ""),
    "insurance.read":            ("billing", "View insurance policies", ""),
    "insurance.create":          ("billing", "Add insurance policies", ""),
    "insurance.update":          ("billing", "Edit insurance policies", ""),
    "insurance.export":          ("billing", "Export insurance data", ""),
    "eligibility.check":         ("billing", "Check eligibility", ""),
    # -------------------------------------------------------------------
    # Claims
    # -------------------------------------------------------------------
    "claim.read":                ("claims", "View claims", ""),
    "claim.create":              ("claims", "Create claims", ""),
    "claim.submit":              ("claims", "Submit claims", ""),
    "claim.correct_resubmit":    ("claims", "Correct & resubmit claims", ""),
    "remit.read":                ("claims", "View remittance (835s)", ""),
    "remit.post":                ("claims", "Post remittance", ""),
    "denial.work":               ("claims", "Work denials", ""),
    "coding.update":             ("claims", "Edit coding", ""),
    # -------------------------------------------------------------------
    # Reports
    # -------------------------------------------------------------------
    "reporting.read":            ("reports", "View operational reports", ""),
    "reporting.read_financial":  ("reports", "View financial reports", ""),
    "reporting.read_clinical":   ("reports", "View clinical reports",
                                  "Contains PHI."),
    "reporting.export":          ("reports", "Export reports", ""),
    "reporting.export_phi":      ("reports", "Export reports containing PHI",
                                  "Destructive to privacy posture if leaked."),
    # -------------------------------------------------------------------
    # Compliance & Audit
    # -------------------------------------------------------------------
    "audit_log.read":            ("compliance_audit", "View audit log", ""),
    "audit_log.export":          ("compliance_audit", "Export audit log", ""),
    "audit_log.delete":          ("compliance_audit", "Delete audit rows",
                                  "Destructive. Typically forbidden."),
    "phi_access_report.read":    ("compliance_audit", "View PHI access report", ""),
    "access_review.manage":      ("compliance_audit", "Manage access reviews", ""),
    "security_event.read":       ("compliance_audit", "View security events", ""),
    "consent.read":              ("compliance_audit", "View consents", ""),
    "consent.capture":           ("compliance_audit", "Capture consents", ""),
    "consent.revoke":            ("compliance_audit", "Revoke consents", ""),
    "release_of_information.read":     ("compliance_audit", "View ROI", ""),
    "release_of_information.create":   ("compliance_audit", "Create ROI", ""),
    "release_of_information.approve":  ("compliance_audit", "Approve ROI", ""),
    "privacy_request.read":      ("compliance_audit", "View privacy requests", ""),
    "privacy_request.create":    ("compliance_audit", "Create privacy requests", ""),
    "privacy_request.verify_identity": ("compliance_audit",
                                        "Verify privacy-request identity", ""),
    "privacy_request.fulfill_export":  ("compliance_audit",
                                        "Fulfill privacy export", ""),
    "privacy_request.fulfill_delete_anonymize": (
        "compliance_audit", "Fulfill privacy delete / anonymize", ""),
    "retention_policy.manage":   ("compliance_audit", "Manage retention policy", ""),
    "break_glass.activate":      ("compliance_audit", "Activate break-glass",
                                  "Emergency PHI access. Heavily audited."),
    "secure_message.export":     ("compliance_audit", "Export secure messages", ""),
    # -------------------------------------------------------------------
    # Settings
    # -------------------------------------------------------------------
    "org_settings.read":         ("settings", "View organization settings", ""),
    "org_settings.update":       ("settings", "Edit organization settings", ""),
    "clinic_settings.read":      ("settings", "View clinic settings", ""),
    "clinic_settings.update":    ("settings", "Edit clinic settings",
                                  "Hours, appointment types, rooms, payers, fee schedules."),
    "template.manage":           ("settings", "Manage templates", ""),
    # -------------------------------------------------------------------
    # User Management
    # -------------------------------------------------------------------
    "user.read":                 ("user_management", "View users", ""),
    "user.invite":               ("user_management", "Invite users", ""),
    "user.disable":              ("user_management", "Disable users", ""),
    "user.unlock":               ("user_management", "Unlock users", ""),
    "user.reset_mfa":            ("user_management", "Reset user MFA", ""),
    "role.read":                 ("user_management", "View roles", ""),
    "role.create":               ("user_management", "Create custom roles", ""),
    "role.update":               ("user_management", "Edit roles", ""),
    "role.assign":               ("user_management", "Assign roles to users", ""),
    "permission.read":           ("user_management", "View permission catalog", ""),
    "permission.update":         ("user_management", "Grant per-user overrides",
                                  "Advanced. Use sparingly."),
    # -------------------------------------------------------------------
    # Administration (integrations, API keys, webhooks, service accounts,
    # self-service session/auth)
    # -------------------------------------------------------------------
    "integration.read":          ("administration", "View integrations", ""),
    "integration.create":        ("administration", "Create integrations", ""),
    "integration.update":        ("administration", "Edit integrations", ""),
    "integration.disable":       ("administration", "Disable integrations", ""),
    "api_key.create":            ("administration", "Create API keys", ""),
    "api_key.rotate":            ("administration", "Rotate API keys", ""),
    "webhook.read":              ("administration", "View webhooks", ""),
    "webhook.deliver":           ("administration", "Deliver webhooks", ""),
    "service_account.create":    ("administration", "Create service accounts", ""),
    "self.change":               ("administration", "Change own password", ""),
    "self.manage":               ("administration", "Manage own MFA", ""),
    "session.read_self":         ("administration", "View own sessions", ""),
    "session.revoke_self":       ("administration", "Revoke own sessions", ""),
    "session.revoke_other":      ("administration", "Revoke other users' sessions", ""),
    "message.read":              ("administration", "View internal messages", ""),
    "message.create":            ("administration", "Send internal messages", ""),
    "broadcast.create":          ("administration", "Send broadcast messages", ""),
}


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    resource: str
    action: str
    module: str
    label: str
    help: str
    sensitivity: str
    phi: bool
    clinical: bool
    financial: bool
    export: bool
    destructive: bool
    privileged: bool


def _build_label(resource: str, action: str) -> str:
    verb = _ACTION_LABEL_FALLBACK.get(action, action.replace("_", " ").title())
    noun = _RESOURCE_LABEL_FALLBACK.get(resource, resource.replace("_", " "))
    return f"{verb} {noun}"


def _entry_for(p: dict) -> CatalogEntry:
    resource = p["resource"]
    action = p["action"]
    key = f"{resource}.{action}"
    explicit = _EXPLICIT.get(key)
    if explicit:
        module, label, help_txt = explicit
    else:
        # Fallback: best-effort plain English + "administration" bucket
        # so we never silently drop a permission from the UI.
        module = "administration"
        label = _build_label(resource, action)
        help_txt = ""
    return CatalogEntry(
        key=key,
        resource=resource,
        action=action,
        module=module,
        label=label,
        help=help_txt,
        sensitivity=p.get("sensitivity", "low"),
        phi=bool(p.get("phi")),
        clinical=bool(p.get("clinical")),
        financial=bool(p.get("financial")),
        export=bool(p.get("export")),
        destructive=bool(p.get("destructive")),
        privileged=key in PRIVILEGED_PERMISSIONS,
    )


_CATALOG: list[CatalogEntry] | None = None


def catalog_entries() -> list[CatalogEntry]:
    """Return every permission decorated with module+label. Cached."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = [_entry_for(p) for p in PERMISSIONS]
    return _CATALOG


def entry_by_key(key: str) -> CatalogEntry | None:
    for e in catalog_entries():
        if e.key == key:
            return e
    return None


def grouped_catalog() -> list[dict]:
    """Module-grouped catalogue for the admin UI.

    Returns a list ordered by MODULES.order. Each entry:
      { module, label, description, permissions: [ {key, label, help,
        sensitivity, phi, clinical, financial, export, destructive,
        privileged, resource, action}, ... ] }

    Permissions inside a module are ordered by (sensitivity desc, label asc)
    so the most impactful rights are surfaced first — this is important
    because the admin sees them in that order in the Role Editor.
    """
    entries = catalog_entries()
    buckets: dict[str, list[CatalogEntry]] = {m["key"]: [] for m in MODULES}
    for e in entries:
        buckets.setdefault(e.module, []).append(e)

    sens_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}

    def _sort_key(x: CatalogEntry) -> tuple[int, str]:
        return (sens_rank.get(x.sensitivity, 99), x.label.lower())

    out: list[dict] = []
    for m in MODULES:
        bucket = sorted(buckets.get(m["key"], []), key=_sort_key)
        out.append({
            "module": m["key"],
            "label": m["label"],
            "description": m["description"],
            "permissions": [
                {
                    "key": e.key,
                    "resource": e.resource,
                    "action": e.action,
                    "label": e.label,
                    "help": e.help,
                    "sensitivity": e.sensitivity,
                    "phi": e.phi,
                    "clinical": e.clinical,
                    "financial": e.financial,
                    "export": e.export,
                    "destructive": e.destructive,
                    "privileged": e.privileged,
                }
                for e in bucket
            ],
        })
    return out


# ---------------------------------------------------------------------------
# Plain-English effective-access summary.
#
# Used by the "review access before save" step of the Create User flow and
# the "Edit access" sidebar on the Users page. Given a list of granted
# permission keys, return a short paragraph a clinic admin can read aloud.
# ---------------------------------------------------------------------------

# Module → how to phrase presence/absence of the module's permissions in
# the summary. Keep short — full detail lives in the grouped accordion.
_MODULE_SUMMARY_POSITIVE: dict[str, str] = {
    "dashboard": "see the dashboard",
    "scheduling": "view and manage appointments",
    "patients": "view patient records",
    "clinical": "view clinical charts and documentation",
    "billing": "work with billing, charges, and payments",
    "claims": "manage insurance claims and remittance",
    "reports": "view reports",
    "compliance_audit": "review audit and compliance data",
    "settings": "view clinic settings",
    "user_management": "view users and roles",
    "administration": "work with integrations and advanced controls",
}

_MODULE_SUMMARY_WRITE: dict[str, str] = {
    "patients": "edit patient demographics",
    "clinical": "create and edit clinical documentation",
    "billing": "collect payments and edit charges",
    "claims": "submit and correct claims",
    "reports": "export reports",
    "settings": "manage clinic settings",
    "user_management": "manage users and roles",
    "scheduling": "create and reschedule appointments",
}


def explain_permissions(permission_keys: list[str]) -> dict:
    """Return a plain-English summary of what a caller can do given the
    set of permission keys in their effective grant list.

    Shape:
      {
        "summary": "This user can view and edit appointments, manage
                    patient demographics, and view billing, but cannot
                    delete records or change clinic settings.",
        "can": ["see the dashboard", "view patient records", ...],
        "cannot": ["delete records", "change clinic settings"],
        "by_module": {
            "scheduling": {"granted": 4, "total": 6, "has_write": true},
            ...
        },
        "sensitive_grants": ["Sign SOAP notes", "Refund payments", ...],
      }
    """
    granted = set(permission_keys or [])
    entries = catalog_entries()

    # Per-module counts + write/read mix
    module_stats: dict[str, dict] = {m["key"]: {
        "granted": 0, "total": 0, "has_read": False, "has_write": False,
    } for m in MODULES}
    sensitive_grants: list[str] = []

    write_actions = {
        "create", "update", "delete", "sign", "approve", "submit",
        "void", "refund", "writeoff", "collect", "post", "purge",
        "hard_delete", "archive", "override_rules", "lock", "revoke",
        "disable", "reset_mfa", "rotate", "manage", "merge_duplicate",
        "assign", "fulfill_export", "fulfill_delete_anonymize",
        "correct_resubmit", "capture", "export",
    }
    read_actions = {"read", "read_self", "read_financial", "read_clinical", "check"}

    for e in entries:
        if e.module not in module_stats:
            module_stats[e.module] = {
                "granted": 0, "total": 0, "has_read": False, "has_write": False,
            }
        module_stats[e.module]["total"] += 1
        if e.key in granted:
            module_stats[e.module]["granted"] += 1
            if e.action in read_actions:
                module_stats[e.module]["has_read"] = True
            if e.action in write_actions:
                module_stats[e.module]["has_write"] = True
            if e.sensitivity in ("high", "critical") or e.destructive or e.privileged:
                sensitive_grants.append(e.label)

    # Build positive/negative phrases
    can: list[str] = []
    cannot: list[str] = []
    for m in MODULES:
        key = m["key"]
        st = module_stats.get(key, {})
        if st.get("granted", 0) == 0:
            # Don't list every "cannot" — only the ones that typically
            # matter to an admin's sanity check.
            neg = _MODULE_SUMMARY_POSITIVE.get(key)
            if neg and key in ("patients", "clinical", "billing", "claims",
                               "reports", "settings", "user_management"):
                cannot.append(neg)
            continue
        pos = _MODULE_SUMMARY_POSITIVE.get(key)
        if pos:
            if st.get("has_write"):
                write_phrase = _MODULE_SUMMARY_WRITE.get(key)
                can.append(write_phrase or pos)
            else:
                can.append(pos)

    # Sentence assembly
    def _join(items: list[str]) -> str:
        items = [i for i in items if i]
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]} and {items[1]}"
        return ", ".join(items[:-1]) + f", and {items[-1]}"

    positive = _join(can) or "no access yet"
    negative = _join(cannot)
    if negative:
        summary = (
            f"This user can {positive}, "
            f"but cannot {negative}."
        )
    else:
        summary = f"This user can {positive}."

    # Deduplicate + cap sensitive_grants display
    seen: set[str] = set()
    deduped_sensitive: list[str] = []
    for s in sensitive_grants:
        if s not in seen:
            seen.add(s)
            deduped_sensitive.append(s)

    return {
        "summary": summary,
        "can": can,
        "cannot": cannot,
        "by_module": module_stats,
        "sensitive_grants": deduped_sensitive,
    }
