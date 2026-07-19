/**
 * TreatmentPlanProgress — Phase 2 Wave B §8
 * Segmented bar showing completed / scheduled / remaining visits with
 * numeric labels. Colour is supplementary — the numeric labels are the
 * primary information carrier (never rely on colour alone).
 */
export default function TreatmentPlanProgress({ plan, testId }) {
  const completed = plan?.visits_completed ?? 0;
  const scheduled = plan?.visits_scheduled ?? 0;
  const planned = plan?.total_visits_planned ?? plan?.visits_planned ?? 0;
  const remaining = Math.max(planned - completed - scheduled, 0);
  const total = Math.max(planned, completed + scheduled + remaining);

  const pct = (n) => (total > 0 ? (n / total) * 100 : 0);

  return (
    <div data-testid={testId} className="space-y-2">
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={completed}
        aria-label={`${completed} of ${total} visits completed, ${scheduled} scheduled, ${remaining} remaining`}
        className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full bg-success"
          style={{ width: `${pct(completed)}%` }}
          data-testid={`${testId}-completed-bar`}
        />
        <div
          className="h-full bg-primary/60"
          style={{ width: `${pct(scheduled)}%` }}
          data-testid={`${testId}-scheduled-bar`}
        />
        <div
          className="h-full bg-warning/50"
          style={{ width: `${pct(remaining)}%` }}
          data-testid={`${testId}-remaining-bar`}
        />
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span data-testid={`${testId}-legend-completed`}>
          <span aria-hidden="true" className="mr-1 inline-block h-2 w-2 rounded-full bg-success align-middle" />
          <span className="font-medium text-foreground">{completed}</span> completed
        </span>
        <span data-testid={`${testId}-legend-scheduled`}>
          <span aria-hidden="true" className="mr-1 inline-block h-2 w-2 rounded-full bg-primary/60 align-middle" />
          <span className="font-medium text-foreground">{scheduled}</span> scheduled
        </span>
        <span data-testid={`${testId}-legend-remaining`}>
          <span aria-hidden="true" className="mr-1 inline-block h-2 w-2 rounded-full bg-warning/60 align-middle" />
          <span className="font-medium text-foreground">{remaining}</span> remaining unscheduled
        </span>
        <span className="text-muted-foreground/80">of {total} planned</span>
      </div>
    </div>
  );
}
