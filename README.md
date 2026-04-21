# CCMS — Chiropractic Clinic Management System

> Cloud-native, multi-tenant clinic-management platform engineered for HIPAA
> compliance, strict tenant isolation, and production-grade auditability.

<p>
  <img src="https://img.shields.io/badge/stack-FastAPI%20%C2%B7%20React%20%C2%B7%20MongoDB-2F5D4F?style=flat-square" alt="stack" />
  <img src="https://img.shields.io/badge/auth-JWT%20%C2%B7%20MFA%20%C2%B7%20reauth-7B9A82?style=flat-square" alt="auth" />
  <img src="https://img.shields.io/badge/compliance-HIPAA-526B58?style=flat-square" alt="hipaa" />
  <img src="https://img.shields.io/badge/architecture-Postgres--ready-5C6A61?style=flat-square" alt="pg-ready" />
</p>

## Table of contents
1. [What is CCMS](#what-is-ccms)
2. [Tech stack](#tech-stack)
3. [Running locally](#running-locally)
4. [Repository layout](#repository-layout)
5. [Core capabilities](#core-capabilities)
6. [Security & compliance](#security--compliance)
7. [Testing](#testing)
8. [Documentation map](#documentation-map)
9. [Keep the docs current](#keep-the-docs-current)
10. [Contributing & support](#contributing--support)

---

## What is CCMS
CCMS is a multi-tenant SaaS that lets chiropractic practices run the clinical,
operational, and financial side of their business from one workspace. Each
tenant sees only their own data; PHI is encrypted at rest, masked by default,
and every read/write is audited.

Primary user roles: **Admin**, **Doctor**, **Staff**, **Patient**. Policy
overlays allow break-glass access for clinicians, mandatory MFA for admins,
and step-up reauth for sensitive writes (delete a patient, add a medical
record, upload a document, …).

## Tech stack
| Layer       | Tooling                                                             |
|-------------|---------------------------------------------------------------------|
| Frontend    | React 19, TailwindCSS, Shadcn UI, react-router-dom, sonner          |
| Backend     | FastAPI 0.11x, Pydantic v2, Motor (async Mongo), Uvicorn            |
| Data        | MongoDB today · **schema is Postgres-ready** (UUID PKs, no embeds)  |
| Cache / RL  | Redis (optional; graceful in-process fallback when Redis is down)   |
| Auth        | JWT (HTTP-only cookies) + TOTP MFA + step-up reauth + RBAC policies |
| Storage     | Emergent object storage (PHI uploads + signed consent PDFs)         |
| Docs        | ReportLab (signed consent PDFs), python-magic (MIME sniffing)       |
| Supervision | `supervisord` manages backend, frontend, MongoDB                    |

## Running locally
The Emergent container brings MongoDB, the backend (port 8001), and the
frontend (port 3000) online automatically under `supervisord`. Useful
commands:

```bash
# Restart services (after .env changes or dep installs)
sudo supervisorctl restart backend
sudo supervisorctl restart frontend

# Tail logs
tail -n 200 /var/log/supervisor/backend.*.log
tail -n 200 /var/log/supervisor/frontend.*.log

# Run backend regressions
cd /app/backend
REACT_APP_BACKEND_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2) \
  python -m pytest tests/test_patient_intake_phase1.py tests/test_phase5_docs_and_consent_pdf.py -q

# Run frontend wizard logic tests (pure JS, no browser)
node /app/frontend/src/pages/patientWizardLogic.test.js
```

Environment variables are sourced from `/app/backend/.env` and
`/app/frontend/.env`. Never hard-code URLs, ports, or secrets.

## Repository layout
```
/app
├── backend/
│   ├── core/                    # audit, crypto, auth, masking, cache, tenancy, object-storage, consent-pdf
│   ├── services/
│   │   ├── identity/            # login, MFA, reauth, admin user CRUD
│   │   ├── patient/             # list/detail/CRUD + split sub-routers
│   │   │   ├── router.py
│   │   │   ├── documents_router.py
│   │   │   └── consent_pdf_router.py
│   │   ├── scheduling/
│   │   ├── communication/       # mock SMS/Email (P1: replace w/ real provider)
│   │   ├── audit/               # admin-only audit viewer
│   │   ├── authz/               # RBAC policies + seed
│   │   ├── tenancy/             # tenant + location models
│   │   ├── privacy/, compliance_ops/, workforce/
│   ├── tests/                   # pytest regressions
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/               # Dashboard, Patients, PatientDetail, Appointments, …
│   │   ├── components/          # SignaturePad, PatientDocumentsCard, ReauthDialog, …
│   │   ├── contexts/            # AuthContext (cookie session + MFA flow)
│   │   └── api/                 # axios client, formatApiError
│   └── package.json
├── memory/                      # long-form architecture & compliance docs
├── docs/                        # public-facing supplementary docs
└── test_reports/                # JSON reports emitted by the testing agent
```

## Core capabilities
- **Multi-tenant foundation** — shared DB schema with strict `tenant_id` +
  `location_id` scoping on every query via `scoped_filter`.
- **RBAC** — 11 roles × 115 permissions, default-deny, policy overlays for
  break-glass + reauth. See `memory/AUTHORIZATION_GUIDE.md`.
- **Patient intake** — 4-step wizard with autosave drafts, edit-from-detail,
  legacy + grouped payload compatibility, wet-ink + typed signatures.
- **Document vault** — insurance cards, IDs, referrals, imaging. Streaming
  uploads to `SpooledTemporaryFile`, 10 MB hard cap, declared-vs-sniffed MIME
  cross-check, reauth-gated, audited.
- **Per-user theming** — Light / Dark / System preference picker in the
  top-bar, persisted to the user's profile and synced on every login.
  Dark mode is driven by CSS variables + semantic utility classes for
  zero-flash, per-page rewrites.
- **Signed consent PDFs** — ReportLab renders a one-page signed consent on
  demand; supports HIPAA / treatment / financial / telehealth / photo-release
  + custom consents in `consents.additional`.
- **Compliance ops** — control registry, evidence collection scaffolding.
- **Performance** — read/write DB split, Redis cache catalogue, read-after-
  write consistency, graceful Redis fallback.

## Security & compliance
See [`SECURITY.md`](./SECURITY.md) for the vulnerability disclosure process.
Deep safeguard inventory lives in `memory/HIPAA_COMPLIANCE.md`. Highlights:

- AES-256-GCM field-level encryption at rest for patient PHI + record text +
  appointment notes + signed consents.
- Cookie-bound JWTs with 15-minute idle timeout and step-up reauth for
  sensitive writes.
- Audit log captures every PHI access (success + failure) with IP,
  user-agent, reason, and PHI flag.
- Masked-by-default PHI with `?unmask=true` requiring break-glass reason
  (≥ 8 chars) for non-admin clinicians.
- 7-year soft-delete retention + legal-hold gate.
- Mandatory MFA prompt for admin/doctor/staff roles.

## Testing
| Layer     | How                                                                 |
|-----------|----------------------------------------------------------------------|
| Backend   | `pytest /app/backend/tests/…`                                        |
| Frontend  | `node /app/frontend/src/pages/patientWizardLogic.test.js` (pure JS)  |
| E2E       | `testing_agent_v3_fork` (invoked from the agent workflow)            |
| Lint      | `ruff` for Python, ESLint for JS/JSX                                 |

Test accounts for every role live in `memory/test_credentials.md` — update
that file whenever auth/seed scripts change.

## Documentation map
| Document                              | Purpose                                                    |
|---------------------------------------|------------------------------------------------------------|
| `README.md` (this file)               | Project overview + pointers                                |
| [`CHANGELOG.md`](./CHANGELOG.md)      | Dated, append-only record of every shipped change          |
| [`CONTRIBUTING.md`](./CONTRIBUTING.md)| Dev workflow, commit style, **doc-update rules**           |
| [`SECURITY.md`](./SECURITY.md)        | HIPAA vulnerability disclosure process                     |
| [`docs/DOC_UPDATE_POLICY.md`](./docs/DOC_UPDATE_POLICY.md) | Canonical "when you change X, update Y" matrix |
| `memory/PRD.md`                       | Product requirements + architecture notes (living)         |
| `memory/HIPAA_COMPLIANCE.md`          | Safeguard inventory (technical, admin, physical)           |
| `memory/AUTHORIZATION_GUIDE.md`       | RBAC, scopes, policy overlays                              |
| `memory/MULTI_TENANCY_ARCHITECTURE.md`| Tenant + location scoping model                            |
| `memory/COMPLIANCE_BACKLOG.md`        | Open compliance work (P1/P2)                               |
| `memory/OPERATIONAL_SECURITY_READINESS.md` | Ops/SOC2 readiness notes                              |
| `memory/test_credentials.md`          | Demo/test accounts used by the testing agent               |

## Keep the docs current
**Every feature PR must update the docs it touches.** The canonical matrix
lives in [`docs/DOC_UPDATE_POLICY.md`](./docs/DOC_UPDATE_POLICY.md) — use it
as the checklist. Short version:

| You changed…                          | You must also update…                                  |
|---------------------------------------|--------------------------------------------------------|
| A product feature / user flow         | `memory/PRD.md`, `CHANGELOG.md`                        |
| An API endpoint or data model         | `memory/PRD.md`, `CHANGELOG.md`, `memory/test_credentials.md` (if auth-adjacent) |
| RBAC roles or permissions             | `memory/AUTHORIZATION_GUIDE.md`, `CHANGELOG.md`        |
| Auth / MFA / reauth / seed users      | `memory/test_credentials.md`, `CHANGELOG.md`, `SECURITY.md` (if policy changed) |
| Tenant / location scoping rules       | `memory/MULTI_TENANCY_ARCHITECTURE.md`, `CHANGELOG.md` |
| Any PHI surface (masking / crypto)    | `memory/HIPAA_COMPLIANCE.md`, `memory/PRIVACY_AND_RETENTION.md`, `CHANGELOG.md` |
| Deployment / ops / env config         | `README.md` (Running locally), `CHANGELOG.md`          |
| Dependencies                          | `backend/requirements.txt` / `frontend/package.json`, `CHANGELOG.md` |

The pull-request template enforces this checklist. The agent also treats
missing doc updates as a blocker during the finish step.

## Contributing & support
- Read [`CONTRIBUTING.md`](./CONTRIBUTING.md) before opening a PR.
- Report security issues privately per [`SECURITY.md`](./SECURITY.md).
- For feature proposals, open an issue referencing the P0/P1/P2 backlog
  section of `memory/PRD.md`.
