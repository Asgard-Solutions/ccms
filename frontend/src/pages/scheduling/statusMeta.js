/**
 * Shared appointment-status metadata — a single source of truth for
 * labels, icons, and visual accents across calendar views, the flow
 * board, the workflow panel, and checkout/patient details.
 *
 * Rules:
 *   - every status is expressed as BOTH a human label AND a structural
 *     accent (left-border / background tint) plus an icon. We never
 *     rely on color alone for meaning.
 *   - legacy "cancelled" spelling is preserved; "canceled" is the
 *     canonical new form.
 */
import {
  Calendar,
  CheckCircle2,
  Circle,
  CircleDot,
  Clock,
  DoorOpen,
  LogOut,
  Stethoscope,
  UserCheck,
  XCircle,
} from "lucide-react";

export const STATUS_META = {
  scheduled: {
    label: "Scheduled",
    Icon: Calendar,
    tone: "bg-primary/5 border-l-primary/70",
    textColor: "text-primary",
    badgeVariant: "outline",
  },
  confirmed: {
    label: "Confirmed",
    Icon: Calendar,
    tone: "bg-primary/10 border-l-primary",
    textColor: "text-primary",
    badgeVariant: "outline",
  },
  checked_in: {
    label: "Checked in",
    Icon: UserCheck,
    tone: "bg-amber-400/10 border-l-amber-500",
    textColor: "text-amber-700 dark:text-amber-300",
    badgeVariant: "outline",
  },
  ready_for_provider: {
    label: "Ready for provider",
    Icon: Stethoscope,
    tone: "bg-indigo-500/10 border-l-indigo-500",
    textColor: "text-indigo-700 dark:text-indigo-300",
    badgeVariant: "outline",
  },
  in_progress: {
    label: "In progress",
    Icon: CircleDot,
    tone: "bg-primary/15 border-l-primary",
    textColor: "text-primary",
    badgeVariant: "default",
  },
  ready_for_checkout: {
    label: "Ready for checkout",
    Icon: Clock,
    tone: "bg-orange-400/10 border-l-orange-500",
    textColor: "text-orange-700 dark:text-orange-300",
    badgeVariant: "outline",
  },
  completed: {
    label: "Completed",
    Icon: CheckCircle2,
    tone: "bg-emerald-500/10 border-l-emerald-500",
    textColor: "text-emerald-700 dark:text-emerald-300",
    badgeVariant: "secondary",
  },
  checked_out: {
    label: "Checked out",
    Icon: LogOut,
    tone: "bg-muted border-l-muted-foreground/60",
    textColor: "text-muted-foreground",
    badgeVariant: "secondary",
  },
  no_show: {
    label: "No-show",
    Icon: XCircle,
    tone: "bg-destructive/10 border-l-destructive",
    textColor: "text-destructive",
    badgeVariant: "destructive",
  },
  canceled: {
    label: "Canceled",
    Icon: XCircle,
    tone: "bg-destructive/5 border-l-destructive/50 line-through",
    textColor: "text-destructive",
    badgeVariant: "destructive",
  },
  cancelled: {
    label: "Canceled",
    Icon: XCircle,
    tone: "bg-destructive/5 border-l-destructive/50 line-through",
    textColor: "text-destructive",
    badgeVariant: "destructive",
  },
};

export function statusMeta(s) {
  return STATUS_META[s] || STATUS_META.scheduled;
}

/**
 * Overdue heuristics for the flow board. Thresholds in minutes:
 *   - Waiting Room:       15m  → "Waiting long"
 *   - Roomed unattended:  30m  → "Overdue"
 *   - Ready for provider: 10m  → "Provider delay"
 *   - Ready for checkout: 10m  → "Checkout delay"
 *
 * Returns null when nothing is overdue. Returned objects always carry
 * a text label so the UI never signals state via color alone.
 */
export function overdueFor(appointment) {
  const now = Date.now();
  const minutesSince = (iso) => (iso ? (now - new Date(iso).getTime()) / 60000 : 0);
  const s = appointment?.status;
  const loc = appointment?.current_location_type;

  if (s === "checked_in") {
    const since = minutesSince(appointment.checked_in_at);
    if (loc === "roomed") {
      const sinceRoom = minutesSince(appointment.location_updated_at);
      if (sinceRoom >= 30) return { label: "Overdue", tone: "destructive" };
    } else if (since >= 15) {
      return { label: "Waiting long", tone: "warning" };
    }
  }
  if (s === "ready_for_provider" && minutesSince(appointment.ready_for_provider_at) >= 10) {
    return { label: "Provider delay", tone: "warning" };
  }
  if (s === "ready_for_checkout" && minutesSince(appointment.ready_for_checkout_at) >= 10) {
    return { label: "Checkout delay", tone: "warning" };
  }
  return null;
}

export function minutesInStage(appointment, stageStartIso) {
  if (!stageStartIso) return null;
  return Math.max(0, Math.floor((Date.now() - new Date(stageStartIso).getTime()) / 60000));
}

// Icon list re-exports so sibling modules don't need to import lucide-react
// a second time for the same iconography.
export { Circle };
