import { cn } from "../../../lib/utils";
import {
  CheckCircle2,
  XCircle,
  AlertTriangle,
  AlertCircle,
  HelpCircle,
  Clock3,
  ShieldCheck,
  Loader2,
} from "lucide-react";


/** Canonical status metadata for the 9 eligibility states. Keep this
 *  file authoritative — all badges and chips import from here so the
 *  visual language stays consistent across the 6 surfaces. */
export const ELIGIBILITY_META = {
  active:      { label: "Active",      tone: "success",     icon: CheckCircle2 },
  partial:     { label: "Partial",     tone: "warning",     icon: AlertCircle  },
  inactive:    { label: "Inactive",    tone: "destructive", icon: XCircle      },
  rejected:    { label: "Rejected",    tone: "destructive", icon: AlertTriangle },
  error:       { label: "Error",       tone: "destructive", icon: AlertTriangle },
  expired:     { label: "Expired",     tone: "warning",     icon: Clock3       },
  unknown:     { label: "Unknown",     tone: "muted",       icon: HelpCircle   },
  submitted:   { label: "Submitted",   tone: "muted",       icon: Loader2      },
  not_checked: { label: "Not checked", tone: "muted",       icon: ShieldCheck  },
};


const TONE_CLASSES = {
  success:     "border-success/40 bg-success-soft/40 text-success",
  warning:     "border-warning/40 bg-warning-soft/40 text-warning",
  destructive: "border-destructive/40 bg-destructive/10 text-destructive",
  muted:       "border-border bg-muted/40 text-muted-foreground",
};


/** Inline pill for tables / workflow cards. Tiny footprint, icon + label. */
export function EligibilityStatusChip({ status, className, testid }) {
  const meta = ELIGIBILITY_META[status] || ELIGIBILITY_META.not_checked;
  const Icon = meta.icon;
  const tone = TONE_CLASSES[meta.tone] || TONE_CLASSES.muted;
  return (
    <span
      data-testid={testid || `eligibility-chip-${status || "not_checked"}`}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide",
        tone, className,
      )}
    >
      <Icon className={cn("h-3 w-3", status === "submitted" && "animate-spin")} />
      {meta.label}
    </span>
  );
}


/** Chunkier banner for dialogs + check-in. Full-width coverage
 *  summary with optional sub-line and right-aligned sandbox badge. */
export function EligibilityStatusBanner({ status, title, subtitle, sandbox, className, testid }) {
  const meta = ELIGIBILITY_META[status] || ELIGIBILITY_META.not_checked;
  const Icon = meta.icon;
  const tone = TONE_CLASSES[meta.tone] || TONE_CLASSES.muted;
  return (
    <div
      data-testid={testid || `eligibility-banner-${status || "not_checked"}`}
      className={cn("flex items-center gap-3 rounded-sm border p-3", tone, className)}
    >
      <Icon className={cn("h-5 w-5 shrink-0", status === "submitted" && "animate-spin")} />
      <div className="flex-1 min-w-0">
        <div className="font-medium">{title || meta.label}</div>
        {subtitle && <div className="truncate text-xs opacity-80">{subtitle}</div>}
      </div>
      {sandbox && (
        <span
          className="rounded-sm bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"
          data-testid="eligibility-sandbox-badge"
        >
          Sandbox
        </span>
      )}
    </div>
  );
}
