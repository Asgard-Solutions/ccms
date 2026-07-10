/**
 * CurrentCareStatusPanel — actionable snapshot of the chart at the top
 * of the Clinical redesign.
 *
 * Emits `clinical_care_status_action_selected` telemetry on every CTA
 * button press. The event vocabulary is strictly allow-listed on the
 * backend (`services/telemetry/router.py`) and carries no PHI, IDs,
 * counts, or free-form strings — just enum slugs identifying which
 * button was pressed on which surface / layout.
 */
import {
  CalendarPlus,
  ClipboardList,
  PlayCircle,
  PlusCircle,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { formatDate, formatDateTime } from "../../utils/time";
import { trackCareStatusAction } from "../../utils/telemetry";

// Allow-list mirror of the backend enum. Kept in sync with
// `services/telemetry/router.py::ActionSlug` and
// `services/telemetry/SCHEMA.md`.
export const CARE_STATUS_ACTION_SLUGS = [
  "open-encounter",
  "add-note",
  "record-outcome",
  "schedule-visit",
  "schedule-reexam",
  "review-billing-issues",
  "edit-missing-information",
];

/**
 * Wraps a click handler with a telemetry emit. Called only from the
 * declarative row/header definitions below so we never lose the slug.
 */
function withTelemetry(slug, fn) {
  return () => {
    if (CARE_STATUS_ACTION_SLUGS.includes(slug)) {
      trackCareStatusAction(slug);
    }
    fn?.();
  };
}

function buildRows({
  activeEpisode,
  primaryDx,
  activePlan,
  nextAppt,
  reExamDue,
  unsignedCount,
  billingWarnings,
  redFlag,
  missingIntakeCount,
  canWrite,
  onJumpTo,
  navigate,
  patientId,
}) {
  const rows = [];

  rows.push({
    key: "episode",
    label: "Active episode",
    value: activeEpisode ? activeEpisode.title : "No current episode",
    tone: activeEpisode ? "default" : "muted",
  });

  rows.push({
    key: "primary-dx",
    label: "Primary diagnosis",
    value: primaryDx
      ? [primaryDx.icd10_code, primaryDx.label].filter(Boolean).join(" · ")
      : "Not documented",
    tone: primaryDx ? "default" : "muted",
    cta: primaryDx
      ? null
      : canWrite
        ? {
            label: "Add diagnosis",
            slug: "edit-missing-information",
            onClick: () => onJumpTo("diagnoses"),
          }
        : null,
  });

  if (activePlan) {
    const completed = activePlan.visits_completed ?? 0;
    const planned = activePlan.total_visits_planned ?? activePlan.visits_planned ?? null;
    const scheduled = activePlan.visits_scheduled ?? null;
    if (planned != null) {
      rows.push({
        key: "plan-progress",
        label: "Visits progress",
        value: `${completed} of ${planned} planned visits completed`,
        tone: "default",
      });
    }
    if (scheduled != null && scheduled > 0) {
      rows.push({
        key: "plan-scheduled",
        label: "Scheduled",
        value: `${scheduled} visit${scheduled === 1 ? "" : "s"} scheduled`,
        tone: "default",
      });
    }
    if (planned != null && scheduled != null) {
      const remaining = Math.max(planned - completed - scheduled, 0);
      if (remaining > 0) {
        rows.push({
          key: "plan-remaining",
          label: "Unscheduled",
          value: `${remaining} visit${remaining === 1 ? "" : "s"} remain unscheduled`,
          tone: "warning",
          cta: canWrite
            ? {
                label: "Schedule visit",
                slug: "schedule-visit",
                onClick: () => navigate(`/scheduling?patient=${patientId}`),
              }
            : null,
        });
      }
    }
  } else {
    rows.push({
      key: "plan-progress",
      label: "Treatment plan",
      value: "No active plan",
      tone: "muted",
      cta: canWrite
        ? {
            label: "Open care plan",
            slug: "edit-missing-information",
            onClick: () => onJumpTo("care-plan"),
          }
        : null,
    });
  }

  rows.push({
    key: "next-appt",
    label: "Next appointment",
    value: nextAppt
      ? `${formatDateTime(nextAppt.start_time)}${
          nextAppt.provider_name ? ` with ${nextAppt.provider_name}` : ""
        }`
      : "Not scheduled",
    tone: nextAppt ? "default" : "warning",
    cta:
      !nextAppt && canWrite
        ? {
            label: "Schedule visit",
            slug: "schedule-visit",
            onClick: () => navigate(`/scheduling?patient=${patientId}`),
          }
        : null,
  });

  if (reExamDue) {
    rows.push({
      key: "reexam-due",
      label: "Re-exam due",
      value: formatDate(reExamDue),
      tone: "warning",
      cta: canWrite
        ? {
            label: "Schedule re-exam",
            slug: "schedule-reexam",
            onClick: () => onJumpTo("care-plan"),
          }
        : null,
    });
  }

  if (unsignedCount > 0) {
    rows.push({
      key: "unsigned",
      label: "Documentation",
      value: `${unsignedCount} unsigned or incomplete document${
        unsignedCount === 1 ? "" : "s"
      }`,
      tone: "warning",
      cta: canWrite
        ? {
            label: "Open encounters",
            slug: "open-encounter",
            onClick: () => onJumpTo("encounters"),
          }
        : null,
    });
  }

  // Billing row.
  //  * null aggregate → caller lacks billing permission OR fetch failed:
  //    keep the row hidden entirely to avoid a misleading zero.
  //  * warning_count === 0 && blocked_count === 0 → nothing to review:
  //    omit the row (matches the panel convention that surfaces
  //    actionable state only).
  //  * blocked_count > 0 → show blocked, deprioritise warnings.
  //  * warning_count > 0 → show warning.
  if (billingWarnings && (billingWarnings.blocked_count > 0 || billingWarnings.warning_count > 0)) {
    const blocked = billingWarnings.blocked_count || 0;
    const warnings = billingWarnings.warning_count || 0;
    const topMessage = billingWarnings.top_message || null;
    let value;
    let tone;
    if (blocked > 0) {
      const warnSuffix = warnings > 0
        ? ` and ${warnings} warning${warnings === 1 ? "" : "s"}`
        : "";
      value = `${blocked} blocked visit${blocked === 1 ? "" : "s"}${warnSuffix}${topMessage ? ` · ${topMessage}` : ""}`;
      tone = "destructive";
    } else {
      value = `${warnings} billing warning${warnings === 1 ? "" : "s"} require review${topMessage ? ` · ${topMessage}` : ""}`;
      tone = "warning";
    }
    rows.push({
      key: "billing",
      label: "Billing",
      value,
      tone,
      cta: {
        label: "Review billing issues",
        slug: "review-billing-issues",
        onClick: () => onJumpTo("encounters"),
      },
    });
  }

  if (redFlag && redFlag.positives.length > 0) {
    rows.push({
      key: "red-flag",
      label: "Safety",
      value: `Positive red-flag findings: ${redFlag.positives.join(", ")}`,
      tone: "destructive",
      cta: {
        label: "Review history",
        slug: "edit-missing-information",
        onClick: () => onJumpTo("history"),
      },
    });
  }

  if (missingIntakeCount > 0) {
    rows.push({
      key: "missing-intake",
      label: "Intake",
      value: `Missing required information (${missingIntakeCount} field${
        missingIntakeCount === 1 ? "" : "s"
      })`,
      tone: "warning",
      cta: canWrite
        ? {
            label: "Open history",
            slug: "edit-missing-information",
            onClick: () => onJumpTo("history"),
          }
        : null,
    });
  }

  return rows;
}

const TONE_TO_CLASS = {
  warning: "text-warning",
  destructive: "text-destructive",
  muted: "text-muted-foreground",
  default: "text-foreground",
};

export default function CurrentCareStatusPanel(props) {
  const {
    canWrite,
    onOpenEncounter,
    onJumpTo,
    navigate,
    patientId,
  } = props;
  const rows = buildRows(props);

  const headerActions = [
    {
      key: "open-encounter",
      testId: "care-status-open-encounter",
      icon: PlayCircle,
      label: "Open current encounter",
      slug: "open-encounter",
      onClick: onOpenEncounter,
    },
    {
      key: "add-note",
      testId: "care-status-add-note",
      icon: PlusCircle,
      label: "Add note",
      slug: "add-note",
      onClick: () => onJumpTo("encounters"),
    },
    {
      key: "record-outcome",
      testId: "care-status-record-outcome",
      icon: ClipboardList,
      label: "Record outcome",
      slug: "record-outcome",
      onClick: () => onJumpTo("outcomes"),
    },
    {
      key: "schedule-visit",
      testId: "care-status-schedule-visit",
      icon: CalendarPlus,
      label: "Schedule visit",
      slug: "schedule-visit",
      onClick: () => navigate(`/scheduling?patient=${patientId}`),
    },
  ];

  return (
    <section
      data-testid="clinical-care-status-panel"
      aria-labelledby="clinical-care-status-title"
      className="rounded-xl border border-border bg-card/60 p-5"
    >
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3
            id="clinical-care-status-title"
            className="font-display text-lg font-semibold text-foreground"
          >
            Current care status
          </h3>
          <p className="text-sm text-muted-foreground">
            A snapshot of what needs attention today, drawn from the chart.
          </p>
        </div>

        {canWrite && (
          <div className="flex flex-wrap gap-2">
            {headerActions.map((a) => {
              const Icon = a.icon;
              return (
                <Button
                  key={a.key}
                  size="sm"
                  variant="outline"
                  onClick={withTelemetry(a.slug, a.onClick)}
                  data-testid={a.testId}
                  className="rounded-full"
                >
                  <Icon className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                  {a.label}
                </Button>
              );
            })}
          </div>
        )}
      </div>

      <ul
        data-testid="clinical-care-status-rows"
        className="divide-y divide-border/60"
      >
        {rows.map((r) => (
          <li
            key={r.key}
            data-testid={`care-status-row-${r.key}`}
            className="flex flex-wrap items-center justify-between gap-3 py-2.5"
          >
            <div className="min-w-0 flex-1">
              <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                {r.label}
              </div>
              <div className={`mt-0.5 text-sm ${TONE_TO_CLASS[r.tone] || TONE_TO_CLASS.default}`}>
                {r.value}
              </div>
            </div>
            {r.cta && (
              <Button
                size="sm"
                variant="ghost"
                onClick={withTelemetry(r.cta.slug, r.cta.onClick)}
                data-testid={`care-status-cta-${r.key}`}
                className="rounded-full text-primary hover:bg-primary/10"
              >
                {r.cta.label}
              </Button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
