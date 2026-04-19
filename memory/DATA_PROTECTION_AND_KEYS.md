# CCMS Data Protection & Key Management

**Last updated:** 2026-02-18 (Data protection & secure configuration hardening phase)
**Complements:** `HIPAA_COMPLIANCE.md`, `ACCESS_CONTROL_AND_AUDIT.md`, `PRIVACY_AND_RETENTION.md`, `COMPLIANCE_BASELINE.md`.

> Encryption is a **technical safeguard**, not a compliance certification.
> CCMS is not independently audited. This document describes what the
> application encrypts, how keys are accessed, and — critically — which
> steps the deploying organisation still has to complete under its
> production infrastructure.

---

## 1. Sensitive-data inventory

| Data | Representation in app | At rest | In transit | Logging exposure |
|---|---|---|---|---|
| **Patient free-text PHI** — `date_of_birth`, `address`, `emergency_contact`, `notes` | `patients.*` | **AES-256-GCM** field-level (`enc:v1:` prefix) | TLS (ingress) | Never logged; masked in list/detail views; audit metadata carries only enums + ids + counts |
| **Medical record narrative** — `description`, `diagnosis`, `treatment` | `medical_records.*` | **AES-256-GCM** field-level | TLS | Never logged |
| **Appointment metadata** — `reason`, `notes` | `appointments.*` | Free-text stored plain today; considered low-PHI (scheduling reason). Add to encrypted set if your deployment deems it sensitive. | TLS | Never logged in audit rows |
| **Password** | `users.password_hash` + `password_history[]` | **bcrypt** (cost 12) | — | Never logged |
| **Password reset token** | `password_reset_tokens.token_hash` | **SHA-256** of the raw token; raw token never persisted | Sent once in the reset link (in prod: email) | `dev_token` field is only enabled in non-prod builds |
| **MFA secret (TOTP)** | `users.mfa_secret` | Plaintext today — **should be KMS-wrapped in production**; flagged in backlog. The secret is indexed-by-user, never broadcast. | TLS | Never logged |
| **MFA backup codes** | `users.mfa_backup_codes[]` | Plaintext today; each entry consumed on use. Same KMS-wrap backlog applies. | — | Never logged |
| **Session cookies** | `access_token`, `refresh_token` | JWT HS256 signed with `JWT_SECRET`. `HttpOnly + Secure + SameSite=None + Path=/`. | TLS | Never logged |
| **Session metadata** | `users.session_epoch`, `last_login_at` | Plain counters / timestamps | TLS | Emitted in audit rows; no secret material |
| **Notification body** | `notifications.body` | Stored plain (mock integration). In a real Twilio/Resend integration the body must either be sensitive-free or encrypted at rest. | TLS | Masked in the admin UI unless `unmask=true` is passed with a reason (audited) |
| **Audit log** | `audit_logs.*` | Structural integrity prioritised over confidentiality; rows never contain PHI values. | TLS | Meta-audit row on each read + CSV export |
| **Consent records** | `consent_records.*` | Plain (policy_type, policy_version, action, ip, ua). No PHI. | TLS | Emits audit row |
| **Privacy request notes** | `privacy_requests.notes`, `response_notes` | Plain text. **Policy: no PHI here** — admin UI disclaims this. | TLS | Visible to admins only |
| **Data encryption key material** | `core/key_manager.py` loads from `DATA_ENCRYPTION_KEY` (base64, 32 bytes) | Env var today. KMS/HSM in production (see §4). | — | Never logged; `describe()` only returns provider + version + lengths |
| **JWT signing secret** | Read directly in `core/security.py` via `os.environ["JWT_SECRET"]` | Env var today. Should be vault-backed in production. | — | Never logged; length-only in admin diagnostics |

---

## 2. What is encrypted

- `patients.date_of_birth` (new)
- `patients.address`
- `patients.emergency_contact`
- `patients.notes`
- `medical_records.description`
- `medical_records.diagnosis`
- `medical_records.treatment`

Ciphertext format:
```
enc:<version>:<base64url(nonce || ciphertext_and_tag)>
```

The leading `enc:` prefix is the marker `core/crypto.py::decrypt_text` uses
to distinguish encrypted values from legacy plaintext. Legacy plaintext is
passed through untouched so a rollout does not require a data migration.

### Forward rotation
Each ciphertext embeds the key version that produced it (`enc:v1:…`). To
rotate keys in-place:

1. Generate a new 32-byte key, base64-encode it.
2. Keep the old key in `EXTRA_DATA_KEYS` as JSON: `{"v1":"<old-b64>"}`.
3. Set `DATA_ENCRYPTION_KEY` to the new key and `DATA_KEY_VERSION=v2`.
4. New writes emit `enc:v2:…`; old reads continue to decrypt against `v1`.
5. Eventually re-encrypt all rows (background job) and retire `v1`.

---

## 3. What is NOT encrypted today

| Field | Why | Mitigation |
|---|---|---|
| `users.mfa_secret` + `mfa_backup_codes` | Would need a KMS-wrap pattern to round-trip cleanly with TOTP verification | Admin MFA reset endpoint (`/users/{id}/mfa/reset`) invalidates instantly on compromise; tracked in backlog |
| `notifications.body` | Mock integration; real body delivery is out-of-band via Twilio/Resend | Mask-by-default in admin UI; unmask is audited |
| `privacy_requests.notes` / `response_notes` | Admin operational workflow; policy forbids PHI there | Disclaimer in the /privacy admin page + audit log |
| `audit_logs` rows | Integrity is the priority; rows deliberately never contain PHI values | Tamper-resistance via DB-layer validators is on the P0 backlog |
| `appointments.reason` / `appointments.notes` | Considered scheduling metadata; add to `APPT_ENCRYPTED` if your deployment deems it PHI | Masked by default, unmask-with-reason is audited |

---

## 4. Key management

### 4.1 Abstraction (this repo)
- Module: `core/key_manager.py`
- Exposes: `provider()`, `current_version()`, `get_key(version)`, `is_enabled()`, `describe()`.
- Guarantees: every code path in the app obtains key material through this
  module. `core/crypto.py` is the only consumer.
- Behaviour today: reads `DATA_ENCRYPTION_KEY` (base64, 32 bytes) from env,
  caches once via `lru_cache`.
- `describe()` returns metadata only; it can safely be surfaced in the admin
  `/security-config` page and in Prometheus.

### 4.2 How production should replace this
Before a production deploy:

1. **Set `KMS_PROVIDER`** to `aws_kms` / `azure_kv` / `vault`.
2. **Implement the provider fetch** inside `core/key_manager.py::_load_active_key`:
   - *AWS KMS*: symmetric data-key wrapping — call `kms.GenerateDataKey` once
     per boot, cache the plaintext data key in memory, throw away the
     ciphertext.
   - *Azure Key Vault*: use `azure-identity` + `azure-keyvault-keys` for
     `wrapKey` / `unwrapKey`.
   - *HashiCorp Vault*: use the Transit secrets engine (`transit/decrypt`).
3. **Never** persist plaintext keys to disk.
4. **Rotate** the underlying CMK on the provider's cadence (90 / 180 days is
   typical). Use the `EXTRA_DATA_KEYS` + `DATA_KEY_VERSION` mechanism to
   preserve access to existing ciphertexts during the rotation window.
5. **Access policy**: grant the running pod's service-account the minimum
   set of permissions (Decrypt / UnwrapKey only — no list / delete).

No router / handler needs to change when this swap happens — the call sites
already ask `core.key_manager.get_key()`.

---

## 5. Secrets handling

### 5.1 Config module
`core/config.py` declares:

- `REQUIRED` — `MONGO_URL`, `DB_NAME`, `JWT_SECRET`, `DATA_ENCRYPTION_KEY`.
  Missing any of these fails the startup lifespan hook with a clear error.
- `RECOMMENDED` — `FRONTEND_URL`, `REDIS_URL`, `ADMIN_PASSWORD`, `MFA_ISSUER`.
  Missing these is surfaced as "Gaps for production go-live" in the admin
  Security Config page but does not refuse to boot.
- `SECRET_MIN_LENGTH` — weak-secret detector for `JWT_SECRET` and
  `DATA_ENCRYPTION_KEY` (<32 chars).

### 5.2 Fail-fast
`server.py::on_startup` calls `ensure_required()` before opening DB indexes
or accepting traffic. A misconfigured deploy raises immediately; it never
serves a request in a broken state.

### 5.3 Masking in logs & diagnostics
- `config.describe()` returns lengths, booleans, and masked prefixes only.
- `config.mask_secret(value, keep=4)` is the one helper used anywhere a
  secret would otherwise reach a log or UI — renders `abcd…(32)`.
- The admin `/security-config` UI shows only lengths + masked prefixes —
  never the raw values.

### 5.4 No hardcoded secrets in the repo
- `.env` is gitignored (`/app/.gitignore` + `/app/backend/.gitignore`).
- Seed passwords for dev are **defaults**; production must provide
  `ADMIN_PASSWORD` or the admin seed refuses to accept the default (future
  hardening — a `REFUSE_DEFAULT_ADMIN_IN_PROD` guard tied to `APP_ENV=production`
  is a one-line follow-up).
- Test files under `/app/backend/tests/` read from `os.environ` only.

### 5.5 Dev vs production boundaries
- `APP_ENV` env var labels the environment (`dev` / `staging` / `production`).
  `/api/compliance/security-config` surfaces this + the computed
  `production_ready` boolean.
- Dev-only conveniences that must be stripped in production:
  - Password-reset `dev_token` response field.
  - Register-flow default behaviours.
  - The default admin seed password.

---

## 6. Admin surface

### `GET /api/compliance/security-config` (admin only)
Returns:
- `app_env`, `production_ready`
- `required_config` / `recommended_config` as booleans
- `weak_secrets` list + length + masked-prefix diagnostics
- `encryption` block from `core.key_manager.describe()`
- `features` block enumerating enabled controls
- `production_gaps` — humanised list of things to fix before go-live

UI: `frontend/src/pages/SecurityConfig.jsx` rendered at admin-only
`/security-config`. Informational only — no mutating actions.

---

## 7. What still belongs to infrastructure / operations

| Concern | Why it is out of app scope |
|---|---|
| KMS / HSM provisioning | Provider-specific (AWS, Azure, GCP, Vault). App provides the abstraction; infra provides the root keys. |
| TLS termination + cipher policy | Ingress controller / load balancer responsibility. |
| Secrets storage (Vault / Secrets Manager / SSM) | Infra team owns credential lifecycle. App reads from the process environment once those are mounted. |
| Backup encryption | Database-layer responsibility (e.g. MongoDB Atlas encrypted backups). |
| Encryption-in-transit between microservices | Today the app is a single FastAPI process. When split across services, add mTLS / service-mesh. |
| Hardware-level attestation | HSM / cloud confidential-compute feature. Not addressable from application code. |

---

## 8. Quick verification recipes

```bash
BASE=$REACT_APP_BACKEND_URL

# admin security-config snapshot
curl -b admin.txt "$BASE/api/compliance/security-config" | jq '{app_env, production_ready, production_gaps, encryption}'

# encryption sanity: create then detail a patient, DOB must round-trip
curl -b admin.txt -X POST "$BASE/api/patients" \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Jane","last_name":"Doe","date_of_birth":"1988-01-15","gender":"female"}'

# after POST, inspect the raw row — `date_of_birth` should begin with `enc:v1:`
mongosh --eval 'db.patients.findOne({last_name: { $regex: /Doe/ }}, {date_of_birth:1})'

# regression: existing plaintext DOBs still readable via `GET /patients/{id}?unmask=true`
```

---

## 9. Known limitations & roadmap

- MFA secret + backup codes not yet KMS-wrapped (backlog P0.3 / CR-3).
- Retention worker (physical purge after retention_until) not implemented
  (backlog P0.1 / DM-5).
- Audit log immutability at the storage layer (backlog P0.2 / AU-3).
- Key rotation tooling — `EXTRA_DATA_KEYS` exists but the background re-encrypt
  job is not yet wired (currently a manual operation).
- Appointment notes / reason not encrypted by default.
- Dev-only `dev_token` password-reset response must be stripped in production
  (trivial env-gated follow-up).

This list is cross-linked in `COMPLIANCE_BACKLOG.md` — use that as the
source of truth for prioritisation.
