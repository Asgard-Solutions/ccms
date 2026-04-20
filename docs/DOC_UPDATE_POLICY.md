# Documentation update policy

> **TL;DR** — if the code changes, the docs change in the same PR. No
> exceptions. Missing doc updates block merge.

This file is the single source of truth for *which* docs must be touched
when *which* kinds of changes ship. It is referenced from `README.md`,
`CONTRIBUTING.md`, and the PR template.

## Owners
- **Main agent (E1) & human maintainers** — responsible for keeping every
  document on this list current.
- **Testing agent** — flags missing test-credential updates in its report.
- **PR review** — verifies the docs checklist in the PR template is
  complete.

## The matrix
Each row maps a *kind of change* to the documents that must be updated. A
single PR may hit multiple rows — satisfy all of them.

| Change kind                                    | `CHANGELOG.md` | `memory/PRD.md` | `memory/HIPAA_COMPLIANCE.md` | `memory/AUTHORIZATION_GUIDE.md` | `memory/MULTI_TENANCY_ARCHITECTURE.md` | `memory/test_credentials.md` | `README.md` | `SECURITY.md` |
|------------------------------------------------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| New user-facing feature / UX flow              | ✅  | ✅  |     |     |     |     |     |     |
| New or renamed API endpoint                    | ✅  | ✅  |     |     |     |     |     |     |
| Data-model change (schema, field, collection)  | ✅  | ✅  |     |     |     |     |     |     |
| RBAC role / permission added or changed        | ✅  |     |     | ✅  |     |     |     |     |
| Tenant- or location-scoping rule change        | ✅  |     |     |     | ✅  |     |     |     |
| Masking / encryption / audit logic change      | ✅  |     | ✅  |     |     |     |     | ✅  |
| Auth flow change (login, MFA, reauth, PW policy) | ✅|     | ✅  |     |     | ✅† |     | ✅  |
| Seed user / admin / demo credentials changed   | ✅  |     |     |     |     | ✅  |     |     |
| Deployment / env / supervisor / port change    | ✅  |     |     |     |     |     | ✅  |     |
| Dependency added or removed (py or js)         | ✅  |     |     |     |     |     |     |     |
| Security vuln fix (HIPAA surface)              | ✅  |     | ✅  |     |     |     |     | ✅  |
| Test harness or testing-agent workflow change  | ✅  |     |     |     |     |     | ✅  |     |

† Only if the password or MFA setup changes for the demo accounts.

## Guidance per document

### `CHANGELOG.md`
- Append to the `[Unreleased]` section. Use the five-section template:
  `Added` / `Changed` / `Fixed` / `Security` / `Dependencies`. Keep entries
  short, imperative, and user-meaningful.
- On a dated release, move the block under `## [YYYY-MM-DD] <theme>`.

### `memory/PRD.md`
- Update "What's implemented" section with the new feature + date.
- Update the roadmap (P0 / P1 / P2) if priorities shift.
- If the document grows past ~700 lines, split into `PRD.md` (static
  problem statement + personas) + `CHANGELOG.md` (history) +
  `ROADMAP.md` (backlog). Reference the split in the top of each file.

### `memory/HIPAA_COMPLIANCE.md`
- Update the matching safeguard row with the new control. If you added a
  new control, insert a row with status (`implemented` / `external`) +
  evidence pointer (file path or audit log action name).

### `memory/AUTHORIZATION_GUIDE.md`
- Update the role × permission matrix. If you added a resource or action
  string, add it to the "Catalogue" table with its scope rules.

### `memory/MULTI_TENANCY_ARCHITECTURE.md`
- Describe the scoping rule change (read/write path) and note whether
  `scoped_filter` was updated or a bespoke filter was introduced.

### `memory/test_credentials.md`
- Keep the credential table in sync with the seed script. Include
  `email`, `password`, `role`, and any MFA prerequisites.
- After auth changes, the **testing agent** will flag this file if stale.
  Resolve the flag before requesting a retest.

### `README.md`
- Update "Running locally" if setup commands change.
- Update the "Repository layout" tree if a new top-level folder appears.
- Update "Documentation map" if a new doc is introduced.

### `SECURITY.md`
- Update the "Our security posture" list when a new safeguard ships.
- Update the "Scope" list when new surfaces become in-scope.

## Triggers for new documents
Create a new document when:
- A subsystem grows past ~600 lines of code and has its own architectural
  story (e.g. Billing, Patient Portal, Reporting). Place under `memory/`.
- A public-facing page is required (marketing, API docs, integration
  guide). Place under `docs/`.

When you create a new doc:
1. Link it from `README.md` (Documentation map section).
2. Link it from any sibling docs it cross-references.
3. Add it to the matrix in this file so future PRs know when to update it.

## Automation hooks
Today the policy is enforced by:
- `.github/pull_request_template.md` checkbox list.
- The testing agent's `test_credentials.md` freshness check.
- The main agent's `finish` tool, which updates `memory/PRD.md` on every
  successful feature completion.

Future automation (tracked in `memory/COMPLIANCE_BACKLOG.md`):
- Pre-commit hook that grep-checks the diff against this matrix.
- CI job that diffs `CHANGELOG.md`'s `[Unreleased]` block and fails if no
  entry was added when code in `backend/` or `frontend/` changed.
