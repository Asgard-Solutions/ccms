import * as React from "react";
import { cva } from "class-variance-authority";

import { cn } from "@/lib/utils";

// Chiro Software badge primitive — semantic variants + a restrained copper
// "premium" variant for billing/admin emphasis (spec §9 accent usage).
// See: /app/docs/theme/CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md §12.7
const badgeVariants = cva(
  "inline-flex items-center rounded-sm border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--focus)] focus:ring-offset-2 focus:ring-offset-background",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-primary text-primary-foreground",
        secondary:
          "border-transparent bg-secondary text-secondary-foreground",
        outline:
          "border-border text-foreground",
        success:
          "border-transparent bg-[var(--success-soft)] text-success",
        warning:
          "border-transparent bg-[var(--warning-soft)] text-warning",
        info:
          "border-transparent bg-[var(--info-soft)] text-info",
        destructive:
          "border-transparent bg-[var(--destructive-soft)] text-destructive",
        // Copper accent — reserved for premium emphasis per spec §9.
        premium:
          "border-transparent bg-[var(--badge-premium-bg)] text-[var(--badge-premium-fg)]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

function Badge({ className, variant, ...props }) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
