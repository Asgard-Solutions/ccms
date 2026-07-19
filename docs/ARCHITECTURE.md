# Architecture — how it works

## Components
- **Host Nginx (edge):** the VPS's existing Nginx. Terminates TLS for
  `adjustpro.io`, routes `/api/*` → active **backend** and everything
  else → active **frontend**. Also serves SilvertreeSolutions.co from its own
  separate `server` block — untouched by this setup.
- **Backend (FastAPI) containers:** `chiropro_backend_blue` (127.0.0.1:9001) and
  `chiropro_backend_green` (127.0.0.1:9002), each running a **single** uvicorn
  worker on internal `:8001`. Single worker on purpose: the app's startup hook
  starts in-process background schedulers (Helcim payment worker + clearinghouse
  ack poller) that must not be duplicated. Both mount the shared data dir
  `/opt/chiropro/data → /app/data` (exports + local PHI storage).
- **Frontend (static SPA) containers:** `chiropro_frontend_blue`
  (127.0.0.1:8081) and `..._green` (127.0.0.1:8082), a tiny Nginx serving the
  CRA/CRACO build on internal `:3000`.
- **Shared MongoDB (`chiro_mongo`)** + **Redis (`chiro_redis`)** on the private
  `chiro_net` Docker network, persisted to named volumes.

A "colour" = the backend **and** frontend container of that colour, deployed and
switched together.

## The switch
The single source of truth for Nginx is one file:
```
/etc/nginx/conf.d/chiropro-active.conf
  upstream chiropro_frontend { server 127.0.0.1:8081|8082; }
  upstream chiropro_backend  { server 127.0.0.1:9001|9002; }
```
`switch.sh <colour>` rewrites both upstreams, runs `nginx -t` (aborts if
invalid), then `systemctl reload nginx` (graceful — no dropped connections).

State on disk:
- `/opt/chiropro/.active_color`   → current live colour
- `/opt/chiropro/.previous_color` → rollback target

## Deploy sequence (deploy.sh)
```
1. docker pull <new backend> and <new frontend>
2. ensure chiro_net + chiro_mongo + chiro_redis are up
3. ENCRYPTED backup of shared Mongo + /data (backup.sh)      ← before any change
4. migrate/seed the shared DB (auto on backend boot)
5. start IDLE backend  (:900x)  and IDLE frontend (:808x)
6. health-check IDLE backend /api/health AND frontend /healthz
      └─ if either unhealthy: remove idle containers, ABORT (live colour intact)
7. switch.sh <idle>                                          ← atomic cutover
8. record .active_color / .previous_color
9. SMOKE TEST through the live edge (smoke-test.sh)
      └─ if it fails: AUTO-ROLLBACK to previous colour, ABORT
10. stop (keep) OLD backend + frontend                       ← rollback target
```
Users are never routed to a broken release: step 7 requires step 6 to pass, and
step 9 re-verifies end-to-end through TLS + Nginx and auto-reverts on failure.

### Backups
`backup.sh` writes AES-256 encrypted `mongodump` + `/data` archives to
`/opt/chiropro/backups` (retention `BACKUP_RETENTION`). Runs pre-deploy and is
cron-friendly. Restore steps: `deploy/db/README.md`.

### Smoke test
`smoke-test.sh` curls unauthenticated paths (`/api/health`, `/api/`, `/`) at the
public origin using `--resolve` to force this host, validating TLS termination,
`/api`→backend + `/`→frontend routing, and app liveness after the cutover.

## Rollback (rollback.sh)
Old colour's containers are stopped, not removed: `docker start` both →
health-check → `switch.sh` back. Seconds, not minutes.

## Shared datastores & safety
Both colours share one Mongo + one Redis + one on-disk data dir
(`/opt/chiropro/data`, bind-mounted into both backends for exports + local PHI
storage). Seeding/index-creation runs automatically and idempotently on backend
startup, so a new colour self-migrates before it is switched in; a boot failure
aborts the deploy with the old version fully live.

### 🔐 Encryption key invariant
PHI is encrypted with `DATA_ENCRYPTION_KEY`. It is identical for blue and green
(one shared `.env`) and must never change across deploys, or existing encrypted
data becomes unrecoverable.

## Frontend ↔ backend URL
CRA/CRACO inlines `REACT_APP_BACKEND_URL` at **build time**. CI sets it to
`https://adjustpro.io`, so the SPA calls `…/api/...` on the same origin
and host Nginx routes those to the active backend. This means the frontend image
is environment-specific (fine — one origin here).

## Coexistence with SilvertreeSolutions.co
- Different domain → different Nginx `server` block.
- Different ports (8081/8082/9001/9002) → no collision.
- Own Docker network + Mongo/Redis volumes.
- Shared only: host Nginx (edge) + Docker daemon — both multi-tenant by design.

## Ports
| Purpose               | Bind                              |
|-----------------------|-----------------------------------|
| Public HTTP/HTTPS     | Host Nginx :80/:443               |
| Frontend blue / green | 127.0.0.1:8081 / :8082 → :3000    |
| Backend  blue / green | 127.0.0.1:9001 / :9002 → :8001    |
| MongoDB (admin only)  | 127.0.0.1:27017                   |
| Redis (admin only)    | 127.0.0.1:6379                    |
