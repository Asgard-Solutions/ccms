import { Toaster as Sonner, toast } from "sonner";

import { useTheme } from "../../contexts/ThemeContext";

// Chiro Software sonner toaster — consumes the app's ThemeContext (not
// next-themes) so toasts flip in lock-step with the user's preference.
// Toast variants inherit semantic tokens; state colors come from the
// theme layer rather than sonner defaults.
// See: /app/docs/theme/CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md §10
const Toaster = ({ ...props }) => {
  const { effective } = useTheme();

  return (
    <Sonner
      theme={effective}
      className="toaster group"
      toastOptions={{
        classNames: {
          toast: [
            "group toast",
            "group-[.toaster]:bg-popover group-[.toaster]:text-popover-foreground",
            "group-[.toaster]:border group-[.toaster]:border-border",
            "group-[.toaster]:shadow-md group-[.toaster]:rounded-sm",
            "group-[.toaster]:font-body",
          ].join(" "),
          title: "group-[.toast]:text-foreground group-[.toast]:font-semibold",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground group-[.toast]:rounded-sm",
          cancelButton:
            "group-[.toast]:bg-secondary group-[.toast]:text-secondary-foreground group-[.toast]:rounded-sm",
          success:
            "group-[.toaster]:!bg-[var(--success-soft)] group-[.toaster]:!text-success group-[.toaster]:!border-[var(--success-soft)]",
          warning:
            "group-[.toaster]:!bg-[var(--warning-soft)] group-[.toaster]:!text-warning group-[.toaster]:!border-[var(--warning-soft)]",
          info:
            "group-[.toaster]:!bg-[var(--info-soft)] group-[.toaster]:!text-info group-[.toaster]:!border-[var(--info-soft)]",
          error:
            "group-[.toaster]:!bg-[var(--destructive-soft)] group-[.toaster]:!text-destructive group-[.toaster]:!border-[var(--destructive-soft)]",
        },
      }}
      {...props}
    />
  );
};

export { Toaster, toast };
