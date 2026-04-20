# Contributing to CCMS

Thanks for improving CCMS. This project handles Protected Health Information
(PHI) — correctness, traceability, and documentation are non-negotiable.
Before you open a PR, please read this file in full.

## Ground rules
1. **Don't over-engineer.** Make the smallest change that solves the
   problem. Extend existing abstractions rather than introducing parallel
   ones. Resist refactors that aren't in the PR's stated scope.
2. **Every PHI surface must be audited + masked + access-controlled.** If
   you're adding a new endpoint that touches patient data, it MUST go
   through `scoped_filter` + `mask_patient` (or the structured equivalent),
   it MUST emit an `audit_success` / `audit_emergency` event, and it MUST
   be guarded by `require_permission` (not a role string).
3. **Never weaken encryption or masking to make a feature easier.**
4. **Never commit secrets.** All URLs, keys, and credentials come from
   `.env`. Omit default fallbacks so missing config fails fast.
5. **Trust the platform guarantees.** Don't add error-handling or validation
   for scenarios that can't happen. Validate only at system boundaries.

## Dev workflow
1. Read the handoff summary + `memory/PRD.md` before proposing changes.
2. For a non-trivial task, post an `ask_human` plan and get approval.
3. Implement the full feature / fix in the smallest set of files that makes
   sense. Use parallel tool calls.
4. Run:
   ```bash
   # Lint
   ruff check /app/backend
   # (ESLint already configured — run via the lint tool on changed files)

   # Backend regressions
   cd /app/backend
   REACT_APP_BACKEND_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d= -f2) \
     python -m pytest tests/test_patient_intake_phase1.py tests/test_phase5_docs_and_consent_pdf.py -q

   # Frontend logic tests (pure JS, no browser)
   node /app/frontend/src/pages/patientWizardLogic.test.js
   ```
5. Use `testing_agent_v3_fork` for any feature that spans ≥ 3 endpoints,
   touches auth, or changes user-facing UI.
6. Update the docs (see below) **in the same PR**.
7. Finish the task with the `finish` tool so PRD + CHANGELOG get flushed.

## Documentation is part of every PR
Missing doc updates block merge. The canonical matrix lives in
[`docs/DOC_UPDATE_POLICY.md`](./docs/DOC_UPDATE_POLICY.md). Fast reference:

| You changed…                          | You must also update…                                  |
|---------------------------------------|--------------------------------------------------------|
| A product feature / user flow         | `memory/PRD.md`, `CHANGELOG.md`                        |
| An API endpoint or data model         | `memory/PRD.md`, `CHANGELOG.md`                        |
| RBAC roles or permissions             | `memory/AUTHORIZATION_GUIDE.md`, `CHANGELOG.md`        |
| Auth / MFA / reauth / seed users      | `memory/test_credentials.md`, `CHANGELOG.md`, `SECURITY.md` if policy changed |
| Tenant / location scoping rules       | `memory/MULTI_TENANCY_ARCHITECTURE.md`, `CHANGELOG.md` |
| Any PHI surface (masking / crypto)    | `memory/HIPAA_COMPLIANCE.md`, `memory/PRIVACY_AND_RETENTION.md`, `CHANGELOG.md` |
| Deployment / env config               | `README.md`, `CHANGELOG.md`                            |
| Added / removed a dependency          | `requirements.txt` or `package.json`, `CHANGELOG.md`   |

Add the entry to the **[Unreleased]** section at the top of `CHANGELOG.md`.
When we cut a date-stamped release, move the unreleased block under a new
`## [YYYY-MM-DD]` heading.

## Commit & PR conventions
- **Imperative mood**, present tense. Example: `Add magic-byte sniff to patient doc uploads`.
- Reference issues or the PRD backlog item in the PR body.
- PRs should be focused: one feature or one bug fix. Large cross-cutting
  changes (e.g. router split) get their own PR with tests + CHANGELOG entry.
- Fill in `.github/pull_request_template.md` fully — the docs checklist is
  part of merge review.

## Code conventions
### Backend (Python / FastAPI)
- All routes are prefixed with `/api` (ingress rule).
- Endpoint DI order: path params → `request: Request` → query/form/body →
  `ctx: TenantContext = Depends(get_tenant_context)` → permission dep.
- `require_reauth(request, user)` is a **plain helper**, not a `Depends`.
  Call it inline after the user is resolved.
- Always exclude `_id` from Mongo projections; never return `ObjectId`.
- Always use `datetime.now(timezone.utc)` — never `utcnow()`.
- New DB collections: add scoping fields (`tenant_id`, `location_id`) at
  insert time via `stamp_for_write`.

### Frontend (React / Tailwind / Shadcn UI)
- Every interactive element and every element displaying critical / PHI
  data MUST carry a unique `data-testid`.
- Pages use default exports; components use named exports.
- Keep components small (< 150 lines when practical).
- Never hard-code URLs — always `process.env.REACT_APP_BACKEND_URL`.
- Use the Shadcn UI primitives in `components/ui/`; use `sonner` for toasts.

### Tests
- Co-locate backend tests in `/app/backend/tests/` using pytest.
- Co-locate pure-JS logic tests next to the module they verify.
- A new endpoint or route MUST ship with at least one happy-path test and
  one negative-path test.

## Security-sensitive changes
If your PR modifies any of the following, request a second reviewer and
explicitly note it in the PR description:

- Anything under `core/audit.py`, `core/crypto.py`, `core/masking.py`,
  `core/reauth.py`, `core/security.py`, `services/authz/*`.
- The masking projection returned from any PHI endpoint.
- The RBAC seed (`services/authz/seed.py`) or role/permission catalogue.
- Any endpoint that receives uploads or external input.

Report vulnerabilities privately — see [`SECURITY.md`](./SECURITY.md).
