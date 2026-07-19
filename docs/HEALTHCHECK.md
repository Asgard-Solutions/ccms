# Health endpoints for the blue/green deploy

The deploy switches traffic only after the idle colour passes both checks:

| Tier     | URL (on the idle colour)             | Status |
|----------|--------------------------------------|--------|
| Backend  | `http://127.0.0.1:<port>/api/health` | ✅ **Already exists** in `backend/server.py` → returns `{"status":"healthy"}` |
| Frontend | `http://127.0.0.1:<port>/healthz`    | ✅ Provided by the frontend container's Nginx (`deploy/nginx/spa.conf`) |

**Nothing to add** — both endpoints already exist. Verified in the repo:
- `backend/server.py` defines `@api_router.get("/health")` under the `/api` prefix.
- `frontend/src/api/client.js` builds the API base from
  `process.env.REACT_APP_BACKEND_URL` (so the CI build-arg name is correct).

## Optional: make the backend health check "deep"
The current `/api/health` is a fast liveness probe (always 200 once the process is
up). Note the app's startup hook runs `create_indexes()` + all seeders *before*
serving, so a 200 already implies Mongo is reachable and seeded. If you want the
probe to also fail when Redis is down, you could extend it — but Redis is
intentionally non-fatal in this app, so keeping the liveness probe as-is is
recommended (don't block a deploy on an optional dependency).

## If you ever change the paths
Set `BACKEND_HEALTH_PATH` / `FRONTEND_HEALTH_PATH` in `/opt/chiropro/.env`.
