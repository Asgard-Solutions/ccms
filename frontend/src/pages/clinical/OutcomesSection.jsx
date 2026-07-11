/**
 * OutcomesSection — Phase 3 Slice 3 orchestrator.
 *
 * Fetches raw outcome entries + active plan, runs the pure derivation
 * helpers, and mounts:
 *   - OutcomeSuggestions
 *   - Per-instrument OutcomeSnapshotCard
 *   - OutcomeTrendChart with milestone markers
 *   - OutcomeTrendTable (view-toggle for accessibility)
 *
 * States: loading / empty / partial / permission-denied / error, each
 * with an explicit `data-testid`. Deterministic. No clinical claims.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { BarChart2, TableProperties, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError } from "../../api/client";
import { Skeleton } from "../../components/ui/skeleton";
import OutcomeSnapshotCard from "./OutcomeSnapshotCard";
import OutcomeTrendChart from "./OutcomeTrendChart";
import OutcomeTrendTable from "./OutcomeTrendTable";
import OutcomeSuggestions from "./OutcomeSuggestions";
import {
  groupByInstrument,
  deriveSeries,
  buildMilestones,
  deriveOutcomeSuggestions,
  windowSeriesToLastMonths,
} from "./outcomeSeriesHelpers";
import { useClinicalReturnState } from "./useClinicalReturnState";

const HISTORY_WINDOW_MONTHS = 24;

export default function OutcomesSection({
  patientId,
  canWrite,
  activePlan,
  onRecordOutcome,
  routeInstanceToken,
}) {
  const { state, saveState } = useClinicalReturnState({
    section: "outcomes",
    routeInstanceToken,
  });

  const [entries, setEntries] = useState(null); // null = loading
  const [err, setErr] = useState(null);
  const view = state?.view || "chart";
  const dismissedSet = useMemo(
    () => new Set(Array.isArray(state?.dismissed) ? state.dismissed : []),
    [state],
  );

  const load = useCallback(async () => {
    setErr(null);
    try {
      const { data } = await api.get(
        `/patients/${patientId}/clinical/outcomes`,
      );
      setEntries(Array.isArray(data) ? data : []);
    } catch (e) {
      // 403 = permission-scoped no-access; anything else = actual error.
      const status = e?.response?.status;
      if (status === 403) {
        setEntries([]);
        setErr("permission");
      } else {
        setEntries([]);
        setErr(formatApiError(e));
      }
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  const groups = useMemo(() => groupByInstrument(entries || []), [entries]);

  const seriesList = useMemo(
    () =>
      groups
        .map(deriveSeries)
        .sort(
          (a, b) =>
            (b.latest?.captured_at || "").localeCompare(
              a.latest?.captured_at || "",
            ),
        ),
    [groups],
  );

  const milestones = useMemo(
    () => buildMilestones({ activePlan }),
    [activePlan],
  );

  const suggestions = useMemo(
    () =>
      deriveOutcomeSuggestions({
        canWrite,
        activePlan,
        entries: entries || [],
        dismissed: dismissedSet,
      }),
    [canWrite, activePlan, entries, dismissedSet],
  );

  const setView = useCallback(
    (next) => {
      saveState({ view: next });
    },
    [saveState],
  );

  const dismissSuggestion = useCallback(
    (s) => {
      const next = new Set(dismissedSet);
      next.add(s.instrument_key);
      saveState({ dismissed: Array.from(next) });
      toast.info(`${s.short_label} suggestion dismissed on this chart.`);
    },
    [dismissedSet, saveState],
  );

  if (entries === null) {
    return (
      <Skeleton
        data-testid="outcomes-section-loading"
        className="h-32 rounded-lg"
      />
    );
  }

  if (err && err !== "permission") {
    return (
      <section
        data-testid="outcomes-section-error"
        aria-labelledby="outcomes-section-title"
        className="rounded-xl border border-destructive/30 bg-destructive-soft p-4 text-sm text-destructive"
      >
        <h3
          id="outcomes-section-title"
          className="font-display text-lg font-semibold"
        >
          Outcomes
        </h3>
        <p className="mt-1">{err}</p>
      </section>
    );
  }

  if (err === "permission") {
    return (
      <section
        data-testid="outcomes-section-permission-denied"
        aria-labelledby="outcomes-section-title"
        className="rounded-xl border border-border bg-card/60 p-4"
      >
        <h3
          id="outcomes-section-title"
          className="font-display text-lg font-semibold text-foreground"
        >
          Outcomes
        </h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Your role does not include outcome-measure access on this patient.
        </p>
      </section>
    );
  }

  return (
    <section
      data-testid="outcomes-section"
      aria-labelledby="outcomes-section-title"
      className="space-y-3 rounded-xl border border-border bg-card/60 p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3
            id="outcomes-section-title"
            className="font-display text-lg font-semibold text-foreground"
          >
            Outcomes
          </h3>
          <p className="text-sm text-muted-foreground">
            Configured functional measures. Values shown as recorded — no clinical inference.
          </p>
        </div>
        <div
          role="tablist"
          aria-label="Outcomes view mode"
          className="inline-flex items-center gap-1 rounded-full border border-border bg-card p-0.5 text-xs"
        >
          <button
            type="button"
            role="tab"
            aria-selected={view === "chart"}
            onClick={() => setView("chart")}
            data-testid="outcomes-view-chart"
            className={[
              "inline-flex items-center gap-1 rounded-full px-2 py-1",
              view === "chart"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            ].join(" ")}
          >
            <BarChart2 className="h-3 w-3" aria-hidden="true" />
            Chart
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "table"}
            onClick={() => setView("table")}
            data-testid="outcomes-view-table"
            className={[
              "inline-flex items-center gap-1 rounded-full px-2 py-1",
              view === "table"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            ].join(" ")}
          >
            <TableProperties className="h-3 w-3" aria-hidden="true" />
            Table
          </button>
        </div>
      </div>

      <OutcomeSuggestions
        suggestions={suggestions}
        onRecord={onRecordOutcome}
        onDismiss={dismissSuggestion}
      />

      {seriesList.length === 0 ? (
        <div
          data-testid="outcomes-section-empty"
          className="rounded-lg border border-dashed border-border bg-card/40 px-5 py-4 text-sm text-muted-foreground"
        >
          No outcome entries have been recorded on this chart yet.
        </div>
      ) : (
        <div className="space-y-3">
          {seriesList.map((s) => {
            const windowed = {
              ...s,
              points: windowSeriesToLastMonths(s.points, HISTORY_WINDOW_MONTHS),
            };
            const testidPrefix = `outcome-${s.instrument_key}`;
            return (
              <div key={s.instrument_key} className="space-y-2">
                <OutcomeSnapshotCard
                  series={s}
                  testidPrefix={testidPrefix}
                  onOpenSource={(point) => onRecordOutcome?.({ view_entry_id: point.entry_id })}
                />
                <div className="rounded-lg border border-border bg-card/40 p-3">
                  {view === "chart" ? (
                    <OutcomeTrendChart
                      series={windowed}
                      milestones={milestones}
                      testidPrefix={testidPrefix}
                    />
                  ) : (
                    <OutcomeTrendTable
                      series={s}
                      milestones={milestones}
                      testidPrefix={testidPrefix}
                    />
                  )}
                </div>
                {s.superseded?.length > 0 && view === "chart" && (
                  <div
                    role="note"
                    data-testid={`${testidPrefix}-superseded-note`}
                    className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] text-muted-foreground"
                  >
                    <TriangleAlert className="h-3 w-3" aria-hidden="true" />
                    {s.superseded.length} superseded same-day entr
                    {s.superseded.length === 1 ? "y" : "ies"} — switch to Table view to inspect.
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
