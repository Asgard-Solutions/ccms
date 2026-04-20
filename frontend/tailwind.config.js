/** @type {import('tailwindcss').Config} */
// Chiro Software — Tailwind theme.
// All colors, radii, shadows, and fonts are driven by CSS variables defined
// in /app/frontend/src/index.css. Feature code must consume the semantic
// utilities exposed here (bg-primary, text-muted-foreground, rounded-sm, …)
// and must not introduce raw Tailwind palette classes.
// See: /app/docs/theme/CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md
module.exports = {
    darkMode: ["class"],
    content: [
        "./src/**/*.{js,jsx,ts,tsx}",
        "./public/index.html",
    ],
    theme: {
        extend: {
            colors: {
                /* Core semantic surfaces + text (shadcn HSL pattern) */
                background: "hsl(var(--background))",
                foreground: "hsl(var(--foreground))",
                card: {
                    DEFAULT: "hsl(var(--card))",
                    foreground: "hsl(var(--card-foreground))",
                },
                popover: {
                    DEFAULT: "hsl(var(--popover))",
                    foreground: "hsl(var(--popover-foreground))",
                },
                primary: {
                    DEFAULT: "hsl(var(--primary))",
                    foreground: "hsl(var(--primary-foreground))",
                },
                secondary: {
                    DEFAULT: "hsl(var(--secondary))",
                    foreground: "hsl(var(--secondary-foreground))",
                },
                muted: {
                    DEFAULT: "hsl(var(--muted))",
                    foreground: "hsl(var(--muted-foreground))",
                },
                accent: {
                    DEFAULT: "hsl(var(--accent))",
                    foreground: "hsl(var(--accent-foreground))",
                },
                destructive: {
                    DEFAULT: "hsl(var(--destructive))",
                    foreground: "hsl(var(--destructive-foreground))",
                },
                border: "hsl(var(--border))",
                input: "hsl(var(--input))",
                ring: "hsl(var(--ring))",

                /* Extended surfaces (direct hex tokens) */
                surface: "var(--surface)",
                "surface-2": "var(--surface-2)",
                "surface-3": "var(--surface-3)",
                "border-strong": "var(--border-strong-color)",

                /* Status tokens (direct hex) — for non-HSL consumers */
                success: {
                    DEFAULT: "var(--success)",
                    foreground: "var(--success-foreground-hex)",
                    soft: "var(--success-soft)",
                },
                warning: {
                    DEFAULT: "var(--warning)",
                    foreground: "var(--warning-foreground-hex)",
                    soft: "var(--warning-soft)",
                },
                info: {
                    DEFAULT: "var(--info)",
                    foreground: "var(--info-foreground-hex)",
                    soft: "var(--info-soft)",
                },
                "destructive-soft": "var(--destructive-soft)",

                /* Brand accent — copper (use sparingly) */
                "accent-strong": "var(--accent-strong)",

                chart: {
                    1: "hsl(var(--chart-1))",
                    2: "hsl(var(--chart-2))",
                    3: "hsl(var(--chart-3))",
                    4: "hsl(var(--chart-4))",
                    5: "hsl(var(--chart-5))",
                },
            },
            borderRadius: {
                xs: "var(--radius-xs)",
                sm: "var(--radius-sm)",
                md: "var(--radius-md)",
                lg: "var(--radius-lg)",
                xl: "var(--radius-xl)",
            },
            boxShadow: {
                xs: "var(--shadow-xs)",
                sm: "var(--shadow-sm)",
                md: "var(--shadow-md)",
                lg: "var(--shadow-lg)",
            },
            fontFamily: {
                display: ["var(--font-display)"],
                body: ["var(--font-body)"],
                mono: ["var(--font-mono)"],
                sans: ["var(--font-body)"],
            },
            keyframes: {
                "accordion-down": {
                    from: { height: "0" },
                    to: { height: "var(--radix-accordion-content-height)" },
                },
                "accordion-up": {
                    from: { height: "var(--radix-accordion-content-height)" },
                    to: { height: "0" },
                },
            },
            animation: {
                "accordion-down": "accordion-down 0.2s ease-out",
                "accordion-up": "accordion-up 0.2s ease-out",
            },
        },
    },
    plugins: [require("tailwindcss-animate")],
};
