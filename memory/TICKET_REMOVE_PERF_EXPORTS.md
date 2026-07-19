# TICKET — Remove performance-governance compatibility re-exports

**Status:** OPEN · **Blocked**
**Blocker:** Clinical redesign freeze (see `/app/memory/CLINICAL_REDESIGN_FREEZE.md`). Ticket remains blocked until the freeze is **formally lifted**.
**Timing:** Post-freeze maintenance window.
**Type:** Planned maintenance (not a shipped change — do **not** add to `CHANGELOG.md` yet).
**Filed:** 2026-02-15
**Owner (proposed):** Platform reliability
**Rollback:** Single-commit revert.

---

## Interim control (during freeze — code review only)

While this ticket is blocked, the compatibility layer is **intentionally preserved**. To keep it from growing without adding another executable governance surface, the following rule is enforced **at code-review time only** — no pre-commit hook, no CI check, no lint rule is added during the freeze:

- **No new consumers** (scripts, tests, tooling, or docs) may import shared governance helpers from `scripts.run_clinical_perf`. New code must import them directly from `scripts._perf_gov_lib`.
- Existing imports from `scripts.run_clinical_perf` for shared helpers are grandfathered until this ticket executes; they are **not** to be expanded.
- Reviewers should reject any diff that adds a new `from scripts.run_clinical_perf import <shared-helper>` line.

When the freeze is lifted, this ticket removes the re-exports **and** migrates every remaining consumer in the **same isolated commit** (see Acceptance criteria below). Only after that cleanup lands should a CI check for forbidden legacy imports be considered — and only if the old path is empirically likely to reappear.

---

## Context

`scripts/_perf_gov_lib.py` was extracted (behavior-preserving) as the single home
for the shared performance-governance primitives used by the G2 measurement
harness, the threshold-promotion tool, and the read-only CI guard. To keep the
extraction low-risk during the redesign freeze, **compatibility re-exports were
intentionally left in place** on the original modules so existing call sites
(scripts, tests, docs) continued to work unchanged.

Current call-site topology (see `git grep` results captured at ticket time):

- `scripts/run_clinical_perf.py` imports canonical symbols from
  `scripts._perf_gov_lib` and re-exports them at module scope for
  backwards compatibility.
- `scripts/promote_perf_threshold.py` and `scripts/check_perf_governance.py`
  still import several shared helpers **from `scripts.run_clinical_perf`**
  (routed transparently through the re-exports).
- `scripts/_perf_gov_lib.py` has a small runtime fallback that pulls
  `METRICS` from `scripts.run_clinical_perf` if the canonical constant
  is not present locally — a defensive shim during the extraction step.
- Tests under `backend/tests/` reference several of these symbols
  through `scripts.run_clinical_perf` and `scripts.promote_perf_threshold`
  rather than `scripts._perf_gov_lib` directly.

Post-freeze, the compatibility layer becomes dead weight and should be
removed so `_perf_gov_lib` is the sole source of truth.

## Scope (what to change)

1. In `scripts/promote_perf_threshold.py` and `scripts/check_perf_governance.py`,
   replace any `from scripts.run_clinical_perf import <shared-helper>` with
   `from scripts._perf_gov_lib import <shared-helper>`.
2. In `scripts/run_clinical_perf.py`, remove the compatibility re-export block
   for symbols that already live in `_perf_gov_lib`. Keep only symbols that
   are genuinely defined by the harness itself (CLI entrypoint, harness-
   specific glue, `METRICS`, etc.).
3. In `scripts/_perf_gov_lib.py`, remove the defensive
   `from scripts.run_clinical_perf import METRICS as _M` fallback and any
   sibling shim comments. `METRICS` remains owned by `run_clinical_perf`
   and callers that need it import it from there directly.
4. In `backend/tests/`, migrate references to shared governance primitives so
   they import from `scripts._perf_gov_lib` directly. Harness-specific tests
   (CLI, `METRICS`, argument parsing) continue to import from
   `scripts.run_clinical_perf`.
5. No behavior changes: CLI flags, stdout/stderr shapes, exit codes,
   exception types, JSON/Markdown output structure — all unchanged.

## Explicit non-goals (do NOT touch)

- Any Clinical UI code under `frontend/src/pages/clinical/**`.
- `identity/users.preferences`, workspace-mode registry, telemetry, or
  feature flags.
- Any frozen Clinical contracts (`ClinicalUIDefaults`, `UIEventPayload`,
  workspaceModes registry, featureFlags matrix).
- Release-gate documents under `/app/memory/CLINICAL_*` and
  `/app/memory/release_evidence/**` — **unless** they explicitly cite the
  old import path (`from scripts.run_clinical_perf import …` for a shared
  primitive), in which case update only that reference.
- `CHANGELOG.md` (this is not a shipped feature; log after execution).

## Acceptance criteria (must all hold)

- All consumers import shared governance primitives **directly** from
  `scripts._perf_gov_lib`.
- **No** remaining imports from `scripts.run_clinical_perf` for shared
  governance helpers. `run_clinical_perf` imports may only cover harness-
  local symbols (CLI entrypoint, `METRICS`, harness-specific glue).
- Compatibility re-exports are removed in **one isolated commit**
  (no drive-by refactors, no unrelated file churn).
- CLI behavior, output formats (human + JSON + Markdown), exception
  semantics, and exit codes remain **unchanged**.
- Full governance tooling test suite passes:
  - `backend/tests/test_perf_gov_lib.py`
  - `backend/tests/test_run_clinical_perf.py`
  - `backend/tests/test_promote_perf_threshold.py`
  - `backend/tests/test_check_perf_governance.py`
  - `backend/tests/test_seed_large_chart.py`
  - `backend/tests/test_perf_threshold_draft.py`
- Full backend clinical contract suite passes.
- **No** Clinical UI, preferences, telemetry, feature-flag, or frozen-
  contract code is modified.
- **No** release-gate documents are changed unless they explicitly
  reference the old import path.
- Rollback is a single-commit revert.
- Ticket remains blocked until the redesign freeze is **formally lifted**.

## Verification steps

1. `git grep -n "from scripts.run_clinical_perf import" backend/` — must
   only return matches that are truly harness-local symbols.
2. `git grep -n "from scripts._perf_gov_lib import" backend/` — must be
   the canonical import site for all shared primitives.
3. Run governance-suite tests + backend clinical contract suite; all green.
4. Manually smoke the three CLIs against a seeded synthetic chart to
   confirm identical stdout/stderr/exit codes vs. pre-change baseline.

## References

- `/app/memory/CLINICAL_REDESIGN_FREEZE.md`
- `/app/memory/CLINICAL_PERFORMANCE_THRESHOLD_PROMOTION.md`
- `/app/backend/scripts/_perf_gov_lib.py`
- `/app/backend/scripts/run_clinical_perf.py`
- `/app/backend/scripts/promote_perf_threshold.py`
- `/app/backend/scripts/check_perf_governance.py`
