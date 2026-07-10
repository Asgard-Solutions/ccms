/**
 * ReExamSection — Phase 2 Wave B §9
 * Renders the active-plan re-exam state without inventing dates.
 * Three states: no plan / due-in-future / overdue.
 */
import { Activity, CalendarPlus, ClipboardList, PlayCircle } from "lucide-react";
import { Button } from "../../components/ui/button";
import { formatDate } from "../../utils/time";
import ReExamsCard from "./ReExamsCard";

function daysUntil(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const now = new Date();
  return Math.round((d.getTime() - now.getTime()) / 86400000);
}

export default function ReExamSection({ patientId, activePlan, canWrite, onJumpTo }) {
  const dueIso = activePlan?.next_reexam_due_date || activePlan?.reexam_due_date || null;
  const days = daysUntil(dueIso);
  const overdue = days != null && days < 0;
  const approaching = days != null && days >= 0 && days <= 14;

  return (
    <section data-testid="reexam-section" aria-labelledby="reexam-title" className="space-y-3">
      <div>
        <h3 id="reexam-title" className="font-display text-lg font-semibold text-foreground">
          Re-exam
        </h3>
        <p className="text-sm text-muted-foreground">
          Progress reassessment tied to the active treatment plan.
        </p>
      </div>

      {!dueIso ? (
        <div
          data-testid="reexam-none"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed border-border bg-card/60 px-4 py-3"
        >
          <div className="flex items-center gap-3">
            <Activity className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-foreground">Re-exam due date not set</p>
              <p className="text-xs text-muted-foreground">
                No re-evaluation encounter is currently scheduled.
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div
          data-testid={overdue ? "reexam-overdue" : approaching ? "reexam-approaching" : "reexam-scheduled"}
          className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3 ${
            overdue
              ? "border-destructive/40 bg-destructive-soft"
              : approaching
                ? "border-warning/40 bg-warning-soft/40"
                : "border-border bg-card/60"
          }`}
        >
          <div className="flex items-center gap-3">
            <ClipboardList className={`h-5 w-5 ${overdue ? "text-destructive" : approaching ? "text-warning" : "text-muted-foreground"}`} aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-foreground">
                Re-exam due {formatDate(dueIso)}
              </p>
              <p className={`text-xs ${overdue ? "text-destructive" : approaching ? "text-warning" : "text-muted-foreground"}`}>
                {overdue
                  ? `${Math.abs(days)} day${Math.abs(days) === 1 ? "" : "s"} overdue`
                  : approaching
                    ? `Approaching · due in ${days} day${days === 1 ? "" : "s"}`
                    : "Scheduled"}
                {" · No re-evaluation encounter is currently linked."}
              </p>
            </div>
          </div>
          {canWrite && (
            <div className="flex gap-2">
              <Button size="sm" onClick={() => onJumpTo?.("care-plan")} data-testid="reexam-schedule-btn" className="rounded-full">
                <CalendarPlus className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Schedule re-exam
              </Button>
              <Button size="sm" variant="outline" onClick={() => onJumpTo?.("encounters")} data-testid="reexam-start-btn" className="rounded-full">
                <PlayCircle className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Start re-exam
              </Button>
            </div>
          )}
        </div>
      )}

      {/* Preserve the existing ReExamsCard list so no historical re-exam
          record is hidden by the Wave B redesign. */}
      <ReExamsCard patientId={patientId} />
    </section>
  );
}
