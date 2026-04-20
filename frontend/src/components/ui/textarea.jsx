import * as React from "react";

import { cn } from "@/lib/utils";

// Chiro Software textarea primitive — 8px radius, visible border in both
// themes, theme-aware placeholder, accessible focus ring.
// See: /app/docs/theme/CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md §12.2
const Textarea = React.forwardRef(({ className, ...props }, ref) => {
  return (
    <textarea
      className={cn(
        "flex min-h-[72px] w-full rounded-sm border border-border bg-surface px-3 py-2 text-sm text-foreground",
        "shadow-xs transition-colors",
        "placeholder:text-[var(--input-placeholder)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)] focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        "disabled:cursor-not-allowed disabled:opacity-60",
        className
      )}
      ref={ref}
      {...props}
    />
  );
});
Textarea.displayName = "Textarea";

export { Textarea };
