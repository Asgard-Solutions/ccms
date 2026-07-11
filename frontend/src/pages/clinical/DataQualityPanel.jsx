/**
 * DataQualityPanel — Phase 3 Slice 4.
 *
 * Patient-chart-scoped data-quality rows with direct-resolution links.
 * The panel is read-only, deterministic, non-mutating, and never
 * exposes patient/record identifiers — it just reports counts + jumps
 * the reviewer to the section that owns the fix.
 *
 * Rows are not dismissible (per Slice 4 brief: data-quality issues
 * should be resolved, not silenced). They disappear from the panel
 * only when the underlying structured data is fixed.
 */
import { AlertTriangle, ChevronRight, Info, ShieldAlert } from "lucide-react";
import { useMemo } from "react";
import { Button } from "../../components/ui/button";
import { deriveDataQualityIssues } from "./dataQualityEngine";

const SEVERITY_META = {
  error: {
    icon: ShieldAlert,
    className: "border-destructive/40 bg-destructive-soft text-destructive",
    label: "Error",
  },
  warning: {
    icon: AlertTriangle,
    className: "border-warning/40 bg-warning-soft text-warning",
    label: "Warning",
  },
  info: {
    icon: Info,
    className: "border-primary/30 bg-primary/10 text-primary",
    label: "Info",
  },
};

export default function DataQualityPanel({
  canWrite,
  summary,
  activePlan,
  primaryDx,
  encounterGroups,
  imaging,
  episodes,
  outcomeEntries,
  onJumpTo,
}) {
  const issues = useMemo(
    () =>
      deriveDataQualityIssues({
        canWrite,
        summary,
        activePlan,
        primaryDx,
        encounterGroups: encounterGroups || [],
        imaging: imaging || [],
        episodes: episodes || [],
        outcomeEntries: outcomeEntries || [],
      }),
    [canWrite, summary, activePlan, primaryDx, encounterGroups, imaging, episodes, outcomeEntries],
  );

  if (issues.length === 0) {
    return (
      <section
        data-testid="data-quality-panel"
        aria-labelledby="data-quality-title"
        className="rounded-xl border border-border bg-card/60 p-4"
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3
              id="data-quality-title"
              className="font-display text-lg font-semibold text-foreground"
            >
              Data quality
            </h3>
            <p className="text-sm text-muted-foreground">
              Patient-scoped structural checks. No aggregate metrics leave this chart.
            </p>
          </div>
        </div>
        <div
          data-testid="data-quality-empty"
          className="mt-3 rounded-lg border border-dashed border-border bg-card/40 px-4 py-3 text-sm text-muted-foreground"
        >
          No data-quality issues detected on this chart.
        </div>
      </section>
    );
  }

  return (
    <section
      data-testid="data-quality-panel"
      aria-labelledby="data-quality-title"
      className="rounded-xl border border-border bg-card/60 p-4"
    >
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3
            id="data-quality-title"
            className="font-display text-lg font-semibold text-foreground"
          >
            Data quality
          </h3>
          <p className="text-sm text-muted-foreground">
            {issues.length} patient-scoped issue{issues.length === 1 ? "" : "s"} — resolve inline to clear the row.
          </p>
        </div>
      </div>
      <ul
        data-testid="data-quality-list"
        aria-label="Patient data-quality issues"
        className="mt-3 space-y-2"
      >
        {issues.map((r) => {
          const sev = SEVERITY_META[r.severity] || SEVERITY_META.info;
          const Icon = sev.icon;
          return (
            <li
              key={r.id}
              data-testid={`data-quality-row-${r.id}`}
              data-severity={r.severity}
              className={[
                "flex flex-wrap items-start justify-between gap-3 rounded-lg border p-3",
                sev.className,
              ].join(" ")}
            >
              <div className="flex min-w-0 flex-1 items-start gap-2">
                <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <div
                    className="text-sm font-medium text-foreground"
                    data-testid={`data-quality-row-${r.id}-label`}
                  >
                    {r.label}
                    <span
                      className="ml-2 rounded-full border border-current px-1.5 py-0.5 text-[10px] uppercase"
                      data-testid={`data-quality-row-${r.id}-severity`}
                      aria-label={`severity ${r.severity}`}
                    >
                      {sev.label}
                    </span>
                    <span
                      className="ml-2 rounded-full bg-card px-1.5 py-0.5 text-[10px] text-muted-foreground"
                      data-testid={`data-quality-row-${r.id}-count`}
                    >
                      {r.count}
                    </span>
                  </div>
                  <div
                    className="mt-0.5 text-xs text-muted-foreground"
                    data-testid={`data-quality-row-${r.id}-why`}
                  >
                    {r.why}
                  </div>
                </div>
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onJumpTo?.(r.resolution?.section, { userInitiated: true })}
                data-testid={`data-quality-row-${r.id}-resolve`}
                className="rounded-full"
              >
                Resolve
                <ChevronRight className="ml-1 h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
