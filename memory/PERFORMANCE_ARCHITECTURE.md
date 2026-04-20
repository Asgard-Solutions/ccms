# CCMS Performance & Scalability Architecture

**Last updated:** 2026-04-19 (Performance pass on top of HIPAA-hardened build)

This document explains how Redis caching, primary/read-replica DB routing, and read-after-write consistency are wired into CCMS, and what the production migration path looks like.

---

## 1. Component overview

```
┌──────────────┐      reads/writes      ┌────────────────────┐
│ React client │ ───────────────────▶   │ FastAPI gateway     │
└──────────────┘                        │   ─ /api/identity   │
                                        │   ─ /api/patients   │
                                        │   ─ /api/appointments│
                                        │   ─ /api/notifications│
                                        │   ─ /api/audit-logs  │
                                        │   ─ /api/perf/stats  │
                                        └────────┬───────────┘
                                                 │
                ┌────────────────────────────────┼─────────────────────────────────┐
                ▼                                ▼                                 ▼
        ┌──────────────┐              ┌────────────────────┐             ┌────────────────┐
        │ Redis        │              │ MongoDB primary    │             │ MongoDB read   │
        │ - cache      │              │ (writes + read-    │             │ (read queries) │
        │ - rate-limit │              │  after-write)      │             │ secondary-     │
        │ - ephemeral  │              └────────────────────┘             │ preferred      │
        │   counters   │                                                 └────────────────┘
        └──────────────┘
```

In this preview environment both Mongo URLs point at the same single instance — the architectural split is what matters. Swapping `MONGO_READ_URL` to a replica-set URI (or to a Postgres read replica + a thin DB facade) will route reads correctly without touching call-sites.

---

## 2. Database routing (CQRS-lite)

Three explicit accessors live in `core/db.py`:

| Accessor                | When to use                                                  | Behaviour                                                  |
|-------------------------|--------------------------------------------------------------|------------------------------------------------------------|
| `get_db_write()`        | inserts, updates, deletes, conflict checks, login lookups   | always primary                                             |
| `get_db_read()`         | list endpoints, detail endpoints when no preceding write     | secondary-preferred                                        |
| `read_after_write_db()` | re-fetching the row you just wrote, before returning         | always primary (strong consistency)                        |
| `get_db()` *(alias)*    | legacy code paths (subscribers, seed scripts)                | currently equals `get_db_write()` — to be removed over time |

`routing_stats()` exposes process-local counters surfaced via `GET /api/perf/stats`.

### Routing matrix (per service)

| Endpoint                          | Read source                    | Write source        | Read-after-write |
|-----------------------------------|--------------------------------|---------------------|------------------|
| `POST /api/auth/login`            | write (auth-critical)          | write               | n/a              |
| `GET  /api/auth/providers`        | read (cached 300 s)            | n/a                 | n/a              |
| `POST /api/auth/users`            | write                          | write               | n/a              |
| `GET  /api/patients`              | **read** (cached 30 s, masked) | n/a                 | n/a              |
| `POST /api/patients`              | n/a                            | write               | n/a              |
| `GET  /api/patients/{id}`         | **read**                       | n/a                 | n/a              |
| `PUT  /api/patients/{id}`         | write                          | write               | **yes**          |
| `DELETE /api/patients/{id}`       | write                          | write               | n/a              |
| `GET  /api/patients/{id}/records` | **read**                       | n/a                 | n/a              |
| `POST /api/patients/{id}/records` | n/a                            | write               | n/a              |
| `GET  /api/appointments`          | **read** (cached 30 s)         | n/a                 | n/a              |
| `POST /api/appointments`          | write (conflict check)         | write               | n/a              |
| `PUT  /api/appointments/{id}`     | write (conflict check)         | write               | **yes**          |
| `POST /api/appointments/{id}/cancel` | n/a                         | write               | **yes**          |
| `GET /api/notifications`         | **read** (masked branch cached 15 s, unmask never cached) | n/a                 | n/a              |
| `GET /api/audit-logs`            | **read** (never cached)        | n/a                 | n/a              |

---

## 3. Redis use cases

### 3.1 Application cache (`core/cache.py`)
Implemented as `get_or_set(key, ttl, fetch)` + `invalidate_prefix(prefix)`.

| Key shape                                                  | TTL  | Why cached                              | Invalidated on                                       |
|------------------------------------------------------------|------|------------------------------------------|------------------------------------------------------|
| `identity:providers:active`                                | 300 s | Provider list barely changes; very hot   | user create / disable / enable / **PATCH role or status** |
| `patients:list:role={role}:search=:deleted={d}:masked=1`   | 30 s | Hot dashboard list; masked only          | patient create / update / delete                     |
| `appts:list:role={role}:provider=…:patient=…:status=…:from=…:to=…` | 30 s | Calendar / dashboard heavy           | appointment create / update / cancel                 |
| `notifications:list:event=…:patient=…:limit=…:masked=1`    | 15 s | Communication log read-heavy; masked only | new notification write (subscribers)                 |
| `dashboard:aggregates:user={id}` *(reserved, future)*      | 60 s | Per-user dashboard counts                | any patient/appointment write                        |

**Never cached:**
- Unmasked PHI (`?unmask=true`)
- Patient detail with break-glass `?reason=…` (always live)
- Audit log (forensic source-of-truth, must be live)
- `/api/patients/{id}/export` (right-to-access, always live)

### 3.2 Ephemeral / security state (`core/rate_limit.py`)
- `rl:login:{ip}:60:{bucket}` — sliding-window IP limiter, 30 logins / minute
- Bucket TTL = `window_seconds + 1`, automatic expiry; LRU eviction caps memory.
- The per-email brute-force lockout still lives in `db.login_attempts` so that a Redis flush cannot silently reset the audit-relevant counter.

### 3.3 Process-local fallback
Every Redis call goes through `core/redis_client.py::safe_call`. If Redis is unreachable:
- `cache.get_or_set` runs the fetch directly (cache miss, no error)
- `rate_limit.is_allowed` falls back to an in-process sliding window
- Logged once at WARN level, then silenced to avoid log floods
This keeps the application **available** under Redis degradation, which matches HIPAA availability expectations.

---

## 4. Cache-invalidation rules

We always invalidate by **prefix** so the keyspace stays simple. Today's rules:

| Write                                     | Prefix invalidated                                            |
|-------------------------------------------|---------------------------------------------------------------|
| `auth/users` create / disable / enable    | `identity:providers`                                          |
| `patients` create                         | `patients:`, `dashboard:`                                     |
| `patients/{id}` update                    | `patients:`, `patient:`, `appts:`                             |
| `patients/{id}` soft-delete               | `patients:`, `patient:`, `dashboard:`                         |
| `patients/{id}/records` create            | `patient:`                                                    |
| `appointments` create / update / cancel   | `appts:`, `dashboard:`                                        |

Prefix-scan uses Redis `SCAN` (cursor-based, non-blocking) — never `KEYS`.

---

## 5. Eventual consistency contract

Reads from `get_db_read()` may lag the primary by a few ms (today: 0; tomorrow on a real replica: low milliseconds). We deliberately accept this for list endpoints because the UI re-renders cheaply.

For workflows where **the user must immediately see their own write**, the routers re-fetch via `read_after_write_db()` before returning the response:

- `PUT /api/patients/{id}`  → response body comes from primary
- `PUT /api/appointments/{id}`  → response body comes from primary
- `POST /api/appointments/{id}/cancel`  → response body comes from primary

Conflict checks for appointment create/update **always** read from the primary (`get_db_write()`) so two staff members cannot double-book the same provider through replica lag.

---

## 6. Why `/api/notifications` is intentionally NOT Redis-cached today

It can be safely added later, but the masking layer applies a per-call decision (`unmask=true` requires admin + audit), and the current 200-row default page is already cheap on a covering index. Caching it would require either:
- a cache key that encodes the full filter set, OR
- caching only the masked branch (the hot path) and bypassing for unmask.

Caching the masked branch is straightforward and is the obvious next step if the notification log grows.

---

## 7. Operator visibility

`GET /api/perf/stats` (admin-only) returns JSON:
```json
{
  "cache": {"hits", "misses", "sets", "invalidations", "errors", "hit_ratio"},
  "db":    {"writes", "reads", "read_after_write", "read_ratio_overall"},
  "rate_limit": {"local_blocks"},
  "redis_alive": true|false
}
```

`POST /api/perf/cache/reset-stats` zeroes the JSON counters (per pod). No PHI is exposed.

`GET /api/perf/connection-info` (admin-only) returns the live Mongo topology
for the write and read clients:
```json
{
  "write": {"topology_type": "ReplicaSetWithPrimary", "nodes": [...], "read_preference": "Primary()"},
  "read":  {"topology_type": "ReplicaSetWithPrimary", "nodes": [...], "read_preference": "SecondaryPreferred(...)"},
  "same_client": false
}
```
Use this to verify a real replica-set deployment is actually routing reads to
a secondary (see §8.4).

`GET /api/metrics` (no auth — restrict at the ingress to your Prometheus
scrape subnet in production) returns the full Prometheus text-exposition
payload with these counters and histograms:
- `ccms_cache_hits_total`, `ccms_cache_misses_total`, `ccms_cache_sets_total`,
  `ccms_cache_invalidations_total`, `ccms_cache_errors_total`
- `ccms_db_queries_total{route="read|write|read_after_write"}`
- `ccms_rate_limit_blocks_total{source="redis|local"}`
- `ccms_redis_up` (Gauge, refreshed on each scrape)
- `ccms_http_request_duration_seconds{method,path_prefix,status_class}` histogram

None of these contain PHI — they are purely operational counters.

---

## 8. Production migration path

### 8.1 Mongo → Mongo replica set
1. Stand up a 3-node replica set in a HIPAA-eligible region with a signed BAA.
2. Set `MONGO_URL = "mongodb://primary,sec1,sec2/?replicaSet=rs0&authSource=…"`
3. Set `MONGO_READ_URL = "mongodb://primary,sec1,sec2/?replicaSet=rs0&readPreference=secondaryPreferred"`.
4. Restart. No application-code changes.

### 8.2 Mongo → PostgreSQL
1. Replace `motor` with `asyncpg` + `sqlalchemy[asyncio]>=2`.
2. Re-implement `get_db_write()` / `get_db_read()` to return `AsyncSession`s bound to `primary_engine` / `replica_engine`.
3. Re-implement `read_after_write_db()` to return a `primary_engine` session.
4. `core/cache.py`, `core/rate_limit.py`, `core/cache_keys.py`, every router's call signatures, and the cache invalidation rules **stay unchanged**.
5. The relational schema is already documented in each `models.py` — apply with Alembic.

### 8.3 Redis → managed Redis
1. Set `REDIS_URL` to the managed endpoint with TLS (`rediss://…`) + authentication.
2. If you cache anything that is or could become PHI in a future change, ensure the BAA covers Redis too (AWS ElastiCache supports HIPAA-eligible deployments).
3. Set `maxmemory-policy allkeys-lru` and a sensible memory budget; we already configure that locally.

### 8.4 Verification recipe after switching to a real replica set

```bash
# 1. Confirm topology
curl -s -b admin_cookies.txt $BASE/api/perf/connection-info | jq .
#    expect write.topology_type == "ReplicaSetWithPrimary"
#    expect read.topology_type  == "ReplicaSetWithPrimary"
#    expect write.read_preference == "Primary()"
#    expect read.read_preference starts with "SecondaryPreferred"

# 2. Reset counters + generate typical clinic traffic for 5 minutes
curl -s -b admin_cookies.txt -X POST $BASE/api/perf/cache/reset-stats

# 3. Read ratio should climb above 0.5 under a typical dashboard-heavy load
curl -s -b admin_cookies.txt $BASE/api/perf/stats | jq '.db.read_ratio_overall'
#    expect > 0.5 (lists and calendars dominate writes)

# 4. Point Prometheus at /api/metrics and alert when read_ratio drops
#    (indicates replica failure — reads are spilling back to primary).
```

---

## 9. Endpoint quick reference (this iteration)

| Endpoint                             | Purpose                                           |
|--------------------------------------|---------------------------------------------------|
| `PATCH /api/auth/users/{id}`         | Admin role/status change; invalidates providers   |
| `GET   /api/perf/stats`              | JSON counters + redis_alive                        |
| `GET   /api/perf/connection-info`    | Verify primary/replica host + read preference     |
| `POST  /api/perf/cache/reset-stats`  | Zero the in-process counters                       |
| `GET   /api/metrics`                 | Prometheus text exposition                         |
get; we already configure that locally.
