# CCMS TLS & Transport Security

**Last updated:** 2026-02-18 (TLS / transport posture phase)
**Complements:** `ACCESS_CONTROL_AND_AUDIT.md`, `DATA_PROTECTION_AND_KEYS.md`, `OPERATIONAL_SECURITY_READINESS.md`, `COMPLIANCE_BASELINE.md`.

> **Shared-responsibility summary.** The application **does not terminate
> TLS**. It never has, never should. True TLS version / cipher / certificate
> enforcement lives at the ingress / reverse-proxy / load-balancer layer. This
> document describes (a) what the application does emit and expect under the
> assumption that a secure transport sits in front of it, and (b) what the
> deployment team still has to do outside the app.

---

## 1. Shared responsibility

| Concern | Application (this repo) | Infrastructure (ingress / LB / proxy) |
|---|---|---|
| Terminate TLS | ❌ | ✅ **(required)** |
| Enforce TLS ≥ 1.2, prefer 1.3 | ❌ | ✅ |
| Disable SSLv3 / TLS 1.0 / TLS 1.1 | ❌ | ✅ |
| Cipher suite policy (AEAD only) | ❌ | ✅ |
| Certificate issuance + rotation | ❌ | ✅ |
| HSTS enforcement at edge | partial (app emits header when it can detect HTTPS in production) | ✅ (authoritative) |
| Reject HTTP → HTTPS redirect / 426 | ❌ | ✅ |
| Mutual TLS between internal services | ❌ (single-process today) | ✅ (when broken into services) |
| Secure cookie flags (`HttpOnly + Secure + SameSite=None`) | ✅ | — |
| `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `CSP`, `COOP`, `CORP` headers | ✅ | ✅ (belt-and-braces; ingress should also emit) |
| Trusted-proxy hop handling | ✅ (reads `x-forwarded-proto`, `x-forwarded-for`) | ✅ (ingress must strip/canonicalise these before forwarding) |
| Log transport warnings on misconfig | ✅ | — |
| WAF / DDoS / bot management | ❌ | ✅ |

---

## 2. App-layer behaviour (what CCMS does itself)

### 2.1 Secure cookie settings
All auth cookies are set in `services/identity/router.py::_cookie_kwargs` with:

```
HttpOnly=True
Secure=True           # always — browsers refuse Secure cookies over plain HTTP
SameSite=None         # required for cross-origin SPA + API deployments
Path=/
```

Consequence: a deployment without TLS termination in front of the app will silently break login — browsers will drop the cookie. This is intentional; it fails closed.

### 2.2 HTTPS-only assumption
The app's cookies + CORS posture make TLS a **hard requirement in practice**. If `FRONTEND_URL` is not HTTPS in production, `core.config.transport_warnings()` surfaces a warning at startup and on `/api/compliance/transport`.

### 2.3 Redirect behaviour
The application does **not** perform HTTP → HTTPS redirects. That belongs at the ingress. A redirect issued by FastAPI would require the app to receive the plaintext request, which already leaks headers (including cookies in some browsers) before the redirect.

### 2.4 Trusted-proxy handling
The application reads `X-Forwarded-For` (first hop — `.split(",")[0].strip()`) for audit IP attribution and `X-Forwarded-Proto` to detect HTTPS for conditional HSTS emission. Both are **spoofable** if the proxy chain in front of the app does not strip/canonicalise them. Production deployments must:

1. Set `TRUSTED_PROXY_COUNT` to the number of proxies in front of the app.
2. Configure the ingress to:
   - Overwrite `X-Forwarded-For` with its own view of the client IP.
   - Set `X-Forwarded-Proto` authoritatively.
   - Reject or strip incoming `X-Forwarded-*` headers from the public internet.

If `TRUSTED_PROXY_COUNT` is unset in production, `transport_warnings()` emits an alert.

### 2.5 CSRF / session behaviour under secure transport
- CSRF risk vector: POST with credentials cookie.
- Mitigations:
  - `SameSite=None; Secure` restricts cookie attachment to explicit cross-site requests; a cross-site POST from a third-party page still attaches the cookie, so we rely on other controls.
  - The authenticated SPA always sends `Origin` and `Referer`; the backend's CORS allow-list is locked to `FRONTEND_URL` in production.
  - JSON-only bodies (`Content-Type: application/json`) — classic HTML-form CSRF (auto-submitted multipart/URL-encoded) would not carry a JSON content-type, and would be rejected by FastAPI at the body-parse stage for endpoints that declare Pydantic bodies.
  - Step-up re-authentication is required for destructive operations (`core/reauth.py`).
- For *higher-risk* deployments (public SaaS with third-party integrations) consider adding a double-submit CSRF token; tracked as a follow-up item.

### 2.6 HSTS awareness
`core/security_headers.py` emits `Strict-Transport-Security` **only** when:
- `APP_ENV=production`, AND
- the effective request scheme (via `X-Forwarded-Proto` or the direct URL) is `https`.

Default: `max-age=15552000; includeSubDomains; preload` (180 days). Override via `HSTS_MAX_AGE_SECONDS`. The ingress should also emit its own authoritative HSTS — the app's header is defence-in-depth.

---

## 3. Production TLS standard

### 3.1 Target
- **TLS 1.3 preferred**, AEAD-only cipher suites:
  `TLS_AES_256_GCM_SHA384`, `TLS_CHACHA20_POLY1305_SHA256`, `TLS_AES_128_GCM_SHA256`.
- **TLS 1.2 allowed as fallback** only with ECDHE + AEAD suites; no CBC, no RC4, no static RSA key exchange. Suggested allow-list:
  - `ECDHE-ECDSA-AES256-GCM-SHA384`
  - `ECDHE-RSA-AES256-GCM-SHA384`
  - `ECDHE-ECDSA-CHACHA20-POLY1305`
  - `ECDHE-RSA-CHACHA20-POLY1305`
- **Disable** TLS 1.0, TLS 1.1, SSLv3. Disable compression. Disable renegotiation.
- OCSP stapling enabled.
- Modern key types: ECDSA P-256 or RSA 2048+ (prefer ECDSA).

### 3.2 Configuration hints
- **NGINX / NGINX-Ingress**:
  ```
  ssl_protocols TLSv1.3 TLSv1.2;
  ssl_ciphers 'ECDHE+AESGCM:ECDHE+CHACHA20:!aNULL:!MD5:!DSS';
  ssl_prefer_server_ciphers off;
  ssl_session_tickets off;
  ssl_stapling on;
  ssl_stapling_verify on;
  ```
- **Envoy / Istio**: set `tls_minimum_protocol_version: TLSv1_2`, `tls_maximum_protocol_version: TLSv1_3`, restrict `cipher_suites` to the AEAD list above.
- **AWS ALB**: attach a policy of `ELBSecurityPolicy-TLS13-1-2-2021-06` (or newer).
- **GCP L7 LB**: use `RESTRICTED` or `MODERN` SSL policy.
- **Azure Application Gateway**: use `AppGwSslPolicy20220101` or newer, min TLS 1.2.

Verify with:
```bash
# tls version + cipher negotiated
curl --tlsv1.3 -I https://<host>/
nmap --script ssl-enum-ciphers -p 443 <host>
testssl.sh <host>
```

---

## 4. Certificate lifecycle

| Concern | Responsibility |
|---|---|
| Issuance | Managed by the cloud LB (AWS Certificate Manager, GCP Managed Certs, Azure Key Vault Certs) OR Let's Encrypt via cert-manager in k8s. |
| Rotation | Fully automated via the above. Track expiry in Prometheus (`probe_ssl_earliest_cert_expiry`). |
| Key storage | Private key never leaves the LB / KMS boundary. Never checked into this repo. |
| Revocation / rekey | Automated when the CA lineage rotates. OCSP stapling configured at edge. |
| Wildcard vs SAN | Follow the principle of least exposure — one cert per service preferred. |

---

## 5. Internal service-to-service transport

Today CCMS is a single FastAPI process. When split into microservices (the PRD's long-term target):

| Link | Requirement |
|---|---|
| App ↔ MongoDB | TLS from driver (Motor URI `?tls=true&tlsCAFile=...`). Atlas clusters default to TLS; self-hosted must configure. |
| App ↔ Redis | `rediss://` scheme, TLS to ElastiCache / Redis Enterprise. |
| App ↔ email/SMS provider | Provider endpoints are HTTPS; pin intermediate CAs if the provider supports it. |
| Service A ↔ Service B | Service mesh (Istio / Linkerd) with mTLS — ensures both authenticity and confidentiality. |
| Service ↔ KMS | Provider SDK uses TLS by default; service-account credentials scoped to least-privilege. |

---

## 6. Security headers emitted by the app

Set by `core/security_headers.py::SecurityHeadersMiddleware`:

| Header | Value / Purpose |
|---|---|
| `Strict-Transport-Security` | Production + HTTPS: `max-age=15552000; includeSubDomains; preload`. Suppressed in dev. |
| `X-Content-Type-Options` | `nosniff` — prevent MIME confusion attacks. |
| `X-Frame-Options` | `DENY` — clickjacking defence. |
| `Referrer-Policy` | `strict-origin-when-cross-origin` — leak-minimising referrer. |
| `Permissions-Policy` | Disable geolocation / microphone / camera / payment / usb / sensors. |
| `Content-Security-Policy` | `default-src 'self'; frame-ancestors 'none'; upgrade-insecure-requests; object-src 'none'; base-uri 'self'; form-action 'self'` + same-origin script/style/img (data:+https: allowed for assets). Tighten to hash/nonce-based `script-src` once the frontend build emits hashes. |
| `Cross-Origin-Opener-Policy` | `same-origin` — isolate from other origins. |
| `Cross-Origin-Resource-Policy` | `same-site`. |

Ingress is encouraged to emit these as well — defence in depth; whichever value reaches the browser first wins by spec, and the app's `.setdefault(...)` path never clobbers an ingress-set header.

---

## 7. Environment + configuration hooks

- `core/config.py::transport_warnings()` is evaluated at startup and on every hit to `GET /api/compliance/transport`. Detects, for `APP_ENV=production`:
  - `FRONTEND_URL` unset → CORS will fall back to wildcard.
  - `FRONTEND_URL` not HTTPS → Secure cookies will fail.
  - `CORS_ORIGINS='*'` → must be explicit in production.
  - `TRUSTED_PROXY_COUNT` unset → forwarded headers are spoofable.
- Each warning is logged at WARNING (`ccms` logger) on startup, surfaced in the admin endpoint, and should be treated as a go-live blocker.

### Env-var contract
| Variable | Purpose |
|---|---|
| `APP_ENV` | `dev` (default) / `staging` / `production`. Enables HSTS + JSON root logging in production. |
| `FRONTEND_URL` | HTTPS origin of the SPA — locks CORS and makes cookie policy safe. |
| `CORS_ORIGINS` | Comma-separated explicit origins. Must not be `*` in production. |
| `TRUSTED_PROXY_COUNT` | Number of proxies in front of the app. Guides how `X-Forwarded-*` are trusted. |
| `HSTS_MAX_AGE_SECONDS` | Override default 180-day HSTS max-age. |
| `CSP_EXTRA` | Extra directives appended to the default CSP. |

---

## 8. Verification recipes

```bash
BASE=$REACT_APP_BACKEND_URL
ADMIN="admin.txt"

# app-emitted security headers
curl -sI "$BASE/api/health" | egrep -i 'x-content|x-frame|referrer|permissions|content-security|cross-origin'

# HSTS only in production
APP_ENV=production python3 -c "
import asyncio
from httpx import AsyncClient
from server import app
async def main():
    async with AsyncClient(app=app, base_url='https://test') as c:
        r = await c.get('/api/health')
        print(r.headers.get('Strict-Transport-Security'))
asyncio.run(main())
"

# admin transport posture
curl -b admin.txt "$BASE/api/compliance/transport" | jq .

# confirm no HTTPS-only redirect happens at the app (ingress does this)
curl -sI -o /dev/null -w '%{http_code}\n' "$BASE/api/health"   # → 200

# TLS probe against the live ingress (ingress responsibility)
openssl s_client -connect <host>:443 -tls1_3 -servername <host> </dev/null
testssl.sh --fast <host>
```

---

## 9. What is still required outside the app

| Action | Owner |
|---|---|
| Provision and attach a TLS 1.3-capable LB / ingress | DevOps |
| Configure cipher-suite policy + disable TLS ≤ 1.1 | DevOps |
| Automate cert issuance + rotation (ACM / cert-manager / Key Vault) | DevOps |
| Strip / canonicalise `X-Forwarded-*` at the edge | DevOps |
| Enable HSTS at the ingress with appropriate max-age + preload roll-out plan | Security Officer |
| Operate WAF / DDoS / bot management | SecOps |
| Enable TLS between the app and MongoDB, Redis, external providers | DevOps |
| Add mTLS when CCMS is split into microservices | DevOps / Platform |
| Monitor cert expiry, TLS handshake failures | SRE / DevOps |

None of these are in-repo deliverables. The application provides the signals + guardrails; the platform provides the crypto.
