<!--
Thank you for contributing to CCMS. Fill in every section — the docs
checklist is part of merge review. Reference:
- CONTRIBUTING.md
- docs/DOC_UPDATE_POLICY.md
- SECURITY.md (for any PHI-adjacent change)
-->

## Summary
<!-- One paragraph: what changed and why. Link the PRD/backlog item. -->

## Type of change
- [ ] Feature
- [ ] Bug fix
- [ ] Refactor / tech-debt cleanup
- [ ] Dependency update
- [ ] Documentation only
- [ ] Security fix (also tick the Security section below)

## Testing evidence
<!-- Paste passing pytest output, node test output, or the testing_agent_v3_fork report path. -->
- [ ] Backend regressions pass (`pytest`)
- [ ] Frontend logic tests pass (`node …test.js`) *(if applicable)*
- [ ] Smoke screenshot attached *(for UI changes)*
- [ ] `testing_agent_v3_fork` was run and all P0 issues fixed

## PHI / HIPAA impact
<!-- Does this touch patient data, audit log, masking, encryption, or auth? If yes: -->
- [ ] `scoped_filter` / `stamp_for_write` applied to any new query/write
- [ ] `require_permission` (not a role string) guards the endpoint
- [ ] `audit_success` / `audit_emergency` emitted with `phi_accessed` flag
- [ ] Masked projection verified for non-admin callers
- [ ] No new fields bypass AES-256-GCM encryption

## Docs checklist (from `docs/DOC_UPDATE_POLICY.md`)
Tick every box that applies. If a box is unticked, justify in the
"Deviations" section below.

- [ ] `CHANGELOG.md` — appended an entry under `[Unreleased]`
- [ ] `memory/PRD.md` — updated what's implemented / roadmap
- [ ] `README.md` — updated setup / layout / docs map *(if relevant)*
- [ ] `memory/HIPAA_COMPLIANCE.md` — updated safeguard row *(if PHI-adjacent)*
- [ ] `memory/AUTHORIZATION_GUIDE.md` — updated RBAC matrix *(if roles/perms changed)*
- [ ] `memory/MULTI_TENANCY_ARCHITECTURE.md` — updated scoping notes *(if tenant rules changed)*
- [ ] `memory/test_credentials.md` — updated seed users *(if auth/seed changed)*
- [ ] `SECURITY.md` — updated safeguards / scope *(if policy changed)*
- [ ] New docs linked from `README.md` and the matrix *(if you created one)*

## Security review
<!-- Required if the PR touches auth, RBAC, masking, encryption, uploads, or exports. -->
- [ ] Second reviewer requested
- [ ] No secrets committed (grep for keys / tokens / passwords)
- [ ] No PHI committed in fixtures / test data

## Deviations
<!-- List every unticked checkbox above with a one-line justification. -->

## Screenshots / recordings
<!-- UI changes: attach a before/after screenshot or a short clip. -->
