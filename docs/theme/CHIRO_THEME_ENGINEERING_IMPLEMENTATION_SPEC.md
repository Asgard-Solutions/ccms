# Chiro Theme Engineering Implementation Spec

## 1. Purpose

This document translates the Chiro Software Theme Standard into implementation guidance for frontend engineering. It defines how the design system must be represented in CSS variables, Tailwind tokens, Shadcn UI overrides, and component usage patterns so that the application consistently adheres to the approved light and dark themes.

This is the engineering source of truth for theme implementation.

---

## 2. Implementation Goals

The theme layer must:

- support Light / Dark / System modes
- use semantic CSS variables as the single source of truth
- integrate cleanly with TailwindCSS and Shadcn UI
- prevent raw palette usage in feature code
- preserve accessibility and visual consistency across the full application
- enable predictable component theming without per-feature overrides

---

## 3. Existing Stack Alignment

Current frontend stack:

- React 19
- Create React App bundler
- React Router 6
- TailwindCSS with `darkMode: 'class'`
- CSS variable semantic tokens in `index.css`
- Shadcn UI / Radix primitives
- lucide-react
- sonner
- ThemeContext with per-user preference persisted through API

This architecture is correct and should remain the foundation.

---

## 4. Required Theme File Structure

Recommended frontend theme structure:

```text
src/
  styles/
    theme/
      foundations.css
      semantic-tokens.css
      component-tokens.css
      utilities.css
  components/
    ui/
  lib/
    theme.ts
```

### Preferred responsibility split

- `foundations.css` = raw palette, typography, spacing, radii, shadow primitives
- `semantic-tokens.css` = light and dark semantic variables
- `component-tokens.css` = specialized aliases for complex components
- `utilities.css` = optional shared utility classes only when tokens alone are not enough
- `theme.ts` = JS helpers for theme preference and runtime sync if needed

If the team prefers a single `index.css`, keep the same logical sections in the same order.

---

## 5. Token Architecture

### 5.1 Layer 1: Foundation tokens

Foundation tokens are raw design primitives and must only be declared centrally.

These include:

- palette ramps
- typography families
- spacing scale
- radius scale
- shadow scale

Feature code must not consume foundation palette tokens directly.

### 5.2 Layer 2: Semantic tokens

Semantic tokens represent UI meaning.

Examples:

- `--background`
- `--foreground`
- `--surface`
- `--card`
- `--border`
- `--primary`
- `--accent`
- `--destructive`
- `--focus`

All components should consume these through Tailwind mappings.

### 5.3 Layer 3: Component alias tokens

Create component tokens only for repeated component-specific needs.

Examples:

- `--sidebar-active-bg`
- `--sidebar-active-fg`
- `--table-row-hover`
- `--table-row-selected`
- `--calendar-today-ring`
- `--dialog-overlay`
- `--input-placeholder`
- `--badge-premium-bg`

This prevents reintroducing one-off color choices in component files.

---

## 6. Canonical CSS Variable Setup

## 6.1 Foundation tokens

```css
:root {
  --font-display: 'Outfit', ui-sans-serif, system-ui, sans-serif;
  --font-body: 'Manrope', ui-sans-serif, system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;

  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;
  --space-12: 48px;

  --radius-xs: 6px;
  --radius-sm: 8px;
  --radius-md: 10px;
  --radius-lg: 12px;
  --radius-xl: 14px;

  --shadow-xs: 0 1px 2px rgba(15, 23, 32, 0.06);
  --shadow-sm: 0 2px 6px rgba(15, 23, 32, 0.08);
  --shadow-md: 0 8px 24px rgba(15, 23, 32, 0.12);
  --shadow-lg: 0 16px 40px rgba(15, 23, 32, 0.16);

  --slate-950: #0F1720;
  --slate-900: #16202B;
  --slate-850: #1B2834;
  --slate-800: #223241;
  --slate-700: #314657;
  --slate-600: #496174;
  --slate-500: #6A8599;
  --slate-400: #93A8B8;
  --slate-300: #B8C6D1;
  --slate-200: #D7E0E7;
  --slate-100: #ECF2F6;
  --slate-50: #F6FAFC;

  --teal-900: #0F4E52;
  --teal-800: #116168;
  --teal-700: #14757C;
  --teal-600: #1A8A91;
  --teal-500: #23A1A8;
  --teal-400: #4CB5BA;
  --teal-300: #7ACACE;
  --teal-200: #A9DEE0;
  --teal-100: #D8F0F1;

  --copper-900: #6B432B;
  --copper-800: #845338;
  --copper-700: #9E6445;
  --copper-600: #B97856;
  --copper-500: #CF8E68;
  --copper-400: #DCA784;
  --copper-300: #E8C1A7;
  --copper-200: #F2DDD1;
  --copper-100: #FAF0EB;
}
```

## 6.2 Semantic tokens

```css
:root {
  --background: #F6FAFC;
  --foreground: #15202B;
  --surface: #FFFFFF;
  --surface-2: #F1F6F9;
  --surface-3: #EAF1F5;

  --card: #FFFFFF;
  --card-foreground: #15202B;
  --popover: #FFFFFF;
  --popover-foreground: #15202B;

  --muted: #ECF2F6;
  --muted-foreground: #53697A;

  --border: #D7E0E7;
  --border-strong: #B8C6D1;
  --input: #D7E0E7;
  --ring: #1A8A91;
  --focus: #23A1A8;

  --primary: #14757C;
  --primary-foreground: #FFFFFF;
  --primary-hover: #116168;
  --primary-active: #0F4E52;

  --secondary: #F1F6F9;
  --secondary-foreground: #15202B;
  --secondary-hover: #EAF1F5;

  --accent: #FAF0EB;
  --accent-foreground: #6B432B;
  --accent-strong: #B97856;

  --success: #249262;
  --success-foreground: #FFFFFF;
  --success-soft: #DDF4E9;

  --warning: #B27A14;
  --warning-foreground: #FFFFFF;
  --warning-soft: #F8EBCB;

  --destructive: #C54A56;
  --destructive-foreground: #FFFFFF;
  --destructive-soft: #F9E1E4;

  --info: #3678A8;
  --info-foreground: #FFFFFF;
  --info-soft: #DDECF7;

  --selection: rgba(35, 161, 168, 0.16);
}

.dark {
  --background: #111A22;
  --foreground: #EAF1F5;
  --surface: #16202B;
  --surface-2: #1B2834;
  --surface-3: #223241;

  --card: #16202B;
  --card-foreground: #EAF1F5;
  --popover: #1B2834;
  --popover-foreground: #EAF1F5;

  --muted: #223241;
  --muted-foreground: #AFC0CC;

  --border: #2B3B4A;
  --border-strong: #3A5063;
  --input: #314657;
  --ring: #4CB5BA;
  --focus: #7ACACE;

  --primary: #4CB5BA;
  --primary-foreground: #0F1720;
  --primary-hover: #7ACACE;
  --primary-active: #A9DEE0;

  --secondary: #223241;
  --secondary-foreground: #EAF1F5;
  --secondary-hover: #2B3B4A;

  --accent: #3A2A23;
  --accent-foreground: #F1D2C2;
  --accent-strong: #DCA784;

  --success: #5EBB87;
  --success-foreground: #0F1720;
  --success-soft: rgba(94, 187, 135, 0.18);

  --warning: #D4A34A;
  --warning-foreground: #0F1720;
  --warning-soft: rgba(212, 163, 74, 0.18);

  --destructive: #DE6A76;
  --destructive-foreground: #0F1720;
  --destructive-soft: rgba(222, 106, 118, 0.18);

  --info: #6AA9D4;
  --info-foreground: #0F1720;
  --info-soft: rgba(106, 169, 212, 0.18);

  --selection: rgba(122, 202, 206, 0.2);
}
```

## 6.3 Component alias tokens

```css
:root {
  --sidebar-bg: var(--surface);
  --sidebar-fg: var(--foreground);
  --sidebar-active-bg: var(--muted);
  --sidebar-active-fg: var(--foreground);
  --sidebar-active-indicator: var(--primary);

  --table-row-hover: var(--surface-2);
  --table-row-selected: var(--teal-100);
  --table-header-bg: var(--surface-2);
  --table-header-fg: var(--muted-foreground);

  --input-placeholder: #6A8599;
  --dialog-overlay: rgba(15, 23, 32, 0.5);
  --calendar-today-ring: var(--primary);
  --calendar-slot-selected: var(--teal-100);
  --badge-premium-bg: var(--copper-100);
  --badge-premium-fg: var(--copper-900);
}

.dark {
  --sidebar-bg: var(--surface);
  --sidebar-fg: var(--foreground);
  --sidebar-active-bg: var(--surface-2);
  --sidebar-active-fg: var(--foreground);
  --sidebar-active-indicator: var(--primary);

  --table-row-hover: var(--surface-2);
  --table-row-selected: #1F3A3F;
  --table-header-bg: var(--surface-2);
  --table-header-fg: var(--muted-foreground);

  --input-placeholder: #93A8B8;
  --dialog-overlay: rgba(5, 10, 14, 0.65);
  --calendar-today-ring: var(--primary);
  --calendar-slot-selected: #234247;
  --badge-premium-bg: #3A2A23;
  --badge-premium-fg: #F1D2C2;
}
```

---

## 7. Base Global Styles

Recommended global styling in `index.css`:

```css
html {
  color-scheme: light;
}

html.dark {
  color-scheme: dark;
}

body {
  background: var(--background);
  color: var(--foreground);
  font-family: var(--font-body);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
}

::selection {
  background: var(--selection);
}

* {
  border-color: var(--border);
}
```

### Typography utility classes

```css
.font-display { font-family: var(--font-display); }
.font-body { font-family: var(--font-body); }
.font-mono { font-family: var(--font-mono); }
```

### Accessible focus utility

```css
.focus-ring {
  outline: none;
  box-shadow: 0 0 0 2px var(--background), 0 0 0 4px var(--focus);
}
```

---

## 8. Tailwind Theme Mapping

## 8.1 Required config direction

Tailwind should expose semantic colors by mapping to CSS variables.

### Example `tailwind.config.js` direction

```js
module.exports = {
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        background: 'var(--background)',
        foreground: 'var(--foreground)',
        surface: 'var(--surface)',
        'surface-2': 'var(--surface-2)',
        'surface-3': 'var(--surface-3)',
        card: 'var(--card)',
        'card-foreground': 'var(--card-foreground)',
        popover: 'var(--popover)',
        'popover-foreground': 'var(--popover-foreground)',
        muted: 'var(--muted)',
        'muted-foreground': 'var(--muted-foreground)',
        border: 'var(--border)',
        'border-strong': 'var(--border-strong)',
        input: 'var(--input)',
        ring: 'var(--ring)',
        primary: 'var(--primary)',
        'primary-foreground': 'var(--primary-foreground)',
        secondary: 'var(--secondary)',
        'secondary-foreground': 'var(--secondary-foreground)',
        accent: 'var(--accent)',
        'accent-foreground': 'var(--accent-foreground)',
        success: 'var(--success)',
        'success-foreground': 'var(--success-foreground)',
        warning: 'var(--warning)',
        'warning-foreground': 'var(--warning-foreground)',
        destructive: 'var(--destructive)',
        'destructive-foreground': 'var(--destructive-foreground)',
        info: 'var(--info)',
        'info-foreground': 'var(--info-foreground)',
      },
      borderRadius: {
        xs: 'var(--radius-xs)',
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)',
      },
      boxShadow: {
        xs: 'var(--shadow-xs)',
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
      },
      fontFamily: {
        display: ['var(--font-display)'],
        body: ['var(--font-body)'],
        mono: ['var(--font-mono)'],
      },
    },
  },
};
```

## 8.2 Approved utility usage

Approved patterns:

- `bg-background text-foreground`
- `bg-card text-card-foreground`
- `border-border`
- `bg-primary text-primary-foreground`
- `bg-secondary text-secondary-foreground`
- `text-muted-foreground`
- `shadow-sm`
- `rounded-sm`, `rounded-lg`
- `font-display`, `font-body`, `font-mono`

Disallowed patterns:

- `text-slate-500`
- `bg-blue-600`
- `dark:bg-zinc-900`
- arbitrary hex values in class strings
- utility classes referencing colors not mapped to theme tokens

---

## 9. Shadcn UI Integration Rules

Shadcn UI components must inherit semantic tokens and approved radii.

### Required baseline updates

- button variants mapped to semantic theme colors
- input, select, textarea, dialog, dropdown, popover, tooltip, tabs, table, and badge styles aligned to token system
- default rounded values updated to 8px or 12px depending on component type
- focus states normalized across all interactive primitives

### Specific overrides to apply

- `Button`: primary, secondary, outline, ghost, destructive variants aligned to theme tokens
- `Input` / `Textarea` / `SelectTrigger`: 8px radius, visible border, focus ring utility
- `DialogContent`: 12px radius, tokenized background, border, and medium shadow
- `DropdownMenuContent`: card/popover tokens, clear hover state, accessible selected state
- `TabsTrigger`: visible active state, compact padding, consistent hover treatment
- `Badge`: semantic variants, restrained shape
- `Table`: header, row hover, row selected styles use component alias tokens

---

## 10. Sonner Toast Theming

Toasts must match the theme system.

### Rules

- default toast uses card/popover surface treatment
- success, warning, info, and destructive toasts use semantic state tokens
- icons must match state meaning
- shadows should remain subtle
- text hierarchy must remain readable in both themes

### Do not

- use bright gradients
- use raw red/green/yellow fills outside semantic tokens
- style toasts independently from the system

---

## 11. ThemeContext and Runtime Behavior

Theme behavior should follow this logic:

- `light` => remove `dark` class from `html`
- `dark` => add `dark` class to `html`
- `system` => evaluate `prefers-color-scheme`
- persist preference in user profile via existing preferences endpoint
- hydrate early to avoid theme flash

### Recommendation

Apply theme class before app paint whenever possible.

Use a startup script or hydration-safe inline logic so users do not get flashbang mode at 7:00 AM chart review.

---

## 12. Component Implementation Standards

## 12.1 Buttons

Canonical class direction:

```jsx
<Button className="h-10 rounded-sm font-body text-sm font-semibold shadow-xs focus-ring" />
```

Variant mapping should be centralized inside the Button component, not repeated in feature code.

## 12.2 Inputs

Canonical traits:

- `h-10`
- `rounded-sm`
- `border border-border`
- `bg-surface`
- `text-foreground`
- `placeholder:text-muted-foreground`
- `focus-ring`

## 12.3 Cards

Canonical traits:

- `rounded-lg`
- `border border-border`
- `bg-card`
- `text-card-foreground`
- `shadow-xs` or no shadow based on context

## 12.4 Tables

Canonical traits:

- container: rounded-lg border bg-card
- header: muted surface with muted foreground labels
- rows: hover and selected use alias tokens
- cells: compact vertical padding and readable text sizing

---

## 13. Special Component Recommendations

## 13.1 Sidebar

Use component alias tokens:

- background = `--sidebar-bg`
- active background = `--sidebar-active-bg`
- active text = `--sidebar-active-fg`
- active indicator = `--sidebar-active-indicator`

Sidebar active state should feel clear, not loud.

## 13.2 Patient Search Results

Use compact search rows with:

- clear typography hierarchy
- subtle hover background
- selected row state distinct from hover
- restrained highlight treatment for matched terms

### Highlight recommendation

- light theme: subtle teal-tinted highlight background
- dark theme: muted teal surface tint
- avoid bright yellow search highlighting

## 13.3 Scheduler

Use semantic appointment statuses only.

Recommended alias tokens:

- `--calendar-event-confirmed`
- `--calendar-event-pending`
- `--calendar-event-cancelled`
- `--calendar-slot-selected`
- `--calendar-today-ring`

Calendar color meaning must remain stable across themes.

---

## 14. Accessibility Implementation Rules

### Required engineering checks

- visible keyboard focus on all interactive controls
- no placeholder-only labels for important fields
- contrast validated for small text and status chips
- icon-only buttons include accessible labels
- row selection is not indicated by color alone where action context matters
- disabled states remain visibly distinct without disappearing

### Testing recommendation

At minimum validate:

- patient search
- scheduling calendar
- tables with dense metadata
- form validation states
- dialogs and dropdown menus
- toasts and alerts
- dark mode on laptop brightness below 50 percent

---

## 15. Guardrails and Linting

Engineering should add automated checks where practical.

### Recommended guardrails

1. Block raw hex colors in JSX, TSX, and component CSS except theme files.
2. Block Tailwind raw palette classes outside design-system files.
3. Flag inline style color usage.
4. Require both light and dark screenshots for major UI changes.
5. Require token usage for any new variant component.

### Candidate lint enforcement patterns

- regex for `#[0-9A-Fa-f]{3,8}` outside theme files
- regex for `bg-(red|blue|green|slate|zinc|gray)-` outside approved locations
- regex for `text-(red|blue|green|slate|zinc|gray)-`

---

## 16. Migration Plan

Recommended rollout order:

### Phase 1

- finalize token values in `index.css` or split theme files
- align Tailwind config to semantic tokens
- normalize body/background/text styles

### Phase 2

- refactor core Shadcn primitives: Button, Input, Textarea, Select, Card, Dialog, DropdownMenu, Tabs, Badge
- add standardized focus ring behavior

### Phase 3

- update high-traffic app areas: app shell, sidebar, patient search, patient profile, scheduler, tables

### Phase 4

- update billing, analytics, reports, admin surfaces
- apply alias tokens to specialized modules

### Phase 5

- add lint checks and PR checklist enforcement
- remove legacy palette usage and dead CSS

---

## 17. Definition of Done for Theme Compliance

A component or screen is compliant only if:

- it uses semantic tokens only
- it renders correctly in light and dark themes
- it preserves focus visibility
- it follows typography, spacing, and radius standards
- it does not introduce raw palette classes or hex values
- it has accessible contrast
- it matches compact operational density expectations

If any of these fail, the work is not done.

---

## 18. Example `index.css` Section Order

Recommended order:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/* 1. Font imports */
/* 2. Foundation tokens */
/* 3. Light semantic tokens */
/* 4. Dark semantic tokens */
/* 5. Component alias tokens */
/* 6. Base element styles */
/* 7. Shared utilities */
```

Keeping the file organized matters. Theme systems rot fastest when everything gets dumped into one giant CSS junk drawer.

---

## 19. Final Engineering Rule

Feature code may choose layout and composition. It may not choose new colors, new radii, new shadows, or new interaction language outside the approved token system.

The theme layer owns those decisions.
