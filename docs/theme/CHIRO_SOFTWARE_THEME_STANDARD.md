# Chiro Software Theme Standard

## 1. Purpose

This document defines the visual design system for the Chiro Software application. It establishes the typography, color system, spacing, radius, elevation, states, and implementation rules required to maintain a consistent, accessible, premium, and operationally efficient user experience across the entire product.

This standard is mandatory for all application surfaces, including:

- app shell
- dashboards
- patient search and lookup
- patient profile and clinical records
- scheduling and calendar views
- billing and financial screens
- forms and wizards
- modals and dialogs
- tables and dense operational views
- alerts and notifications
- mobile-responsive layouts
- exports and print-friendly screens where applicable

The application must use semantic design tokens only. Direct use of raw color values in components is not allowed except inside the centralized theme/token layer.

---

## 2. Brand Positioning

### Visual direction

The product should feel:

- premium and polished
- operationally efficient
- modern and trustworthy
- clinically professional without looking cold or institutional
- structured and precise, not playful or overly decorative

### What the interface should not feel like

The product must not feel:

- neon or trendy for trend’s sake
- generic hospital software
- consumer wellness spa software
- cartoonish, soft, bubbly, or toy-like
- overloaded with saturated blues or medical greens

### Product mood

The visual language should communicate:

- speed
- clarity
- confidence
- discipline
- readability
- calm control under heavy operational load

---

## 3. Core Principles

1. **Readability first**  
   All decisions must preserve legibility in dense workflows, especially patient lists, scheduling, tables, and forms.

2. **Accessibility is required**  
   WCAG AA contrast compliance is a hard requirement for text, controls, focus states, and essential UI boundaries.

3. **Semantic over decorative**  
   Color should communicate meaning, hierarchy, and interaction state before branding flair.

4. **Premium restraint**  
   The UI should feel refined through discipline, spacing, hierarchy, and contrast rather than visual excess.

5. **System-driven consistency**  
   Components must consume centralized semantic tokens. No one-off styling decisions in feature code.

6. **Compact efficiency**  
   The product should support high information density while leaving enough breathing room to avoid fatigue.

---

## 4. Theme Direction

Selected palette direction:

- **Primary direction:** Slate + Teal + Copper
- **Light theme:** slightly cool / blue-white
- **Dark theme:** brand-tinted charcoal
- **Secondary accent:** warmer premium copper / bronze

This combination supports a premium, trustworthy, and differentiated chiropractic software brand without relying on generic healthcare blue or hospital green.

---

## 5. Typography System

### Approved typefaces

- **Display / headings:** Outfit
- **Body / interface copy:** Manrope
- **Code / technical values:** JetBrains Mono

### Typography principles

- Use Outfit only for headings, section titles, major numeric callouts, and branded moments.
- Use Manrope for all body text, labels, table text, helper text, inputs, and controls.
- Use JetBrains Mono only for technical values such as IDs, audit references, timestamps where fixed alignment matters, and code-like displays.

### Font stack

```css
--font-display: 'Outfit', ui-sans-serif, system-ui, sans-serif;
--font-body: 'Manrope', ui-sans-serif, system-ui, sans-serif;
--font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;
```

### Type scale

#### Display and heading scale

- Display XL: 36px / 40px / 700 / Outfit
- Display L: 30px / 36px / 700 / Outfit
- H1: 24px / 32px / 650 / Outfit
- H2: 20px / 28px / 650 / Outfit
- H3: 18px / 24px / 600 / Outfit
- H4: 16px / 22px / 600 / Outfit

#### Body and UI scale

- Body L: 16px / 24px / 500 / Manrope
- Body M: 14px / 20px / 500 / Manrope
- Body S: 13px / 18px / 500 / Manrope
- Caption: 12px / 16px / 500 / Manrope
- Micro: 11px / 14px / 500 / Manrope

#### Control text

- Button default: 14px / 20px / 600
- Input text: 14px / 20px / 500
- Input label: 12px / 16px / 600
- Table header: 12px / 16px / 700
- Table cell: 13px / 18px / 500

### Typography rules

- Avoid ultra-light font weights.
- Avoid using all caps for long labels.
- Reserve letter-spacing adjustments for headings and micro-labels only.
- Use tabular numbers where alignment matters in tables, billing, and analytics.

---

## 6. Density and Layout

### Density target

The application uses a **compact operational density** model.

This means:

- efficient row heights
- tighter vertical rhythm than consumer apps
- enough whitespace to maintain scanability
- forms and tables optimized for daily repeated use

### Layout spacing scale

```css
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-5: 20px;
--space-6: 24px;
--space-8: 32px;
--space-10: 40px;
--space-12: 48px;
```

### Spacing rules

- Use 8px as the base rhythm.
- Prefer 12px and 16px for internal component padding.
- Use 20px to 24px between major grouped sections.
- Dense tables and filter bars may use 8px vertical spacing.
- Avoid large empty gaps that reduce information density without improving comprehension.

### Recommended component sizing

- Toolbar height: 48px to 56px
- Input height: 40px default, 36px for dense inline filtering
- Button height: 40px default, 36px secondary dense action
- Table row height: 40px to 44px default
- Modal padding: 24px
- Card padding: 16px to 20px

---

## 7. Shape Language

### Radius standard

Use restrained, balanced radius values.

```css
--radius-xs: 6px;
--radius-sm: 8px;
--radius-md: 10px;
--radius-lg: 12px;
--radius-xl: 14px;
```

### Radius rules

- Inputs: 8px
- Buttons: 8px
- Selects / dropdown triggers: 8px
- Cards: 12px
- Modals / dialogs / drawers: 12px
- Tables: outer container may use 12px, rows should remain visually crisp
- Badges / chips: 8px or 999px only when semantic pills require it

### Shape principle

Corners should feel refined and controlled, not soft and playful.

---

## 8. Color System

## 8.1 Brand Palette

These are foundational palette values. Components must not use them directly. Components must use semantic tokens defined later.

### Core brand hues

```css
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
--slate-50:  #F6FAFC;

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

--success-700: #1E7A52;
--success-600: #249262;
--success-100: #DDF4E9;

--warning-700: #9A6A12;
--warning-600: #B27A14;
--warning-100: #F8EBCB;

--danger-700: #B03E4A;
--danger-600: #C54A56;
--danger-100: #F9E1E4;

--info-700: #2B658F;
--info-600: #3678A8;
--info-100: #DDECF7;
```

## 8.2 Light Theme Semantic Tokens

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

  --focus: #23A1A8;
  --selection: rgba(35, 161, 168, 0.16);
}
```

## 8.3 Dark Theme Semantic Tokens

```css
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

  --focus: #7ACACE;
  --selection: rgba(122, 202, 206, 0.20);
}
```

---

## 9. Color Usage Rules

### Primary teal usage

Use primary teal for:

- primary buttons
- active navigation indicators
- interactive highlights
- focus rings
- selected states where emphasis is required
- links when elevated above default text hierarchy

Do not use primary teal as a background for large surfaces.

### Copper usage

Use copper sparingly for:

- premium highlights
- selected tabs in specialized contexts where brand warmth is beneficial
- badges for premium or billing-adjacent emphasis
- subtle visual distinction in empty states or key summaries

Do not use copper for destructive, warning, or primary system actions. Copper is a brand accent, not a meaning substitute.

### Neutral slate usage

Use slate neutrals for:

- background structure
- surfaces
- borders
- text hierarchy
- table chrome
- dividers
- secondary controls

### Status color usage

Use semantic status colors only for system meaning:

- success = completed, paid, active, verified
- warning = pending, attention, review needed
- destructive = errors, failures, cancellation, deletion risk
- info = neutral notices, informational banners, system context

Do not repurpose status colors for brand decoration.

---

## 10. Accessibility Standards

### Required standard

All application text and core controls must meet WCAG AA contrast requirements.

### Minimum rules

- Body text must maintain AA contrast against all backgrounds.
- Small text under 14px must receive extra scrutiny.
- Placeholder text must remain readable and should never be mistaken for disabled text.
- Focus indicators must be clearly visible in both light and dark themes.
- Border-only controls must remain distinguishable without relying on low-contrast outlines.
- State cannot be communicated by color alone. Use icons, labels, or shape changes where appropriate.

### Focus treatment

Use a visible, accessible focus ring on all keyboard-focusable elements.

Recommended pattern:

- 2px outer ring
- 1px supporting border or shadow separation
- theme-aware focus token

Example concept:

```css
box-shadow: 0 0 0 2px var(--background), 0 0 0 4px var(--focus);
```

---

## 11. Elevation, Borders, and Shadows

### Principle

The product should rely primarily on contrast, borders, and layered surfaces rather than heavy shadow.

### Shadow scale

```css
--shadow-xs: 0 1px 2px rgba(15, 23, 32, 0.06);
--shadow-sm: 0 2px 6px rgba(15, 23, 32, 0.08);
--shadow-md: 0 8px 24px rgba(15, 23, 32, 0.12);
--shadow-lg: 0 16px 40px rgba(15, 23, 32, 0.16);
```

### Usage rules

- Cards: border first, subtle shadow optional
- Popovers/dropdowns: small shadow + border
- Dialogs: medium shadow + clear boundary
- Sticky headers/toolbars: use border and backdrop before shadow
- Avoid dramatic floating effects

---

## 12. Component Standards

## 12.1 Buttons

### Variants

- Primary
- Secondary
- Ghost
- Outline
- Destructive
- Link

### Button rules

- Default button height: 40px
- Dense button height: 36px
- Radius: 8px
- Font: Manrope 14px 600
- Horizontal padding: 12px to 16px

### Primary button

- uses `--primary`
- high contrast text
- hover and active use tokenized darker/lighter states

### Secondary button

- neutral surface-based button
- not visually louder than primary

### Ghost and link buttons

- must still show clear hover and focus states
- avoid invisible actions in dense workflows

### Destructive button

- reserved for delete, irreversible actions, or dangerous state transitions

## 12.2 Inputs and Form Controls

### Rules

- Height: 40px default
- Dense filters: 36px allowed
- Radius: 8px
- Border must remain visible in both themes
- Placeholder text must be subdued but readable
- Error/help text must sit directly beneath the control

### States

- default
- hover
- focus
- filled
- disabled
- error
- success where appropriate

### Labels

- labels are always visible for key inputs
- placeholder text must not serve as the only label
- required indicators must be consistent across the application

## 12.3 Tables and Data Grids

### Principles

Tables are core to product usability and should feel efficient, scannable, and calm.

### Rules

- Use compact but readable row heights
- Default cell text: 13px Manrope
- Header text: 12px semibold or bold
- Support zebra striping only if it improves readability; keep it subtle
- Row hover should be visible but not loud
- Selected row state must be obvious in both themes
- Sticky headers should maintain a distinct layered surface
- Use mono numerals for aligned financial or audit values

### Avoid

- overusing border lines on every axis
- loud row hover colors
- low-contrast metadata text that becomes unreadable in dense screens

## 12.4 Cards and Panels

- Radius: 12px
- Padding: 16px to 20px
- Use layered surfaces and borders to separate sections
- Cards should not feel like consumer tiles
- Avoid oversized padding in operational screens

## 12.5 Dialogs and Drawers

- Radius: 12px
- Padding: 24px
- Strong title hierarchy
- Clear footer action alignment
- Destructive actions separated from default actions
- Overlay should dim without crushing readability

## 12.6 Tabs

- Support compact navigation for dense workflows
- Selected state must be obvious through color and weight, not color alone
- Copper accent may be used sparingly for premium, summary, or executive views, not general navigation

## 12.7 Badges and Status Chips

- Use restrained shapes
- Use semantic color mapping
- Text must remain readable at small sizes
- Avoid overly saturated fills

## 12.8 Alerts and Notifications

- Use semantic fills and borders with clear iconography
- Alert tone should be informative, not alarming unless necessary
- Sonner toasts must follow semantic token colors and typography rules

## 12.9 Navigation

### Side navigation

- active item uses primary emphasis
- icons must be clear at small sizes
- navigation groups should use visual hierarchy, not decorative dividers everywhere

### Top bars / subheaders

- should reinforce structure and context
- use borders and surfaces to separate levels

---

## 13. Iconography

### Icon set

Use lucide-react only unless a justified exception is approved.

### Icon rules

- default sizes: 16px, 18px, 20px
- align icons consistently with text baselines
- use icons to support recognition, not replace labels in dense workflows without tooltips
- avoid mixing icon stroke weights or styles from multiple libraries

---

## 14. Data Visualization

Charts and analytics must remain readable in both themes.

### Rules

- neutral axes and labels
- primary teal for primary series
- copper only for secondary emphasis or benchmark/reference series
- status colors reserved for semantic meaning
- do not overload charts with too many saturated colors
- dark mode charts require deliberate contrast tuning for gridlines and labels

### Preferred chart palette order

1. primary teal
2. muted teal
3. copper accent
4. info blue
5. slate neutral series

---

## 15. Light and Dark Theme Behavior

### General behavior

The themes must feel like the same product, not two unrelated skins.

### Light theme

- cooler white backgrounds
- stronger surface contrast through subtle layering
- dark slate text for clarity

### Dark theme

- charcoal with brand tint
- never pure black for standard surfaces
- preserve depth through layer contrast, borders, and subtle shadows
- ensure low-priority text remains readable

### Theme parity rules

- semantic meaning must remain equivalent across themes
- interaction states must remain equally visible
- spacing, radius, and component behavior must not change by theme
- charts, tables, and dialogs must be validated separately in dark mode

---

## 16. CSS Variable and Token Architecture

The application already uses Tailwind with CSS variable semantic tokens. This is the correct architecture and must remain the standard.

### Required token structure

#### Foundation tokens

Raw palette values defined in one centralized theme file.

#### Semantic tokens

Mapped values for application meaning:

- background
- foreground
- surface
- border
- primary
- accent
- success
- warning
- destructive
- info
- focus

#### Component tokens where needed

For specialized or difficult components, define component-level aliases rather than hardcoding colors.

Examples:

- `--table-row-hover`
- `--sidebar-active-bg`
- `--calendar-today-ring`
- `--patient-banner-critical-bg`

### Prohibited practices

- no raw hex values in JSX class strings
- no feature-specific one-off palette additions without design-system review
- no inline style color declarations unless dynamically computed and approved
- no hardcoded dark-mode overrides outside the token system

---

## 17. Tailwind Implementation Guidance

### Tailwind usage rule

Tailwind utility classes must reference semantic tokens, not raw palette values.

### Example token mapping direction

Use patterns such as:

- `bg-background`
- `text-foreground`
- `bg-card`
- `text-muted-foreground`
- `border-border`
- `ring-ring`
- `bg-primary`
- `text-primary-foreground`

### Recommended extension categories

- colors mapped to CSS vars
- border radius mapped to token vars
- box shadow mapped to token vars
- font family mapped to approved fonts

---

## 18. Component Library Rules for Shadcn UI

Shadcn UI components must be adapted to this system, not used in stock form without theme alignment.

### Rules

- all components inherit semantic tokens
- default radii updated to system values
- focus rings use global focus treatment
- destructive variants use approved destructive tokens
- dialogs, menus, popovers, and tooltips must be validated in both themes
- dropdowns must preserve contrast for selected, hovered, and disabled items

---

## 19. Enforcement Rules

To ensure the application always adheres to this theme system, the following rules are mandatory.

### Design and engineering compliance rules

1. All UI colors must come from semantic tokens.
2. New components must support light and dark mode before merge.
3. New components must show default, hover, focus, disabled, and error/destructive states where applicable.
4. No component may ship without keyboard focus visibility.
5. No low-contrast placeholder or metadata text may be merged.
6. No feature team may create a new semantic color without design-system approval.
7. Typography must use approved families and scale.
8. Radius, spacing, and shadows must follow tokenized standards.
9. Table and form density must remain consistent with the compact operational model.
10. Any exception must be documented and reviewed.

### Pull request checklist

Every UI-related PR must confirm:

- uses semantic tokens only
- works in light and dark theme
- meets accessibility contrast requirements
- preserves focus states
- matches spacing and radius standards
- avoids raw hex values and custom one-off styles
- has screenshots for both themes when visual impact is meaningful

### ESLint / code review guidance

Engineering should add guardrails where possible to detect:

- raw hex color usage in JSX/TSX/CSS outside theme files
- arbitrary Tailwind color utilities outside approved mappings
- unauthorized inline style color properties

---

## 20. Special Workflow Guidance

## 20.1 Patient Search and Lookup

This area is mission-critical and should emphasize speed and scanability.

- prioritize input clarity and response visibility
- search results should use high-contrast text hierarchy
- matched text highlighting should use restrained primary emphasis
- selected results should be clearly visible without becoming loud
- filters should remain compact and aligned

## 20.2 Scheduler / Calendar

- distinguish appointment states semantically
- avoid oversaturated calendars
- current day and selected slot must be clearly visible in both themes
- maintain readability under heavy event density

## 20.3 Billing and Financial Views

- numeric alignment must be precise
- use mono numerals for currency-heavy tables where helpful
- success, warning, and overdue states must be unmistakable
- copper may be used sparingly for premium summary emphasis, not accounting meaning

## 20.4 Clinical and Patient Detail Screens

- structure must reduce cognitive load
- section boundaries should be visible but not heavy
- labels and values must remain easy to scan over long sessions

---

## 21. Print and Export Guidance

Where screens or reports are exported:

- prefer light theme print rendering unless a dark report is intentional
- ensure text remains high contrast when converted to PDF
- do not rely on dark-mode colors for printed meaning
- charts and tables should remain readable in grayscale where possible

---

## 22. Example Design Tokens for Engineering

```css
:root {
  --font-display: 'Outfit', ui-sans-serif, system-ui, sans-serif;
  --font-body: 'Manrope', ui-sans-serif, system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;

  --radius-input: 8px;
  --radius-button: 8px;
  --radius-card: 12px;
  --radius-dialog: 12px;

  --shadow-card: 0 2px 6px rgba(15, 23, 32, 0.08);
  --shadow-dialog: 0 8px 24px rgba(15, 23, 32, 0.12);

  --table-row-hover: #F1F6F9;
  --table-row-selected: #D8F0F1;
}

.dark {
  --table-row-hover: #1B2834;
  --table-row-selected: #1F3A3F;
}
```

---

## 23. Do Not Use

The following are prohibited unless explicitly approved:

- bright neon accents
- generic medical green as primary brand color
- fully rounded playful controls
- pure black backgrounds for standard dark mode surfaces
- low-contrast gray text on gray surfaces
- random one-off dashboard colors
- all-caps labels for normal UI controls
- oversized shadows
- giant padding that reduces operational efficiency
- mixed font families outside the approved set

---

## 24. Final Standard

Chiro Software must present as a premium, disciplined, efficient healthcare platform. The interface should support long-duration operational use with high readability, stable visual hierarchy, and strong consistency across every workflow.

The theme is not a suggestion. It is a product standard.

Every screen must inherit from this system. Every component must use tokenized semantics. Every new feature must maintain visual and behavioral parity across light and dark themes.

If a UI decision conflicts with readability, accessibility, or consistency, readability, accessibility, and consistency win.

