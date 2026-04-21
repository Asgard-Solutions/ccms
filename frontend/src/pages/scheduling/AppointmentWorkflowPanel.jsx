import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  CheckCircle2,
  Clock,
  FileText,
  LogOut,
  RotateCcw,
  Stethoscope,
  UserCheck,
  XCircle,
} from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import RoomAssignmentControl from "./RoomAssignmentControl";
import { statusMeta } from "./statusMeta";

/**
 * Appointment workflow + intake panel.
 *
 * Surfaces the arrival workflow as operational quick-actions with an
 * inline intake status. The panel is intentionally self-contained — it
 * consumes an appointment object and emits `onUpdated(updated)` after any
 * successful transition so the parent can refresh.
 *
 * Phase 2 scope (check-in + intake integration only):
 *   - Check in / Undo check-in / No-show buttons
 *   - Ready-for-provider (intake-gated; override path surfaces a confirm)
 *   - Intake status badge + link out to the existing intake form
 *   - Operational timeline (who + when for each milestone)
 */

const INTAKE_META = {
  not_started: { label: "Not started", variant: "outline",   tone: "text-muted-foreground" },
  in_progress: { label: "In progress", variant: "secondary", tone: "text-amber-700 dark:text-amber-300" },
  completed:   { label: "Completed",   variant: "default",   tone: "text-emerald-700 dark:text-emerald-300" },
};

function formatLocalTime(iso) {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function AppointmentWorkflowPanel({
  appointment,
  onUpdated,
  onOpenIntake,
}) {
  const [busy, setBusy] = useState(null);
  const [rooms, setRooms] = useState([]);

  // Active rooms scoped to this appointment's location.
  useEffect(() => {
    if (!appointment?.location_id) return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get("/rooms", {
          params: { active_only: true, location_id: appointment.location_id },
        });
        if (!cancelled) setRooms(data || []);
      } catch {
        if (!cancelled) setRooms([]);
      }
    })();
    return () => { cancelled = true; };
  }, [appointment?.location_id]);

  const status = appointment?.status || "scheduled";
  const intakeStatus = appointment?.intake_status || "not_started";
  const meta = statusMeta(status);
  const intakeMeta = INTAKE_META[intakeStatus] || INTAKE_META.not_started;

  const timeline = useMemo(() => buildTimeline(appointment), [appointment]);

  async function run(endpoint, { payload = {}, label, afterMsg } = {}) {
    if (!appointment?.id) return;
    setBusy(endpoint);
    try {
      const { data } = await api.post(
        `/appointments/${appointment.id}/${endpoint}`,
        payload,
      );
      toast.success(afterMsg || `${label} recorded`);
      onUpdated?.(data);
    } catch (err) {
      toast.error(err.response?.data?.detail || `Failed to ${label?.toLowerCase() || "update"}`);
    } finally {
      setBusy(null);
    }
  }

  async function onReadyForProvider() {
    if (intakeStatus === "completed") {
      await run("ready-for-provider", { label: "Ready for provider" });
      return;
    }
    // Intake not complete — confirm override before proceeding.
    const reason = window.prompt(
      "Intake is not complete for this patient. " +
      "Type a reason to override and mark ready for provider:",
    );
    if (!reason || !reason.trim()) return;
    await run("ready-for-provider", {
      payload: { override: true, reason: reason.trim() },
      label: "Ready for provider (override)",
      afterMsg: "Ready for provider (intake override recorded)",
    });
  }

  const isCanceled = status === "canceled" || status === "cancelled";
  const isTerminal = ["checked_out", "no_show"].includes(status) || isCanceled;

  return (
    <div
      data-testid="appt-workflow-panel"
      className="space-y-4 rounded-sm border border-border bg-muted/40 p-4"
    >
      <header className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wider text-muted-foreground">
          Status
        </span>
        <Badge
          data-testid="appt-status-badge"
          variant={meta.badgeVariant}
          className="rounded-sm"
        >
          {meta.label}
        </Badge>
        <span className="ml-3 text-xs uppercase tracking-wider text-muted-foreground">
          Intake
        </span>
        <Badge
          data-testid="appt-intake-badge"
          variant={intakeMeta.variant}
          className="rounded-sm"
        >
          <FileText className="mr-1 h-3 w-3" />
          {intakeMeta.label}
        </Badge>
        {appointment?.intake_completed_at && (
          <span
            data-testid="appt-intake-completed-at"
            className="text-xs text-muted-foreground"
          >
            · completed {formatLocalTime(appointment.intake_completed_at)}
            {appointment.intake_completed_by_name
              ? ` by ${appointment.intake_completed_by_name}`
              : ""}
          </span>
        )}
      </header>

      {/* Quick actions */}
      {!isTerminal && (
        <div className="flex flex-wrap gap-2">
          {(status === "scheduled" || status === "confirmed") && (
            <>
              <Button
                type="button"
                size="sm"
                data-testid="appt-checkin-btn"
                disabled={busy !== null}
                onClick={() => run("check-in", { label: "Check-in" })}
                className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
              >
                <UserCheck className="mr-1.5 h-4 w-4" />
                Check in
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                data-testid="appt-noshow-btn"
                disabled={busy !== null}
                onClick={() => {
                  if (!window.confirm("Mark this appointment as no-show?")) return;
                  run("no-show", { label: "No-show" });
                }}
                className="rounded-sm text-destructive hover:bg-destructive-soft"
              >
                <XCircle className="mr-1.5 h-4 w-4" />
                No-show
              </Button>
            </>
          )}

          {(status === "checked_in" || status === "ready_for_provider") && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              data-testid="appt-undo-checkin-btn"
              disabled={busy !== null}
              onClick={() => run("undo-check-in", { label: "Undo check-in" })}
              className="rounded-sm"
            >
              <RotateCcw className="mr-1.5 h-4 w-4" />
              Undo check-in
            </Button>
          )}

          {status === "checked_in" && (
            <Button
              type="button"
              size="sm"
              data-testid="appt-ready-for-provider-btn"
              disabled={busy !== null}
              onClick={onReadyForProvider}
              className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            >
              <Stethoscope className="mr-1.5 h-4 w-4" />
              Ready for provider
            </Button>
          )}
        </div>
      )}

      {isTerminal && (
        <p
          data-testid="appt-terminal-notice"
          className="text-xs italic text-muted-foreground"
        >
          This appointment is {meta.label.toLowerCase()} — arrival
          actions are no longer available.
        </p>
      )}

      {/* Intake quick-link */}
      {onOpenIntake && (        <div className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-3">
          <FileText className="h-4 w-4 text-muted-foreground" />
          <span className={`text-xs ${intakeMeta.tone}`}>
            {intakeStatus === "not_started"
              ? "Patient has not started intake yet."
              : intakeStatus === "in_progress"
                ? "Intake is in progress."
                : "Intake completed."}
          </span>
          <Button
            type="button"
            size="sm"
            variant="link"
            data-testid="appt-open-intake-btn"
            onClick={() => onOpenIntake(appointment)}
            className="h-auto p-0 text-xs"
          >
            Open intake
          </Button>
        </div>
      )}

      {/* Room assignment */}
      {!isTerminal && appointment?.location_id && (
        <div
          data-testid="appt-room-section"
          className="border-t border-border/60 pt-3"
        >
          <RoomAssignmentControl
            appointment={appointment}
            rooms={rooms}
            onUpdated={onUpdated}
          />
        </div>
      )}

      {/* Operational timeline */}
      {timeline.length > 0 && (
        <ol
          data-testid="appt-timeline"
          className="space-y-1.5 border-t border-border/60 pt-3 text-xs"
        >
          {timeline.map((row) => (
            <li
              key={row.key}
              data-testid={`appt-timeline-${row.key}`}
              className="flex flex-wrap items-center gap-2"
            >
              <row.Icon className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="font-medium">{row.label}</span>
              <span className="text-muted-foreground">
                {formatLocalTime(row.at)}
              </span>
              {row.by && (
                <span className="text-muted-foreground">· {row.by}</span>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function buildTimeline(appt) {
  if (!appt) return [];
  const rows = [
    { key: "checked_in",         label: "Checked in",         at: appt.checked_in_at,         by: appt.checked_in_by_user_id,         Icon: UserCheck },
    { key: "ready_for_provider", label: "Ready for provider", at: appt.ready_for_provider_at, by: appt.ready_for_provider_by_user_id, Icon: Stethoscope },
    { key: "visit_started",      label: "Visit started",      at: appt.visit_started_at,      by: appt.visit_started_by_user_id,      Icon: Clock },
    { key: "ready_for_checkout", label: "Ready for checkout", at: appt.ready_for_checkout_at, by: appt.ready_for_checkout_by_user_id, Icon: Clock },
    { key: "completed",          label: "Completed",          at: appt.completed_at,          by: appt.completed_by_user_id,          Icon: CheckCircle2 },
    { key: "checked_out",        label: "Checked out",        at: appt.checked_out_at,        by: appt.checked_out_by_user_id,        Icon: LogOut },
    { key: "no_show",            label: "No-show",            at: appt.no_show_at,            by: appt.no_show_by_user_id,            Icon: XCircle },
  ];
  return rows.filter((r) => !!r.at);
}
