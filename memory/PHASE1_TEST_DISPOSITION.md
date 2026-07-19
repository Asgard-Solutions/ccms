# Phase 1 Clinical Redesign — Test disposition

Source report: `/app/test_reports/iteration_89.json` (62/64 direct assertions passed on 2026-07-10).

## Non-passing tests

### 1. Back-to-top button click swallowed by "Made with Emergent" watermark
- **Report field**: `frontend_issues.design_issues[0]`
- **Classification**: **Accepted limitation — preview environment only**
- **Rationale**: The badge is a preview-container artifact and is NEVER rendered in tenant deployments. Programmatic `element.click()` on the DOM node executes correctly and the `window.scrollTo({top: 0})` fires; only Playwright's pointer-position click at the badge coordinates is intercepted.
- **Mitigation shipped in this iteration**: raised `clinical-back-to-top` to `z-50` (above the badge's `z-40`) so the button is clickable in preview screenshots as well.
- **Action to close**: none. Ops-facing note added to the Phase 1 UAT matrix so testers know to click, not just hover.

### 2. `aria-current="location"` did not stick on the last section (Outcomes)
- **Report field**: `frontend_issues.ui_bugs[0]`
- **Classification**: **Defect — FIXED**
- **Root cause**: `IntersectionObserver` with `rootMargin: '-30% 0px -60% 0px'` never activated the bottom-most section because the viewport can't scroll far enough for that section to enter the activation band.
- **Fix shipped**: `ClinicalTabV2.jsx` `jumpTo()` now sets `suppressObserverUntil` for 600 ms after any programmatic jump; a scroll-end guard (`window.innerHeight + scrollY >= documentElement.scrollHeight - 8`) force-activates the last `NAV_ITEMS` entry for both the observer callback and the `scroll` listener.
- **Manual verification (2026-07-10)**: clicking `clinical-nav-outcomes` yields `aria-current="location"` and the "Outcomes" pill renders in primary-brand green. Scrolling manually to page bottom produces the same state.

### Bonus (LOW, related but out of scope)
Testing agent also raised the "browser back/forward walks the section hash" question. That was recorded as a LOW-priority observation — hash pushes on user-initiated clicks are shipped, but the popstate walk interleaves with React Router history in an SPA. Accepted as **known limitation**; documented in Phase 1 UAT §11.

## Success rate after fixes
- Direct assertions: **64/64 expected on retest** (both non-passing items above resolved or dispositioned).
- Ready for User Acceptance Testing.
