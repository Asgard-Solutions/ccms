# Data lifecycle & blue/green (shared MongoDB + Redis + disk)

CCMS is FastAPI + **MongoDB** (motor) + **Redis**, multi-tenant/HIPAA. Blue and
green share one MongoDB (`chiro_mongo`), one Redis (`chiro_redis`), and one
on-disk data dir (`/opt/chiropro/data` → `/app/data`).

## Seeding/migrations are AUTOMATIC (no separate step)
The backend's FastAPI `startup` hook (verified in `backend/server.py`) runs, in
order and **idempotently**, on every boot:
```
ensure_config()  →  create_indexes()  →  validate_startup()
  →  seed_tenancy → seed_identity → seed_authz → seed_compliance_ops
  →  seed_billing → seed_demo_clinic → seed_demo_billing  → cleanup_expired_exports
```
So when a new colour boots against the shared DB, it self-migrates (creates
indexes) and self-seeds. All operations are safe to re-run, and only one colour
boots at a time during a deploy, so this is blue/green-safe.

➡️ Therefore `MIGRATE_CMD` in `.env` stays **EMPTY**. Only set it if you add a
bespoke one-off data migration outside the startup seeders.

## Backward-compatibility still matters
Both colours may briefly run against the shared DB (old serving traffic while new
boots). Follow expand/contract for any *new* code that changes document shape:
- Add optional fields; dual-write during transitions; remove old fields only in a
  later release after the old colour is retired.
- New `create_indexes()` entries are additive and safe. Avoid adding a `unique`
  index on a field with existing duplicates — it will throw on boot.

## 🔐 DATA_ENCRYPTION_KEY is sacred
PHI is encrypted at rest with `DATA_ENCRYPTION_KEY`. It **must be identical for
blue and green** (it is — one shared `.env`) and **must never change** across
deploys, or existing encrypted records become unreadable. Store a secure backup
of this key separate from the VPS.

## Shared on-disk data
`/opt/chiropro/data` is bind-mounted into both backends:
- `data/exports/`  → generated CSV/PDF exports (downloaded via signed token; a
  user may request on one colour and download after a switch — hence shared).
- `data/storage/`  → local PHI storage backend (`STORAGE_ROOT`), used unless you
  configure Emergent object storage (`EMERGENT_LLM_KEY`) or S3.

## Backups before destructive change
Automatic **encrypted** backups run before every deploy (`scripts/backup.sh`,
invoked by `deploy.sh` when `BACKUP_BEFORE_DEPLOY=true`). They also run well as a
cron job. Archives (AES-256, `openssl enc -aes-256-cbc -pbkdf2`) land in
`/opt/chiropro/backups`, retained newest-`BACKUP_RETENTION`:
- `mongo-<ts>-<label>.archive.gz.enc`  — `mongodump --archive --gzip`
- `data-<ts>-<label>.tgz.enc`          — the `/opt/chiropro/data` dir

Run manually:  `sudo BACKUP_PASSPHRASE=… bash /opt/chiropro/scripts/backup.sh manual`
Nightly cron (as root):
```
15 2 * * *  cd /opt/chiropro && bash scripts/backup.sh nightly >> /var/log/chiropro-backup.log 2>&1
```

### Restore
```bash
# MongoDB (into the running chiro_mongo container)
openssl enc -d -aes-256-cbc -pbkdf2 -pass "pass:$BACKUP_PASSPHRASE" \
  -in mongo-<ts>-<label>.archive.gz.enc \
  | docker exec -i -e MURI="$MONGO_URL" chiro_mongo \
      sh -c 'mongorestore --uri "$MURI" --archive --gzip --drop'

# Data dir
openssl enc -d -aes-256-cbc -pbkdf2 -pass "pass:$BACKUP_PASSPHRASE" \
  -in data-<ts>-<label>.tgz.enc | tar xzf - -C /opt/chiropro
```
> ⚠️ `--drop` replaces collections — restore into a maintenance window, and keep a
> fresh backup first. Test restores periodically.

## Redis
Shared cache / rate-limit / job store; `redis_ping()` at startup is non-fatal (the
app boots even if Redis is down, losing distributed rate-limiting). Avoid
backward-incompatible key-format changes within a single deploy.
