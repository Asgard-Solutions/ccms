# Chiro Theme Implementation Artifacts

This document contains implementation-ready artifacts for the Chiro Software theme system.

It includes:

1. `src/index.css` theme foundation and semantic tokens
2. `tailwind.config.js` theme extension mapping
3. baseline Shadcn UI component patterns and conventions

These artifacts are intended to be adapted into the existing React 19 + Tailwind + Shadcn UI codebase.

---

## 1. `src/index.css`

```css
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;600&family=Manrope:wght@500;600;700;800&family=Outfit:wght@600;650;700&display=swap');

@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    /* Typography */
    --font-display: 'Outfit', ui-sans-serif, system-ui, sans-serif;
    --font-body: 'Manrope', ui-sans-serif, system-ui, sans-serif;
    --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;

    /* Spacing */
    --space-1: 4px;
    --space-2: 8px;
    --space-3: 12px;
    --space-4: 16px;
    --space-5: 20px;
    --space-6: 24px;
    --space-8: 32px;
    --space-10: 40px;
    --space-12: 48px;

    /* Radius */
    --radius-xs: 6px;
    --radius-sm: 8px;
    --radius-md: 10px;
    --radius-lg: 12px;
    --radius-xl: 14px;

    /* Shadows */
    --shadow-xs: 0 1px 2px rgba(15, 23, 32, 0.06);
    --shadow-sm: 0 2px 6px rgba(15, 23, 32, 0.08);
    --shadow-md: 0 8px 24px rgba(15, 23, 32, 0.12);
    --shadow-lg: 0 16px 40px rgba(15, 23, 32, 0.16);

    /* Foundation palette */
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

    /* Light theme semantic tokens */
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

    /* Component aliases */
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

  html {
    color-scheme: light;
  }

  html.dark {
    color-scheme: dark;
  }

  * {
    @apply border-border;
  }

  body {
    @apply bg-background text-foreground font-body antialiased;
    text-rendering: optimizeLegibility;
  }

  ::selection {
    background: var(--selection);
  }

  h1, h2, h3, h4, h5, h6 {
    font-family: var(--font-display);
    color: var(--foreground);
  }

  code, pre, kbd, samp {
    font-family: var(--font-mono);
  }
}

@layer components {
  .focus-ring {
    outline: none;
    box-shadow: 0 0 0 2px var(--background), 0 0 0 4px var(--focus);
  }

  .surface-panel {
    @apply bg-surface text-foreground border border-border rounded-lg shadow-xs;
  }

  .input-base {
    @apply h-10 w-full rounded-sm border border-input bg-surface px-3 text-sm text-foreground placeholder:text-muted-foreground;
  }

  .input-base:focus-visible {
    outline: none;
    box-shadow: 0 0 0 2px var(--background), 0 0 0 4px var(--focus);
  }

  .dense-input {
    @apply h-9;
  }

  .table-shell {
    @apply rounded-lg border border-border bg-card text-card-foreground overflow-hidden;
  }

  .table-header-row {
    background: var(--table-header-bg);
    color: var(--table-header-fg);
  }

  .table-row-hover:hover {
    background: var(--table-row-hover);
  }

  .table-row-selected {
    background: var(--table-row-selected);
  }
}
```

---

## 2. `tailwind.config.js`

```js
/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './src/**/*.{js,jsx,ts,tsx}',
  ],
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
      fontFamily: {
        display: ['var(--font-display)'],
        body: ['var(--font-body)'],
        mono: ['var(--font-mono)'],
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
      spacing: {
        1: 'var(--space-1)',
        2: 'var(--space-2)',
        3: 'var(--space-3)',
        4: 'var(--space-4)',
        5: 'var(--space-5)',
        6: 'var(--space-6)',
        8: 'var(--space-8)',
        10: 'var(--space-10)',
        12: 'var(--space-12)',
      },
    },
  },
  plugins: [],
};
```

---

## 3. Baseline Shadcn UI Component Patterns

These are the baseline conventions the team should apply to core Shadcn UI components under `src/components/ui/`.

### 3.1 `button.jsx`

Key goals:
- 8px radius
- compact operational sizing
- centralized variant styles
- visible focus ring

Suggested variant direction:

```jsx
import * as React from 'react';
import { cva } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-sm font-body text-sm font-semibold transition-colors disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:opacity-95',
        secondary: 'bg-secondary text-secondary-foreground hover:bg-[var(--secondary-hover)]',
        outline: 'border border-border bg-surface text-foreground hover:bg-surface-2',
        ghost: 'text-foreground hover:bg-surface-2',
        destructive: 'bg-destructive text-destructive-foreground hover:opacity-95',
        link: 'text-primary underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm: 'h-9 px-3',
        lg: 'h-11 px-5',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
);

export function Button({ className, variant, size, ...props }) {
  return (
    <button
      className={cn(buttonVariants({ variant, size }), 'focus-ring', className)}
      {...props}
    />
  );
}
```

### 3.2 `input.jsx`

Suggested direction:

```jsx
import * as React from 'react';
import { cn } from '@/lib/utils';

const Input = React.forwardRef(({ className, type = 'text', ...props }, ref) => {
  return (
    <input
      type={type}
      ref={ref}
      className={cn(
        'input-base focus-ring disabled:cursor-not-allowed disabled:opacity-60',
        className
      )}
      {...props}
    />
  );
});

Input.displayName = 'Input';

export { Input };
```

### 3.3 `card.jsx`

Suggested direction:

```jsx
import * as React from 'react';
import { cn } from '@/lib/utils';

export function Card({ className, ...props }) {
  return (
    <div
      className={cn('rounded-lg border border-border bg-card text-card-foreground shadow-xs', className)}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }) {
  return <div className={cn('p-5 pb-3', className)} {...props} />;
}

export function CardTitle({ className, ...props }) {
  return <h3 className={cn('font-display text-lg font-semibold', className)} {...props} />;
}

export function CardContent({ className, ...props }) {
  return <div className={cn('p-5 pt-0', className)} {...props} />;
}
```

### 3.4 `dialog.jsx`

Requirements:
- 12px radius
- themed overlay
- clear separation from background

Suggested content container direction:

```jsx
className={cn(
  'rounded-lg border border-border bg-card text-card-foreground shadow-md p-6',
  className
)}
```

Overlay direction:

```jsx
className="fixed inset-0 bg-[var(--dialog-overlay)]"
```

### 3.5 `table.jsx`

Requirements:
- compact density
- readable headers
- visible selected and hover states

Recommended conventions:

- table wrapper: `table-shell`
- header row: `table-header-row`
- body rows: `table-row-hover`
- selected row: append `table-row-selected`
- cell padding: `px-4 py-2.5`
- default body text: `text-[13px]`
- header text: `text-xs font-semibold uppercase tracking-[0.02em]`

### 3.6 `tabs.jsx`

Recommendations:
- compact height
- selected state obvious in both themes
- not overly pill-like

Suggested trigger direction:

```jsx
className={cn(
  'inline-flex h-9 items-center justify-center rounded-sm px-3 text-sm font-medium text-muted-foreground transition-colors',
  'data-[state=active]:bg-surface data-[state=active]:text-foreground data-[state=active]:shadow-xs',
  'hover:bg-surface-2 focus-ring',
  className
)}
```

### 3.7 `badge.jsx`

Badge rules:
- restrained radius
- semantic variants only
- premium copper variant allowed for special brand emphasis

Suggested variant set:
- default
- secondary
- success
- warning
- destructive
- info
- premium

---

## 4. App Shell Implementation Notes

### Sidebar

Suggested class direction:

```jsx
<aside className="border-r border-border bg-[var(--sidebar-bg)] text-[var(--sidebar-fg)]" />
```

Active item direction:

```jsx
className="bg-[var(--sidebar-active-bg)] text-[var(--sidebar-active-fg)] relative before:absolute before:left-0 before:top-2 before:bottom-2 before:w-1 before:rounded-full before:bg-[var(--sidebar-active-indicator)]"
```

### Top bars / subheaders

Suggested pattern:
- `bg-background/95`
- `border-b border-border`
- optional `backdrop-blur`
- use surface separation before adding shadow

---

## 5. Patient Search Implementation Notes

For the patient search redesign, use these visual rules:

- main search field: `h-10` or `h-11` depending on hierarchy
- filter row inputs: `dense-input`
- results container: `table-shell`
- selected patient result: `table-row-selected`
- match highlighting: subtle teal-tinted inline highlight, not yellow

Suggested highlight utility:

```css
.search-hit {
  background: color-mix(in srgb, var(--primary) 12%, transparent);
  color: inherit;
  border-radius: 4px;
  padding: 0 2px;
}
```

Use a simpler fallback if `color-mix` support becomes a problem.

---

## 6. Sonner Toast Theme Direction

Recommended toast wrapper classes:

```jsx
className="rounded-lg border border-border bg-popover text-popover-foreground shadow-sm"
```

Status-based toasts should map to semantic tokens and not invent new palettes.

---

## 7. Enforcement Notes

Engineering should enforce the following:

- no raw hex colors in feature JSX/CSS
- no Tailwind raw palette utilities outside the token layer
- all new UI must support light and dark themes
- all new core components must expose consistent focus states
- all screens must use approved typography and radius system

Recommended lint rules or grep checks:

- `#[0-9A-Fa-f]{3,8}` outside `index.css`
- `bg-(red|blue|green|slate|gray|zinc)-`
- `text-(red|blue|green|slate|gray|zinc)-`

---

## 8. Rollout Order

Recommended implementation order:

1. Replace `src/index.css` theme layer
2. Update `tailwind.config.js`
3. Refactor core Shadcn components
4. Apply app shell updates
5. Apply patient search/table styling
6. Apply dialogs, tabs, badges, and toasts
7. Sweep for raw palette and dark-mode overrides

That sequence reduces chaos and helps avoid the classic “half the app looks premium, the other half looks like it survived three mergers.”

