# Account Security Architecture

_One-page reference for the Account Settings page (`/security`) — Profile, Password, MFA, PIN, Licenses, Session._

## 1. Endpoint matrix

| Endpoint                              | Scope        | Reauth | Throttle (per-user) | Throttle (per-IP)  | Audit action                       |
|---------------------------------------|--------------|--------|---------------------|--------------------|------------------------------------|
| `GET  /auth/me`                       | self         | no     | —                   | —                  | —                                  |
| `PATCH /auth/me/preferences`          | self         | no     | —                   | —                  | `user.preferences_updated`         |
| `PATCH /auth/me/profile` (benign)     | self         | no     | —                   | —                  | `user.profile_updated`             |
| `PATCH /auth/me/profile` (email)      | self         | YES    | 5 / 15 min          | 60 / 60 s          | `user.profile_updated` (+ epoch ↑) |
| `POST  /auth/change-password`         | self         | no     | 5 / 15 min          | 60 / 60 s          | `auth.password_changed` / fail     |
| `POST  /auth/reauth`  password path   | self         | —      | 5 / 15 min          | 60 / 60 s          | `auth.reauth`                      |
| `POST  /auth/reauth`  PIN path        | self         | —      | PIN lockout (5 → 15 min) | 60 / 60 s     | `auth.reauth`                      |
| `POST  /auth/mfa/setup`               | self         | no     | —                   | —                  | `auth.mfa_setup_started`           |
| `POST  /auth/mfa/verify`              | self         | no     | —                   | —                  | `auth.mfa_enabled` / `auth.mfa_enable` fail |
| `POST  /auth/mfa/disable`             | self         | no     | 5 / 15 min          | 60 / 60 s          | `auth.mfa_disabled` / fail         |
| `GET   /auth/me/pin/status`           | self         | no     | —                   | —                  | —                                  |
| `POST  /auth/me/pin`                  | self         | no     | 5 / 15 min          | 60 / 60 s          | `user.pin_created` / fail          |
| `PATCH /auth/me/pin`                  | self         | no     | 5 / 15 min          | 60 / 60 s          | `user.pin_changed` / fail          |
| `POST  /auth/me/pin/reset`            | self         | YES    | —                   | —                  | `user.pin_reset`                   |
| `DELETE /auth/me/pin`                 | self         | no     | 5 / 15 min          | 60 / 60 s          | `user.pin_removed` / fail          |
| `POST  /auth/me/pin/verify`           | self         | no     | PIN lockout         | —                  | `auth.pin_verify` / fail           |
| `GET   /auth/sessions`                | self         | no     | —                   | —                  | —                                  |
| `GET   /auth/me/licenses`             | self         | no     | —                   | —                  | —                                  |
| `POST  /auth/me/licenses`             | self (clin)  | no     | —                   | —                  | `user.license_added`               |
| `PATCH /auth/me/licenses/{id}`        | self (clin)  | no     | —                   | —                  | `user.license_updated`             |
| `DELETE /auth/me/licenses/{id}`       | self (clin)  | no     | —                   | —                  | `user.license_removed`             |
| `GET   /auth/me/export`               | self         | no     | —                   | —                  | `account.self_exported`            |

*(clin) = role ∈ `{admin, doctor}`. Every other role gets 403 on writes and `[]` on the read.*

## 2. Shared throttling contract

All five **sensitive-auth** endpoints above share the same pattern implemented in
`services/identity/router.py::_guard_sensitive_auth` + `_record_sensitive_auth_failure`:

- **Per-user failure counter** — keyed `{action}:user:{user_id}`. Five failures in
  a sliding 15-minute window ⇒ the handler short-circuits with HTTP 429 and
  a `locked_out` audit row, **before** doing any credential hash comparison.
  Naturally expires after the window rolls over.
- **Per-IP volume ceiling** — keyed `{action}:vol:{ip}`. 60 requests per
  60-second sliding window. Blocks scripted abuse regardless of which
  user account is being targeted. Keyed by action, so spamming
  password-changes can't burn the budget for PIN verifies.

Backing store:

1. Redis (when `REDIS_URL` is set and reachable).
2. In-process deques in `core/rate_limit.py` as a graceful fallback.

**Reset hook**: non-production environments expose
`POST /api/_debug/rate-limit/reset` (gated by `APP_ENV != production`)
so the pytest `conftest.py` can clear state between tests.

## 3. Audit reason vocabulary

All sensitive-action audit rows use the same machine-readable `reason`
field for trivial SIEM filtering:

| reason                | When                                                      |
|-----------------------|-----------------------------------------------------------|
| `invalid_password`    | Wrong password supplied                                   |
| `invalid_pin`         | Wrong PIN supplied                                        |
| `locked_out`          | Per-user failure budget exhausted, or PIN lockout engaged |
| `rate_limited_volume` | Per-IP volume ceiling hit                                 |
| `rate_limited_failures` | (change-password only) — alias of `locked_out`          |
| `pin_not_configured`  | Reauth PIN path called without a PIN set                  |
| `bad_code`            | MFA TOTP verify failure                                   |

**Never logged**: password values, PIN values, MFA secrets or TOTP codes,
license numbers (only `license_number_length` is recorded).

## 4. Step-up flow

```
User action requiring reauth
    ↓
Backend gate `require_reauth()` checks x-reauth-token / reauth_token cookie
    ↓ (missing/expired)
Backend returns 401 with X-Reauth-Required response header
    ↓
Frontend axios interceptor pops <ReauthDialog>
    ↓
User enters password OR 6-digit PIN (if configured)
    ↓
POST /api/auth/reauth  { password: ... } | { pin: ... }
    ↓ success
5-minute `reauth_token` cookie + header value
    ↓
Original request transparently retried by the interceptor
```

The PIN path reuses the same 5-failure / 15-minute lockout the PIN
verify endpoint enforces — you cannot side-step the PIN lockout by
going through reauth.

## 5. Frontend state hygiene

- Passwords and PINs live only in component-local React state. No
  sensitive auth values are written to `localStorage` or `sessionStorage`
  anywhere in the app (grep-verified).
- Dialog components (`CreatePinDialog`, `ChangePinDialog`,
  `ResetPinDialog`, `RemovePinDialog`, `ReauthDialog`) clear their
  password/PIN state in a `useEffect` on `open → false` — so closing
  and reopening always starts from blank inputs.
- The password-change form (`SecurityTab.PasswordChangeCard`) clears on
  a successful submit.
- Reauth dialog clears on successful confirm; keeps entered values on
  failure so the user can correct a typo without retyping everything.
- React GC naturally drops sensitive state on component unmount.

## 6. Session hardening (existing, unchanged)

- **Session epoch**: bumped on password change, email change, MFA
  enable/disable, admin MFA reset. Old JWTs die immediately.
- **Absolute session lifetime**: hard cap enforced in `core/deps.py`
  regardless of refreshes.
- **Refresh on privilege change**: `/auth/refresh` re-validates epoch.
- **Session revocation on password change**: all other active sessions
  are killed inline; the acting session gets re-issued cookies.
- **MFA policy**: Admin / doctor / staff roles see an MFA setup banner
  until enrolled; admins can force-require per user via
  `POST /auth/users/{id}/mfa/require`.

## 7. Data at rest (HIPAA)

- Passwords & PINs: `bcrypt` hashing via `core/security.hash_password` /
  `verify_password`. Never stored or logged in plaintext.
- PHI (patient records): AES-256-GCM encryption in `core/crypto.py`.
- License numbers are stored in plaintext (non-PHI, non-secret) but
  redacted from audit metadata (length only).
