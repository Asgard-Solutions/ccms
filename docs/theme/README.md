# Chiro Software — Theme System

This directory is the canonical source of truth for the Chiro Software design system. It contains three binding documents that engineering, design, and QA must all follow.

## Documents

1. **[CHIRO_SOFTWARE_THEME_STANDARD.md](./CHIRO_SOFTWARE_THEME_STANDARD.md)**
   Product-level design standard. Defines brand, typography, color palette (Slate + Teal + Copper), spacing, radius, shadows, accessibility, and enforcement rules.

2. **[CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md](./CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md)**
   Engineering translation of the standard. CSS variables, Tailwind config mapping, Shadcn UI override rules, Sonner theming, ThemeContext behavior, and migration plan.

3. **[CHIRO_UI_REVIEW_AND_COMPLIANCE_CHECKLIST.md](./CHIRO_UI_REVIEW_AND_COMPLIANCE_CHECKLIST.md)**
   Pass/fail review tool for PRs, design reviews, and QA.

## Rule of adherence

> The theme is not a suggestion. It is a product standard.

- All UI colors **must** come from semantic tokens.
- No raw hex values or raw Tailwind palette classes (`bg-blue-600`, `text-slate-500`) are permitted in feature code.
- New components must render correctly in both light and dark themes before merge.
- Every interactive element must show a visible focus state.
- Compact operational density is the default; do not pad like a marketing site.

## Where to find the implementation

| Concern | File |
| --- | --- |
| Foundation + semantic + alias tokens | `/app/frontend/src/index.css` |
| Tailwind mapping | `/app/frontend/tailwind.config.js` |
| Runtime theme switcher | `/app/frontend/src/contexts/ThemeContext.jsx` |
| Shadcn primitives | `/app/frontend/src/components/ui/` |

## Changelog discipline

Any change to `frontend/**` — including token tweaks — requires a `CHANGELOG.md` entry and may require a theme/policy doc update per `/app/docs/doc_rules.yml`.
