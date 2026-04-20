import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva } from "class-variance-authority";

import { cn } from "@/lib/utils";

// Chiro Software button primitive — conforms to the theme spec:
//   • 40px default height (spec §6), 8px radius (spec §7)
//   • 600 weight (spec §5 control text)
//   • Visible focus ring per spec §10 (2px offset ring in theme focus color)
//   • Variants bound to semantic tokens only
// See: /app/docs/theme/CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md §12.1
const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-2 whitespace-nowrap",
    "rounded-sm font-semibold text-sm transition-colors",
    "disabled:pointer-events-none disabled:opacity-50",
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
    // Accessible focus ring using the theme focus token.
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)] focus-visible:ring-offset-2 focus-visible:ring-offset-background",
  ].join(" "),
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground shadow-xs hover:bg-[var(--primary-hover)] active:bg-[var(--primary-active)]",
        destructive:
          "bg-destructive text-destructive-foreground shadow-xs hover:brightness-95 active:brightness-90",
        outline:
          "border border-border bg-surface text-foreground hover:bg-secondary hover:text-secondary-foreground",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-[var(--secondary-hover)]",
        ghost:
          "text-foreground hover:bg-secondary hover:text-secondary-foreground",
        link:
          "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 px-3 text-xs",
        lg: "h-11 px-6 text-base",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

const Button = React.forwardRef(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
