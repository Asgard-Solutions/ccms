# Blue/Green Deployment — adjustpro.io (ChiroPro / CCMS)

Zero-downtime **blue/green deployment** for the ChiroPro chiropractic clinic
management app, running on the **same Hostinger VPS** as `SilvertreeSolutions.co`.

**Stack (detected from the repo):** FastAPI (Python) backend + CRA/CRACO React
frontend, multi-tenant / HIPAA-oriented, using **MongoDB** + **Redis**.

Each colour runs a **backend** container and a **frontend** container. The VPS's
**existing host Nginx** is the edge (Silvertree is never touched): it routes
`/api/*` to the active backend and everything else to the active frontend.
Deploys bring up the *idle* colour, health-check it, then flip Nginx atomically.
The old colour is kept (stopped) for instant rollback.

```
                    Internet (https://adjustpro.io)
                                │
                     ┌──────────▼───────────┐
                     │   Host Nginx (edge)   │  ← also serves SilvertreeSolutions.co
                     │ chiropro-active.conf  │  ← the switch (blue OR green)
                     └───┬───────────────┬───┘
              /api/* →   │               │   → / (SPA)
        ┌────────────────┼───────────────┼────────────────┐
        ▼                ▼               ▼                 ▼
 backend_blue :9001  frontend_blue :8081  backend_green :9002  frontend_green :8082
   (uvicorn :8001)     (nginx :3000)         ...idle...           ...idle...
        └───────────────┬────────────────────────────────┘
                        ▼
        shared:  chiro_mongo (MongoDB)  +  chiro_redis (Redis)
```

## Files

```
deploy/
  Dockerfile.backend             # FastAPI image (uvicorn single worker, :8001)
  Dockerfile.frontend            # CRA/CRACO build → static, served by nginx :3000
  .dockerignore
  docker-compose.infra.yml       # shared MongoDB + Redis + network
  .env.example                   # copy to /opt/chiropro/.env on the VPS
  nginx/
    adjustpro.io.conf   # host site: /api → backend, / → frontend
    chiropro-active.conf         # switchable upstreams (blue|green) — switch.sh
    spa.conf                     # in-container nginx for the frontend SPA + /healthz
  scripts/
    init-vps.sh  deploy.sh  switch.sh  rollback.sh  health-check.sh  migrate.sh
  db/README.md                   # Mongo/Redis blue-green safety (expand/contract)
.github/workflows/deploy.yml     # build backend+frontend → GHCR → ssh deploy → switch
docs/  SETUP.md  ARCHITECTURE.md  HEALTHCHECK.md
```

## Where these files go
Copy `deploy/` and `.github/workflows/deploy.yml` into the ChiroPro repo root
(the repo containing `backend/` and `frontend/`). Then run `init-vps.sh` once on
the VPS. Full walkthrough in **docs/SETUP.md**.

## Quick start
1. Ensure the backend exposes `/api/health` — see `docs/HEALTHCHECK.md`.
2. Confirm `frontend/src/api/client.js` reads `REACT_APP_BACKEND_URL`.
3. Commit `deploy/` + `.github/workflows/deploy.yml`.
4. On the VPS: `sudo bash deploy/scripts/init-vps.sh`, then edit `/opt/chiropro/.env`.
5. Add GitHub secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `GHCR_PAT` (+ optional `VPS_PORT`).
6. Push to `main` → first (blue) release deploys. Rollback: `deploy/scripts/rollback.sh`.

## ✅ Verified against the repo (Asgard-Solutions/ccms)
- Stack: FastAPI + MongoDB (motor) + Redis; frontend CRA **+ CRACO, built with
  yarn**; React 19.
- Backend health `GET /api/health` **already exists**; frontend already reads
  `REACT_APP_BACKEND_URL`. No app code changes required.
- **Seeding/migrations run automatically on backend startup** (idempotent), so
  `MIGRATE_CMD` stays empty.
- Backend hard-requires only 4 env vars at boot: `MONGO_URL`, `DB_NAME`,
  `JWT_SECRET`, `DATA_ENCRYPTION_KEY`. Redis + integrations are non-fatal.
- Backend runs a **single uvicorn worker** (its startup hook launches in-process
  background schedulers — Helcim worker + clearinghouse ack poller — which must
  not be duplicated across workers).
- App writes to local disk (`/app/data/exports`, `/app/data/storage`), so
  `/opt/chiropro/data` is bind-mounted into **both** colours' backends (shared +
  persistent). `init-vps.sh` creates & chowns it (uid 1000).

## 🔧 What you must still do
1. Fill `/opt/chiropro/.env`: strong `MONGO_ROOT_PASSWORD`/`REDIS_PASSWORD`, and
   **generate** `JWT_SECRET` + `DATA_ENCRYPTION_KEY` (`openssl rand -hex 32`).
   ⚠️ `DATA_ENCRYPTION_KEY` must stay constant forever (it encrypts PHI) — back it up.
2. Set optional integration keys you use (Stripe, Helcim, Twilio, Resend, Google,
   `EMERGENT_LLM_KEY` for AI + object storage) — all optional/non-fatal.
3. GitHub secrets + run `init-vps.sh` + issue TLS (see docs/SETUP.md).

---

## 📚 Table of contents

- [About the app (CCMS / ChiroPro)](#-about-the-app-ccms--chiropro)
- [Tech stack](#-tech-stack)
- [Repository layout](#-repository-layout)
- [Local development (Emergent preview / laptop)](#-local-development-emergent-preview--laptop)
- [Environment variables — full reference](#-environment-variables--full-reference)
- [API surface & health endpoints](#-api-surface--health-endpoints)
- [Data model & storage](#-data-model--storage)
- [Testing](#-testing)
- [Security, PHI & HIPAA posture](#-security-phi--hipaa-posture)
- [Observability & logs](#-observability--logs)
- [Backups & restore](#-backups--restore)
- [Blue/green day-2 operations](#-bluegreen-day-2-operations)
- [Troubleshooting](#-troubleshooting)
- [Release governance & the Clinical freeze](#-release-governance--the-clinical-freeze)
- [Contributing & code style](#-contributing--code-style)
- [Support & further reading](#-support--further-reading)

---

## 🩺 About the app (CCMS / ChiroPro)

CCMS ("ChiroPro" internally) is a **multi-tenant, HIPAA-oriented clinic
management system** for chiropractic practices. It covers the clinical, billing
and operational surface a small–mid clinic needs:

- **Patient charting** — demographics, clinical timeline, encounters, SOAP
  notes, uploads, and a redesigned Patient Profile > Clinical page (currently
  under a strict release freeze — see the [governance section](#-release-governance--the-clinical-freeze)).
- **Scheduling & front-desk** — appointments, check-in, insurance capture.
- **Billing** — line items, superbills, exports, clearinghouse ack polling
  and payment worker (Helcim) running as in-process background schedulers.
- **Reports & data quality** — clinic-level rollups, audit trails, PHI masking.
- **AI-assisted workflows** — powered via the Emergent LLM Key
  (Anthropic / OpenAI / Gemini) for chart summaries, drafting and search.
- **Multi-tenant isolation** — per-tenant scoping enforced across storage,
  queries, exports, and audit.

The app is currently deployed as:
1. An **Emergent preview** environment (`REACT_APP_BACKEND_URL` from `frontend/.env`), used for iterative development and QA.
2. A **VPS blue/green production** target at `https://adjustpro.io`, using the
   `deploy/` tooling described above.

---

## 🧱 Tech stack

| Layer            | Technology                                                   |
|------------------|--------------------------------------------------------------|
| Backend          | Python 3, **FastAPI** (`uvicorn` single worker)              |
| Async DB driver  | **Motor** (MongoDB async)                                    |
| DB               | **MongoDB**                                                  |
| Cache / queue    | **Redis** (optional / non-fatal)                             |
| Auth             | **JWT** (`PyJWT`), password hashing via `bcrypt`, MFA via `pyotp` |
| PHI encryption   | **`DATA_ENCRYPTION_KEY`** (must never change post-first-write) |
| AI / LLM         | Emergent LLM Key → Anthropic, OpenAI, Gemini (`emergentintegrations`) |
| Payments         | **Stripe**, **Helcim** (in-process worker)                   |
| Clearinghouse    | Ack poller (in-process scheduler)                            |
| Comms            | **Twilio** (SMS), **Resend** (email)                         |
| Object storage   | **Emergent Object Storage** (via Emergent LLM Key) + local `/app/data/storage` fallback |
| Frontend         | **React 19 + CRA / CRACO**, TailwindCSS, shadcn/ui, lucide-react, sonner |
| Routing          | `react-router-dom` v7                                        |
| State/forms      | `react-hook-form`, `zod`, `@hookform/resolvers`              |
| Charts           | `recharts`                                                   |
| Build            | `yarn` (see `packageManager` pin in `frontend/package.json`) |
| Testing          | **Pytest** (backend), **Jest** (frontend, via `craco test`)  |
| Deployment       | Docker + GitHub Actions + Nginx (blue/green — see top of file) |

> The backend **must** run a single uvicorn worker because it launches two
> in-process background schedulers on startup (Helcim payment worker + clearinghouse
> ack poller). Multiple workers would duplicate side-effects.

---

## 🗂️ Repository layout

```
/
├── backend/                 # FastAPI app
│   ├── server.py            # ASGI entrypoint (mounts /api router, startup hooks)
│   ├── routes/              # API routers (grouped by domain)
│   ├── services/            # Domain services (billing, clinical, auth, etc.)
│   ├── models/              # Pydantic + Mongo document models
│   ├── scripts/             # Ops scripts (perf governance, seeders, migrations)
│   │   ├── _perf_gov_lib.py
│   │   └── run_clinical_perf.py    # NOTE: contains legacy re-exports — do NOT
│   │                                  refactor until the Clinical freeze is lifted
│   ├── tests/               # Pytest suites (288+ tests)
│   └── requirements.txt
│
├── frontend/                # React 19 + CRA/CRACO app
│   ├── src/
│   │   ├── api/client.js    # Reads process.env.REACT_APP_BACKEND_URL
│   │   ├── components/ui/   # shadcn/ui components (edit here, not upstream)
│   │   ├── pages/clinical/  # FROZEN Clinical redesign surface
│   │   └── ...
│   ├── public/
│   └── package.json
│
├── deploy/                  # Blue/green deploy tooling (see top of this README)
├── docs/                    # ARCHITECTURE.md, SETUP.md, HEALTHCHECK.md
├── memory/                  # Release governance, PRD, roadmap, gate status
├── test_reports/            # Per-iteration test result JSONs
└── .github/workflows/       # CI (build + deploy)
```

Backend routers are always mounted under `/api/*` so the Nginx / preview
ingress can route them correctly.

---

## 💻 Local development (Emergent preview / laptop)

The Emergent preview environment already runs everything under supervisor. You
usually do not need to start anything by hand.

### Preview environment (what most contributors use)
```bash
# Check services
sudo supervisorctl status

# Restart after .env changes or new dependencies
sudo supervisorctl restart backend
sudo supervisorctl restart frontend

# Tail logs
tail -f /var/log/supervisor/backend.err.log
tail -f /var/log/supervisor/frontend.err.log
```

Hot reload is enabled for both backend and frontend — code edits do **not**
require a restart. Restart only for:
- Changes to `.env` files
- Installing new Python or JS dependencies

### Running locally on your laptop
```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8001 --reload

# Frontend (in a second terminal)
cd frontend
yarn install
yarn start        # CRA/CRACO dev server on :3000
```

Point `frontend/.env`'s `REACT_APP_BACKEND_URL` at your backend origin (must be
reachable from the browser). `MONGO_URL` and `DB_NAME` are required in
`backend/.env`.

### Installing new dependencies
- **Python**: `pip install <pkg> && pip freeze > backend/requirements.txt`
- **JS**: `cd frontend && yarn add <pkg>` (never edit `package.json` by hand)

---

## 🔐 Environment variables — full reference

Split across two files. Never commit real secrets.

### `backend/.env`
| Variable                 | Required? | Notes                                                                 |
|--------------------------|-----------|-----------------------------------------------------------------------|
| `MONGO_URL`              | ✅        | Full connection string, e.g. `mongodb://user:pass@host:27017`         |
| `DB_NAME`                | ✅        | Do not change once data exists                                        |
| `JWT_SECRET`             | ✅        | Generate with `openssl rand -hex 32`                                  |
| `DATA_ENCRYPTION_KEY`    | ✅        | 32-byte key. **Immutable** after first PHI write. Back it up securely |
| `REDIS_URL`              | ⚪ opt.    | Non-fatal if missing (rate-limit / cache degrade to in-memory)        |
| `EMERGENT_LLM_KEY`       | ⚪ opt.    | Enables AI features + Emergent Object Storage                         |
| `STRIPE_SECRET_KEY`      | ⚪ opt.    | Billing (Stripe path)                                                 |
| `HELCIM_*`               | ⚪ opt.    | Helcim payment worker credentials                                     |
| `TWILIO_*`               | ⚪ opt.    | SMS                                                                   |
| `RESEND_API_KEY`         | ⚪ opt.    | Transactional email                                                   |
| `GOOGLE_*`               | ⚪ opt.    | Emergent-managed Google Auth (if used)                                |
| `CORS_ORIGINS`           | ⚪ opt.    | Comma-separated allowed origins                                       |
| `PUBLIC_URL`             | ⚪ opt.    | Used by smoke tests + email links                                     |

### `frontend/.env`
| Variable                 | Required? | Notes                                                                 |
|--------------------------|-----------|-----------------------------------------------------------------------|
| `REACT_APP_BACKEND_URL`  | ✅        | Inlined at **build time** by CRA/CRACO. In prod CI it is `https://adjustpro.io` |

> ⚠️ Never delete the protected keys (`MONGO_URL`, `DB_NAME`, `REACT_APP_BACKEND_URL`)
> from `.env` files. Never add default fallbacks in code — fail fast if a var is missing.

Reference `deploy/.env.example` (produced by `init-vps.sh`) for the full VPS
`.env` (Mongo/Redis creds, backup retention, etc.).

---

## 🔌 API surface & health endpoints

- All API routes are mounted under **`/api/*`**.
- OpenAPI docs (when enabled): `GET /api/docs`
- Liveness/health probe: `GET /api/health` → `{"status":"healthy"}`
  - Backing indexes + seeders run **before** the first 200 response, so a
    healthy backend implies Mongo is reachable and the schema is ready.
- Frontend liveness (inside the SPA container only): `GET /healthz`

Selected authenticated routes (see backend/routes/ for the full list):
- `PATCH /api/auth/me/preferences` — durable UI config per user (used by the
  Clinical page)
- `POST  /api/telemetry/ui-event`   — PHI-safe UI telemetry ingress

> ⚠️ All backend routes **must** start with `/api`. Any new route without that
> prefix will not be reachable through the production Nginx or the Emergent preview ingress.

Curl a route from a shell:
```bash
API_URL=$(grep REACT_APP_BACKEND_URL frontend/.env | cut -d= -f2)
curl -s "$API_URL/api/health"
```

---

## 🗃️ Data model & storage

- **MongoDB collections** (selected):
  - `identity.users` — auth + `preferences` (durable UI config)
  - `clinical_timeline_events`, `clinical_encounters` — chart data
  - Audit / access-log collections for HIPAA trail
- **On-disk state** (persisted, bind-mounted into both blue/green containers):
  - `/app/data/exports`  — generated PDF/CSV exports
  - `/app/data/storage`  — locally-stored PHI blobs (fallback if object storage
    isn't configured)
- **ObjectId hygiene** — all document models extend a `BaseDocument` that maps
  `_id → id` via a `PyObjectId` annotated type. Raw Mongo dicts are **never**
  returned from API endpoints. Datetimes use `datetime.now(timezone.utc)`.

---

## 🧪 Testing

The project is deeply tested (**405+ green tests** at the time of the Clinical
freeze — 288 Pytest + 117 Jest).

```bash
# Backend
cd backend
pytest -q                           # full suite
pytest -q tests/test_perf_cache_rate.py   # a single file

# Frontend
cd frontend
yarn test --watchAll=false          # CI mode

# End-to-end smoke via the preview URL
API_URL=$(grep REACT_APP_BACKEND_URL frontend/.env | cut -d= -f2)
curl -s "$API_URL/api/health"
```

Test reports produced by the Emergent testing agent are stored per iteration at
`test_reports/iteration_<n>.json`. Do not delete these — they are the running
regression trail.

---

## 🛡️ Security, PHI & HIPAA posture

- **Encryption at rest for PHI** via `DATA_ENCRYPTION_KEY`. This key is
  identical across blue and green colours (one shared `.env` on the VPS) and
  **must** never change post-first-write — old records become unrecoverable
  otherwise. Back it up separately (KMS, offline vault, etc.).
- **JWT auth** with bcrypt password hashing; MFA via `pyotp`; brute-force
  protection.
- **Per-tenant scoping** at the storage/query/audit layer.
- **PHI masking** for lower-privilege roles and in telemetry payloads.
- **Audit trail** for access to sensitive data (append-only).
- **Security headers** validated by tests (`backend/tests/test_iteration11_security_headers.py`).
- **Rate limiting / operational security** covered by
  `test_iteration9_operational_security.py` + related suites.
- **Non-fatal integrations** — Redis, LLM keys, Twilio, Resend, etc., degrade
  gracefully and never crash the boot.

If you are working on **anything** touching auth (login, registration, JWT,
password reset, admin seeding, MFA, OAuth), you **must** call the Emergent
`integration_playbook_expert_v2` for the current auth playbook before writing
code. This is a project-wide rule.

---

## 📈 Observability & logs

| What                        | Where                                              |
|-----------------------------|----------------------------------------------------|
| Backend logs (preview)      | `/var/log/supervisor/backend.*.log`                |
| Frontend logs (preview)     | `/var/log/supervisor/frontend.*.log`               |
| Backend logs (VPS)          | `docker logs -f chiropro_backend_<color>`          |
| Frontend logs (VPS)         | `docker logs -f chiropro_frontend_<color>`         |
| Nginx access/error (VPS)    | `/var/log/nginx/*.log`                             |
| Active colour marker (VPS)  | `/opt/chiropro/.active_color`                      |
| Previous colour (rollback)  | `/opt/chiropro/.previous_color`                    |
| Prometheus client metrics   | Backend exposes `prometheus_client` — wire a scrape target if needed |

---

## 💾 Backups & restore

- **Automatic pre-deploy backup** — `deploy/scripts/backup.sh` runs before every
  deploy and writes AES-256 encrypted `mongodump` + `/data` archives to
  `/opt/chiropro/backups`.
- **Retention** — controlled by `BACKUP_RETENTION` in `/opt/chiropro/.env`.
- **Manual backup**:
  ```bash
  sudo bash /opt/chiropro/scripts/backup.sh manual
  ```
- **Cron** — `backup.sh` is cron-friendly; wire it to your preferred schedule.
- **Restore** — full procedure in `deploy/db/README.md` (expand/contract-safe
  ordering for blue/green shared datastores).

---

## 🚦 Blue/green day-2 operations

Quick reference for the most common ops tasks on the VPS:

```bash
# Which colour is live?
cat /opt/chiropro/.active_color

# What's the rollback target?
cat /opt/chiropro/.previous_color

# Tail live backend logs
docker logs -f "chiropro_backend_$(cat /opt/chiropro/.active_color)"

# Manual health probe against the live edge
curl -sSf https://adjustpro.io/api/health

# Manual smoke test
sudo bash /opt/chiropro/scripts/smoke-test.sh https://adjustpro.io "/api/health,/api/,/"

# Instant rollback to previous colour
sudo bash /opt/chiropro/scripts/rollback.sh

# Redeploy a specific tag (re-run the workflow with backend_tag/frontend_tag)
#   GitHub → Actions → Deploy ChiroPro → Run workflow
```

**Cutover invariants** (see `docs/ARCHITECTURE.md` for full detail):
1. Deploy always targets the **idle** colour.
2. Both tiers must pass health checks before Nginx flips.
3. Post-switch smoke test failure triggers **automatic rollback** to the
   previous colour.
4. The old colour is stopped, not removed — rollback is `docker start` +
   `nginx reload`, i.e. seconds.

---

## 🧰 Troubleshooting

| Symptom                                                | Likely cause / fix                                                           |
|--------------------------------------------------------|------------------------------------------------------------------------------|
| Backend crashes on boot with `libmagic` error          | `sudo apt-get install -y libmagic1 libmagic-dev libmagic-mgc` then restart backend |
| `PATCH /api/auth/me/preferences` returns 401           | Preview session expired — sign in again; verify `JWT_SECRET` matches deploy  |
| Frontend build calls `http://localhost:8001`           | `REACT_APP_BACKEND_URL` was missing at **build** time — rebuild with the CI env |
| New route returns 404 through the ingress              | Route not prefixed with `/api` — add the prefix                              |
| Redis logs `FATAL … redis-server` in supervisor        | Redis binary not installed on the sandbox — safe to ignore in preview; VPS uses the `chiro_redis` container |
| Deploy fails on health check                           | Check `docker logs chiropro_backend_<idle>` — usually a missing/wrong env var |
| PHI decryption errors after a deploy                   | `DATA_ENCRYPTION_KEY` was rotated — restore original key immediately         |
| Stale UI after switch                                  | Hard-reload; ensure the SPA build artefacts were re-uploaded                 |
| Auto-rollback fired                                    | `smoke-test.sh` failed — check `/var/log/nginx/error.log` and previous colour logs |

For anything that repeats twice, use Emergent's `troubleshoot_agent` for a
read-only RCA rather than guessing.

---

## 📜 Release governance & the Clinical freeze

The Patient Profile > Clinical redesign is currently under a **strict code
freeze** governed by six external release gates:

| Gate | Purpose                              | Status                        |
|------|--------------------------------------|-------------------------------|
| G1   | Stakeholder UAT sign-off             | Awaiting external sign-off    |
| G2   | Production-build perf measurement    | Awaiting external run         |
| G3   | Production rollback rehearsal        | Awaiting external run         |
| G4   | (Documented, closed internally)      | ✅                            |
| G5   | Authorised 25-shot screenshot set    | Awaiting external capture     |
| G6   | Staged internal rollout              | Blocked until G1/G2/G3 close  |

Full execution runbook: **`memory/CLINICAL_EXTERNAL_GATE_ACTION_PLAN.md`**.
Live status tracker: **`memory/CLINICAL_RELEASE_GATE_STATUS.md`**.

During the freeze:
- No new features, contract changes, telemetry additions, preference schema
  changes, or UI edits on the Clinical surface.
- Only **verified defects** may result in code changes.
- The performance-governance compatibility re-exports in `backend/scripts/`
  are pinned in place until the freeze is lifted; the removal ticket lives at
  **`memory/TICKET_REMOVE_PERF_EXPORTS.md`**.

---

## 🤝 Contributing & code style

- **Editing existing files** > creating new ones. Match the surrounding style.
- **Python**: `black`, `isort`, `flake8`, `mypy` are all pinned in `requirements.txt`.
- **JavaScript**: ESLint v9 flat config; React 19 rules; Tailwind for styling.
  Use shadcn/ui components from `frontend/src/components/ui/` — do not
  re-implement primitives.
- Every interactive UI element and every element showing critical info must
  have a stable **`data-testid`** (kebab-case, describes function, unique).
- Backend routes: always `/api/*`.
- Env vars only — no hardcoded URLs, ports, or secrets in code.
- MongoDB: never return raw docs; always go through `BaseDocument.from_mongo()`
  / `to_mongo()`. Use `datetime.now(timezone.utc)` — never `datetime.utcnow()`.
- Third-party integrations: **always** go through
  `integration_playbook_expert_v2` — do not roll your own SDK code.

---

## 📎 Support & further reading

- **`docs/ARCHITECTURE.md`** — end-to-end architecture and cutover invariants
- **`docs/SETUP.md`** — one-time VPS + DNS + GitHub setup walkthrough
- **`docs/HEALTHCHECK.md`** — health endpoint contracts
- **`deploy/db/README.md`** — Mongo/Redis backup + restore + expand/contract
- **`memory/PRD.md`** — product requirements (living doc)
- **`memory/ROADMAP.md`** — P0/P1/P2 backlog
- **`memory/CHANGELOG.md`** — dated record of what shipped
- **`memory/CLINICAL_EXTERNAL_GATE_ACTION_PLAN.md`** — G1–G6 execution runbook
- **`memory/CLINICAL_RELEASE_GATE_STATUS.md`** — live gate tracker
- **`memory/TICKET_REMOVE_PERF_EXPORTS.md`** — post-freeze cleanup ticket

Bug reports, security disclosures and refund/support requests should be routed
through the Asgard-Solutions internal channels — do not open PHI-bearing issues
in a public tracker.
