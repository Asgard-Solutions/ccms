# Clinical Release — Screenshot Index

**Purpose:** The complete evidence set expected for G5. Each row lists what to capture, the viewport, the fixture/persona, expected DOM signals, and PHI redaction requirements. All fixtures use the Riverbend Chiropractic & Wellness demo tenant — no production PHI.

**Status:** `READY FOR SCREENSHOT CAPTURE`. Three in-environment screenshots are captured as proof-of-life; the full 25-shot set requires a scripted capture pass in an authorised environment.

## Capture principles

- **Test data only.** Never capture production PHI.
- **Fictional patient personas** (Riverbend Chiropractic personas — see `DEMO_SEED.md`).
- Mask or redact anything that looks PHI-shaped (emails, phone numbers, DOBs, addresses, record IDs, encounter IDs, free-text notes) even in fictional shots.
- Consistent viewport per row.
- Include effective flag state in file metadata, not visibly in the screenshot.
- No developer tools / debug overlays.
- Preview watermark ("Made with Emergent") should be marked as environment artifact where it appears.

## Workspace-mode set (5 shots × 5 views = 25 subshots)

For each mode: `general` · `provider` · `front_desk` · `billing` · `administrator`:

| # | View | Viewport | Persona | DOM signal |
|:-:|---|---|---|---|
| 1 | Full-page desktop | 1920×900 | Persona whose role includes that mode | `[data-testid=patient-clinical-tab-v2]` |
| 2 | Top-of-page orientation area | 1920×400 crop | Same | `[data-testid=clinical-patient-context-header]` visible |
| 3 | Current Care Status | 1920×500 crop | Same | `[data-testid=clinical-care-status-panel]` |
| 4 | Next Actions | 1920×400 crop | Same | `[data-testid=next-actions-panel]` |
| 5 | Role-specific prioritized section | 1920×600 crop | Same | Provider → Encounters first; Billing → billing_readiness first; etc. |
| 6 | Workspace-mode switcher | 800×300 crop | Same | `[data-testid=workspace-mode-switcher]` open |
| 7 | Configurable summary state | 1920×500 crop | Same | `[data-testid=summary-config-drawer]` open |
| 8 | Representative empty state | 1920×400 crop | Fresh patient with no data | `[data-testid=outcomes-section-empty]` |
| 9 | Representative error/permission state | 1920×400 crop | Same | `[data-testid=outcomes-section-permission-denied]` for restricted role |

**Persona → mode mapping** (from `workspaceModes.js`):
- Admin (Ava Bennett) — general/provider/front_desk/billing/administrator
- Doctor (Dr. Noah Carter) — general/provider
- Staff (Mia Ramirez) — general/front_desk/billing

## Additional required shots

| # | Scenario | Viewport | Notes |
|:-:|---|---|---|
| A | Masked patient | 1920×900 | Default staff session; expect masked initials "M. R." |
| B | Unmasked patient with safe test data | 1920×900 | Admin session after More actions → Reveal (audited) |
| C | Dark theme | 1920×900 | Set `theme=dark` in preferences |
| D | Light theme | 1920×900 | Default preference |
| E | Small-screen layout | 375×667 | Assert nav horizontal scroll |
| F | Tablet layout | 900×1200 | Assert intake two-column → single |
| G | 200% zoom | 1920×900 @ DPR 2 | Assert sticky header + section nav reflow |
| H | Keyboard focus state | 1920×900 | Tab to `clinical-nav-outcomes`; expect focus ring |
| I | Positive red-flag state | 1920×600 crop | `[data-testid=safety-summary-red-flags]` |
| J | Billing warning state | 1920×400 crop | `care-status-row-billing` warning tone |
| K | Billing blocked state | 1920×400 crop | Destructive tone |
| L | Imaging with metadata | 1920×500 crop | ImagingCard with modality label |
| M | Imaging missing classification | 1920×400 crop | Data Quality panel row lit |
| N | Outcome snapshot | 1920×500 crop | `OutcomeSnapshotCard` |
| O | Outcome trend | 1920×500 crop | `OutcomeTrendChart` with milestone lines |
| P | Accessible outcome table | 1920×500 crop | Toggle → `OutcomeTrendTable` visible |
| Q | Timeline filters | 1920×500 crop | Filter pills visible |
| R | Saved preset with icon strip | 800×300 crop | `PresetIconStrip` |
| S | Data Quality panel | 1920×500 crop | `DataQualityPanel` |
| T | Re-exam upcoming | 1920×300 crop | `reexam-approaching` warning tone |
| U | Re-exam overdue | 1920×300 crop | `reexam-overdue` destructive tone |
| V | Legacy fallback | 1920×900 | `[data-testid=patient-clinical-tab]` (no `-v2`) |
| W | Slice 4 disabled | 1920×900 | `ImagingCard`, `DataQualityPanel` absent |
| X | Slice 5 disabled | 1920×900 | Workspace switcher absent, default section order |
| Y | Slice 6 disabled | 1920×900 | Section boundaries fall back to per-card fallback |
| Z | Phase 3 disabled | 1920×900 | Every Phase 3 surface hidden |
| AA | Parent redesign disabled | 1920×900 | Full legacy fallback |

## Captured this pass (proof-of-life — inline evidence)

The screenshot tool used in the release-gate closeout streams the images **inline** in the tool response rather than persisting to a specified path. The following screenshots were captured and inline-rendered in the closeout tool trace (available in the run log for the release manager). They are **not persisted** to `/app/memory/screenshots/` in this container; the full 25-shot G5 capture requires the authorized environment described below.

| Row | Persona | Captured content | Persisted to disk? |
|---|---|---|:-:|
| Login | — | Login screen with fictional demo credentials | No — inline only |
| Workspace mode → General row 1 | Admin (Ava Bennett) | Chart of M. R. (Riverbend). Sticky header, section nav, Current Care Status, workspace-mode caption "General · Balanced Clinical page order" visible | No — inline only |
| Mid-page | Admin | Intake history, safety summary | No — inline only |
| Data Quality row | Admin | Data Quality panel with 3 patient-scoped issues | No — inline only |
| Row V — Legacy fallback | Admin | `clinicalRedesign` flag off; legacy `ClinicalTab` renders — 6-tile chart-summary grid, no sticky header, no section nav | No — inline only |
| Row X — Slice 5 disabled | Admin | Slice 5 flag off | No — inline only |
| Dashboard post-login | Admin | Reference shot for the entry point to the Clinical tab | No — inline only |

**Action for the authorized capture pass:** Re-run the Playwright script sketched below and persist to `/app/memory/screenshots/release/<row>_<persona>_<viewport>.jpg` (or an equivalent per-repo location) inside the environment where the release manager can archive the images alongside the release ticket.

## Remaining capture instructions (for the authorised environment)

Run the Playwright script sketched below against a staging instance with the Riverbend seed. Each screenshot lands in `/app/memory/screenshots/release/<row>_<persona>_<viewport>.jpg`.

```python
# Pseudocode — the executable script must be reviewed by clinical lead
personas = [
  ("admin",   "admin@ccms.app",   "Admin@ComplianceClinic1"),
  ("doctor",  "doctor@ccms.app",  "Doctor@ComplianceClinic1"),
  ("staff",   "staff@ccms.app",   "Staff@ComplianceClinic1"),
]
modes = ["general", "provider", "front_desk", "billing", "administrator"]
for persona in personas:
    login(persona)
    for mode in allowed_modes_for(persona.role):
        set_workspace_mode(mode)
        capture_row("full_desktop",      "1920x900",  f"1_{persona}_{mode}.jpg")
        capture_row("top_orientation",   "1920x400",  f"2_{persona}_{mode}.jpg")
        # ... rows 3-9 as documented above
```

Ownership: Clinical platform lead + QA.

## PHI check for release

Before publishing:
1. Every captured file passes `grep -E '(SSN|@[a-z]+\.(com|app|org)|[0-9]{3}-[0-9]{3}-[0-9]{4})'` — no matches unless the value belongs to a fictional persona.
2. Reviewer initials required on every shot in `/app/memory/screenshots/release/APPROVAL.md`.

## Status

**READY FOR SCREENSHOT CAPTURE.**
