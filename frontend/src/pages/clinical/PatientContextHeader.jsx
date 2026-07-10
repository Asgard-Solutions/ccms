/**
 * PatientContextHeader — sticky orientation bar rendered above the
 * clinical section nav. Read-only, mask-aware.
 */
import { AlertTriangle } from "lucide-react";
import { formatDate, formatDateTime } from "../../utils/time";

function ContextChip({ label, children, testId }) {
  return (
    <div className="min-w-0" data-testid={testId}>
      <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="truncate text-sm text-foreground">{children}</div>
    </div>
  );
}

export default function PatientContextHeader({
  patient,
  age,
  initials,
  activeEpisode,
  primaryDx,
  currentProviderName,
  nextAppt,
  reExamDue,
  alerts,
}) {
  const p = patient || {};
  const nameOrMask = p.unmasked
    ? `${p.first_name || ""} ${p.last_name || ""}`.trim() || "—"
    : p.display_name_masked || "Masked patient";

  return (
    <div
      data-testid="clinical-patient-context-header"
      className="border-b border-border bg-background/95 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/80"
    >
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/15 text-sm font-semibold text-primary"
          >
            {initials}
          </span>
          <div className="min-w-0">
            <div
              className="truncate font-display text-base font-semibold text-foreground"
              data-testid="clinical-context-patient-name"
            >
              {nameOrMask}
            </div>
            <div className="text-xs text-muted-foreground">
              {[
                age != null ? `Age ${age}` : null,
                p.gender || null,
                p.status === "deleted" ? "Archived" : null,
              ]
                .filter(Boolean)
                .join(" · ") || "Patient profile"}
            </div>
          </div>
        </div>

        <ContextChip label="Episode" testId="clinical-context-episode">
          {activeEpisode ? activeEpisode.title : "No active episode"}
        </ContextChip>

        <ContextChip label="Primary diagnosis" testId="clinical-context-primary-dx">
          {primaryDx ? primaryDx.label || primaryDx.icd10_code : "Not documented"}
        </ContextChip>

        <ContextChip label="Provider" testId="clinical-context-provider">
          {currentProviderName || "Unassigned"}
        </ContextChip>

        <ContextChip label="Next appointment" testId="clinical-context-next-appt">
          {nextAppt ? formatDateTime(nextAppt.start_time) : "Not scheduled"}
        </ContextChip>

        <ContextChip label="Re-exam due" testId="clinical-context-reexam-due">
          {reExamDue ? formatDate(reExamDue) : "Not scheduled"}
        </ContextChip>

        {alerts && alerts.length > 0 && (
          <span
            data-testid="clinical-context-alerts"
            className="inline-flex items-center gap-1.5 rounded-full border border-warning/40 bg-warning-soft px-2.5 py-1 text-xs font-medium text-warning"
            role="status"
          >
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            {alerts.length === 1 ? alerts[0] : `${alerts.length} clinical alerts`}
          </span>
        )}
      </div>
    </div>
  );
}
