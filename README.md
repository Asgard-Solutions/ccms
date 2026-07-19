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
