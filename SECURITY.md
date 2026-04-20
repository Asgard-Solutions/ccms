# Security policy

CCMS handles Protected Health Information (PHI). Any security issue that
could impact confidentiality, integrity, or availability of patient data is
treated as high-priority.

## Reporting a vulnerability
**Please do not open a public GitHub issue for security bugs.**

- Email: `security@ccms.app` (replace with the clinic's dedicated security
  mailbox when forking this repo for production use).
- Expect an initial acknowledgement within 3 business days.
- Expect a triage decision + target fix date within 10 business days.
- Coordinated disclosure: we will work with you on a joint timeline before
  publishing any write-up.

Please include, as much as is safe to share:

1. A clear, reproducible description of the issue.
2. The exact endpoint / file / URL affected.
3. Any PoC request / response (redact PHI).
4. The impact you believe it has (confidentiality / integrity / availability,
   affected roles, tenant boundary implications).
5. Your suggested severity.

## Scope
In scope:
- Authentication (login, MFA, reauth, password reset).
- Authorization (RBAC, tenant + location scoping, break-glass overlays).
- PHI surfaces (patient data, medical records, documents, consent PDFs,
  audit log, exports).
- Dependency / supply-chain issues in pinned libraries.
- Storage backends (MongoDB queries, object storage, Redis cache).

Out of scope:
- Self-XSS requiring a signed-in user to paste code into devtools.
- Denial-of-service via volumetric traffic (rate-limited globally).
- Issues in unmodified upstream services (e.g. `emergentintegrations`) —
  report those to the upstream maintainer.

## Safe-harbour
Researchers acting in good faith, respecting user privacy (no exfiltrating
PHI, no accessing accounts that aren't yours), and giving us reasonable
time to respond will not face legal action under this policy.

## Our security posture
Technical safeguards currently shipping:
- AES-256-GCM field-level encryption at rest for PHI.
- JWT cookie sessions, 15-minute idle timeout, mandatory MFA for staff
  roles, step-up reauth for sensitive writes.
- Default-deny RBAC with 11 roles × 115 permissions.
- Tenant + location isolation on every query via `scoped_filter`.
- PHI-flagged audit logging on every read/write with IP + user-agent.
- 7-year soft-delete retention + legal-hold gate.
- Magic-byte MIME sniffing + 10 MB cap + streaming upload for PHI uploads.
- Signed consent PDFs rendered on demand (no persistent PHI leakage).

See [`memory/HIPAA_COMPLIANCE.md`](./memory/HIPAA_COMPLIANCE.md) for the
full safeguard inventory and [`memory/OPERATIONAL_SECURITY_READINESS.md`](./memory/OPERATIONAL_SECURITY_READINESS.md)
for SOC 2 / ISO 27001 readiness notes.

## Changes to this policy
Policy updates are recorded in `CHANGELOG.md` under the `Security` heading
of each release block.
