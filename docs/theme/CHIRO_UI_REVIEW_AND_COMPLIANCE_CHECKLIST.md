# Chiro UI Review and Compliance Checklist

## 1. Purpose

This checklist is the required review standard for all UI work in Chiro Software. It exists to ensure that every screen, component, workflow, and visual change adheres to the approved theme system, accessibility requirements, and operational design principles.

Use this checklist for:

- pull requests
- design reviews
- QA validation
- feature acceptance
- regression checks after refactors

This document is intended to be used as a pass/fail review tool, not as optional guidance.

---

## 2. Core Review Standard

A UI change is not complete unless it:

- follows the theme token system
- works in both light and dark modes
- meets accessibility expectations
- preserves visual consistency
- supports compact operational workflows
- avoids one-off styling

If a reviewer cannot confidently answer yes to the relevant checklist items, the change should not be approved.

---

## 3. Global Pass / Fail Questions

### Must all be yes

- Does the UI use approved semantic tokens instead of raw colors?
- Does it render correctly in both light and dark themes?
- Does it preserve readable contrast for all text and controls?
- Does every interactive element show a visible focus state?
- Does it follow approved typography, spacing, and radius rules?
- Does it match the premium, polished, operational design direction?
- Does it avoid playful, overly rounded, neon, or generic hospital styling?
- Does it keep the interface efficient and readable under dense usage?

If any answer is no, the review fails.

---

## 4. Theme Token Compliance

### Required checks

- No raw hex color values in component code.
- No direct Tailwind palette classes like `bg-blue-600`, `text-slate-500`, or `dark:bg-zinc-900`.
- All backgrounds, borders, text, and states use semantic theme classes or approved component aliases.
- No one-off feature-specific colors have been introduced.
- Any specialized visual state uses an approved component token.

### Reviewer questions

- Is this component styling driven by the theme system?
- Could the same component survive a palette refresh without code changes?
- Did the author add visual values in feature code that should belong in tokens?

### Fail examples

- Random hardcoded `#f3f4f6`
- A special-case purple badge for one page only
- A dark-mode override directly inside a feature component
- Tailwind arbitrary color values in JSX

---

## 5. Light and Dark Theme Parity

### Required checks

- The UI works in light theme.
- The UI works in dark theme.
- Meaning remains consistent across themes.
- Hover, selected, active, disabled, and error states remain visible in both themes.
- Popovers, dialogs, dropdowns, tables, charts, and toasts were checked in both themes.

### Reviewer questions

- Does the dark theme feel like the same product, not a different skin?
- Do subtle borders disappear in dark mode?
- Does low-priority text become unreadable in dark mode?
- Do selected states still read clearly?

### Fail examples

- Borders disappear on dark surfaces
- Selected rows become indistinguishable from hover rows
- Dialog overlays crush contrast and readability
- Dark mode looks acceptable only on one specific screen

---

## 6. Accessibility and Contrast

### Required checks

- Body text meets WCAG AA contrast expectations.
- Small text remains readable.
- Placeholder text is visible and not mistaken for disabled text.
- Disabled controls remain recognizable.
- Focus states are obvious for keyboard users.
- Important state is not communicated by color alone.
- Icon-only buttons have accessible labels.

### Reviewer questions

- Could a tired user at the end of the day still read this quickly?
- Does the component rely only on subtle color shifts for meaning?
- Can keyboard users track focus without guesswork?

### Fail examples

- Light gray text on muted gray backgrounds
- Placeholder text disappearing in dark mode
- Focus state visible only through a tiny border change
- Error state using color alone without message or icon

---

## 7. Typography Review

### Required checks

- Heading text uses Outfit appropriately.
- Body text, labels, and controls use Manrope.
- Mono text is used only where alignment or technical formatting matters.
- Font sizes follow the approved scale.
- Font weights are consistent and readable.
- All-caps labels are avoided unless explicitly justified.

### Reviewer questions

- Is the hierarchy obvious at a glance?
- Are table labels and values easy to scan?
- Did anyone use display styling where body styling should be used?

### Fail examples

- Multiple arbitrary font sizes on one page
- Headings that are too light to read
- Tiny metadata text with low contrast
- Mono font used decoratively instead of functionally

---

## 8. Spacing and Density

### Required checks

- The screen matches compact operational density.
- There is enough breathing room without wasting space.
- Related controls and content are grouped logically.
- Dense views remain scannable.
- Padding and gaps follow the approved spacing rhythm.

### Reviewer questions

- Does the screen support real daily use, or was it spaced like a marketing site?
- Are filter bars, forms, and tables compact enough?
- Are there awkward empty zones that reduce efficiency?

### Fail examples

- Huge card padding on dense workflow screens
- Inconsistent spacing between similar sections
- Table rows so tight they become hard to read
- Form layouts with random gaps and misalignment

---

## 9. Radius, Borders, and Elevation

### Required checks

- Inputs and buttons use restrained 8px radius.
- Cards and dialogs use restrained 12px radius.
- Controls do not appear overly rounded or playful.
- Borders remain visible in both themes.
- Shadows are subtle and not overused.
- Layering is communicated through surface contrast and borders first.

### Reviewer questions

- Does this look refined or cartoonishly soft?
- Are we relying on giant shadows instead of proper structure?
- Do nested surfaces remain visually clear?

### Fail examples

- Fully pill-shaped controls without reason
- Heavy drop shadows everywhere
- Borderless cards floating ambiguously on complex screens
- Mixed radius values with no system logic

---

## 10. Button and Action Review

### Required checks

- Primary action is clearly identifiable.
- Secondary and tertiary actions are present but not visually louder than primary.
- Destructive actions are reserved for real destructive flows.
- Ghost/link buttons remain discoverable.
- Button states are clear: default, hover, focus, disabled, loading.

### Reviewer questions

- Can users tell the main action instantly?
- Are there too many visually competing actions?
- Did a normal action get styled like a destructive one?

### Fail examples

- Multiple primary-colored buttons in one action group
- Invisible ghost buttons in dense forms
- Delete button styled like a harmless secondary action
- Disabled buttons that look broken instead of disabled

---

## 11. Form and Input Review

### Required checks

- Important inputs have visible labels.
- Placeholder text is supportive, not the only label.
- Input states are clear and consistent.
- Error messages appear near the affected control.
- Dense filter controls remain aligned and readable.
- Required fields are marked consistently.

### Reviewer questions

- Can users complete this form quickly and confidently?
- Are field groupings logical?
- Does validation feel clear or annoying?

### Fail examples

- Placeholder-only labels
- Inconsistent spacing between fields
- Validation shown far away from the field
- Dense search/filter controls with uneven heights

---

## 12. Table and Dense Data Review

### Required checks

- Table headers are readable and distinct.
- Row heights are compact but comfortable.
- Hover and selected states are distinguishable.
- Metadata remains readable.
- Numeric and billing-heavy columns align properly.
- Sticky headers or pinned columns remain visually clear.

### Reviewer questions

- Can a user scan this table for hours without eye fatigue?
- Are row states easy to distinguish?
- Are there too many borders, too little contrast, or both?

### Fail examples

- Hover and selected states look identical
- Tiny low-contrast metadata text
- Dense tables with no visual hierarchy
- Financial values not aligned well enough to compare quickly

---

## 13. Dialog, Dropdown, Popover, and Overlay Review

### Required checks

- Overlays separate content clearly without hurting readability.
- Dialogs use the correct surface, border, shadow, and radius.
- Dropdown items have clear hover, selected, and disabled states.
- Focus remains trapped and visible where required.
- Action footers are clear and aligned.

### Reviewer questions

- Is the overlay treatment calm and readable?
- Does the dropdown remain legible in dark mode?
- Can users tell what the safe action is versus the dangerous one?

### Fail examples

- Muddy overlays that flatten the page
- Dropdowns with poor contrast on hover
- Dialogs with weak visual separation from the background
- Dangerous action placement that encourages mistakes

---

## 14. Navigation Review

### Required checks

- Active navigation state is obvious.
- Navigation hierarchy is easy to understand.
- Icons support labels but do not replace clarity.
- Sidebar and top navigation follow tokenized styling.
- Navigation chrome does not overpower content.

### Reviewer questions

- Can users orient themselves instantly?
- Is the active location obvious in both themes?
- Does navigation feel premium and disciplined?

### Fail examples

- Barely visible active nav indicator
- Too many decorative separators
- Inconsistent icon alignment
- Navigation more visually intense than the main content

---

## 15. Status, Alerts, Toasts, and Feedback Review

### Required checks

- Status colors follow semantic meaning only.
- Alerts and toasts are readable and visually consistent.
- Feedback messages are appropriately toned.
- Success, warning, info, and destructive patterns are used consistently.
- Important feedback is noticeable without being visually chaotic.

### Reviewer questions

- Does the message feel proportionate to the event?
- Is semantic meaning stable across the app?
- Did anyone use the copper brand accent as a fake warning color?

### Fail examples

- Status colors reused decoratively
- Toast text with poor contrast
- Warning banners styled louder than destructive errors
- Brand accent used as a substitute for system state meaning

---

## 16. Screen-Specific Workflow Review

## 16.1 Patient Search

### Required checks

- Search input is prominent and clear.
- Results are easy to scan by name, phone, DOB, and address.
- Match highlighting is restrained and readable.
- Selected patient row is obvious.
- Dense search workflows remain efficient.

### Fail examples

- Search results are noisy or visually crowded
- Highlighting is loud or hard to read
- Selected result blends into hover state

## 16.2 Scheduler / Calendar

### Required checks

- Appointment states are semantically distinguishable.
- Calendar density remains readable.
- Today, selected slot, and status differences are visible in both themes.
- Visual overload is avoided.

### Fail examples

- Too many saturated event colors
- Today state impossible to notice in dark mode
- Scheduling grid too cramped or too loose

## 16.3 Billing and Financial Views

### Required checks

- Numeric values are easy to compare.
- Statuses such as paid, overdue, pending, and failed are unmistakable.
- Layout feels precise and structured.
- Emphasis does not become visually noisy.

### Fail examples

- Currency values difficult to scan
- Overuse of color for financial meaning
- Premium accent used where semantic status should be used

---

## 17. Charts and Data Visualization Review

### Required checks

- Charts are readable in both themes.
- Axis labels, legends, and gridlines have appropriate contrast.
- Primary teal is used for main series.
- Copper is used sparingly for secondary emphasis only.
- Status colors are reserved for semantic meaning.

### Reviewer questions

- Can users understand the chart without visual strain?
- Did the chart introduce unnecessary colors?
- Does the dark version still hold up?

### Fail examples

- Too many bright series colors
- Illegible axis labels in dark mode
- Brand accent used randomly across data series

---

## 18. PR Reviewer Checklist

### Copy into PR template if needed

- [ ] Uses semantic tokens only
- [ ] No raw hex values or unauthorized Tailwind palette classes
- [ ] Verified in light theme
- [ ] Verified in dark theme
- [ ] Focus states visible on all interactive elements
- [ ] Contrast and readability reviewed
- [ ] Typography follows approved fonts and scale
- [ ] Radius, spacing, and shadows follow system rules
- [ ] Dense workflows remain efficient and readable
- [ ] Screenshots included for meaningful visual changes
- [ ] No new one-off colors, states, or visual patterns introduced

---

## 19. QA Review Checklist

### Required validation

- Test in light and dark theme
- Test keyboard navigation
- Test hover, focus, selected, disabled, error, and loading states
- Test at normal and reduced screen brightness
- Test dense tables and long-form workflows
- Test dialogs, menus, and toasts
- Test responsive breakpoints where relevant

### QA fail triggers

- unreadable text
- inconsistent states
- missing focus treatment
- theme mismatch
- spacing drift
- any one-off styling inconsistency visible to users

---

## 20. Escalation Rule

If a reviewer finds a visual treatment that does not fit the theme but is not covered by an existing token or component pattern, the answer is not to improvise.

The reviewer should:

1. block the change from merging as-is
2. request a design-system decision
3. add or update a token/component pattern centrally if justified

No one-off visual exception should become permanent by accident.

---

## 21. Final Review Standard

Chiro Software should always look like one product, one system, and one disciplined brand.

Every screen should feel premium, efficient, readable, and trustworthy. Every workflow should hold up under real operational use. Every component should feel intentional.

If a screen looks like it came from a different product, a rushed admin template, or a rogue late-night styling adventure, the review should fail.

