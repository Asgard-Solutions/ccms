/**
 * OutcomeSnapshotCard — Phase 3 Slice 3.
 *
 * Per-instrument snapshot card. Renders baseline, latest, previous,
 * and two labeled deltas. **NO** clinical inference — every derived
 * value is neutrally labeled and delta signs use the raw arithmetic
 * meaning (positive = "value went up", negative = "value went down"),
 * never "improved" / "worsened".
 */
import { AlertTriangle, RefreshCw } from "lucide-react";
import { formatDate } from "../../utils/time";
import { formatDelta } from "./outcomeSeriesHelpers";

function ScoreCell({ point, prefix }) {
  if (!point) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="inline-flex items-baseline gap-1">
      <span
        className="text-2xl font-semibold text-foreground"
        data-testid={`snapshot-${prefix}-score`}
      >
        {point.score}
      </span>
      {point.max_score && (
        <span className="text-xs text-muted-foreground">/ {point.max_score}</span>
      )}
    </span>
  );
}

export default function OutcomeSnapshotCard({
  series,
  onOpenSource,
  testidPrefix,
}) {
  const {
    instrument_label,
    short_label,
    baseline,
    latest,
    previous,
    change_since_baseline,
    change_since_prev,
    insufficient_baseline,
    partial_count,
    usable_count,
  } = series;

  return (
    <section
      data-testid={`${testidPrefix}-card`}
      aria-labelledby={`${testidPrefix}-title`}
      className="rounded-xl border border-border bg-card p-4"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h4
            id={`${testidPrefix}-title`}
            className="text-sm font-semibold text-foreground"
          >
            {instrument_label}
          </h4>
          <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
            {short_label} · {usable_count} entr{usable_count === 1 ? "y" : "ies"}
            {partial_count > 0 && (
              <span className="text-warning"> · {partial_count} incomplete</span>
            )}
          </p>
        </div>
        {latest?.is_amended && (
          <span
            className="inline-flex items-center gap-1 rounded-full border border-warning/40 bg-warning-soft px-2 py-0.5 text-[10px] text-warning"
            title="Latest entry was amended after original creation"
            data-testid={`${testidPrefix}-amended-badge`}
          >
            <RefreshCw className="h-2.5 w-2.5" aria-hidden="true" />
            Amended
          </span>
        )}
      </div>

      <div className="mt-3 grid grid-cols-3 gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
            Baseline
          </div>
          <ScoreCell point={baseline} prefix={`${testidPrefix}-baseline`} />
          {baseline && (
            <div className="mt-0.5 text-[10px] text-muted-foreground">
              {formatDate(baseline.captured_at)}
            </div>
          )}
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
            Previous
          </div>
          <ScoreCell point={previous} prefix={`${testidPrefix}-previous`} />
          {previous && (
            <div className="mt-0.5 text-[10px] text-muted-foreground">
              {formatDate(previous.captured_at)}
            </div>
          )}
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
            Latest
          </div>
          <ScoreCell point={latest} prefix={`${testidPrefix}-latest`} />
          {latest && (
            <div className="mt-0.5 text-[10px] text-muted-foreground">
              {formatDate(latest.captured_at)}
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 rounded-lg bg-card/40 p-2 text-xs">
        <div>
          <span className="text-muted-foreground">Change from baseline: </span>
          <span
            className="font-medium text-foreground"
            data-testid={`${testidPrefix}-change-baseline`}
          >
            {formatDelta(change_since_baseline)}
          </span>
          {baseline && latest && (
            <span className="text-muted-foreground">
              {" "}(from {baseline.score} to {latest.score})
            </span>
          )}
        </div>
        <div>
          <span className="text-muted-foreground">Change from previous: </span>
          <span
            className="font-medium text-foreground"
            data-testid={`${testidPrefix}-change-prev`}
          >
            {formatDelta(change_since_prev)}
          </span>
        </div>
      </div>

      {insufficient_baseline && (
        <div
          role="note"
          data-testid={`${testidPrefix}-insufficient-baseline`}
          className="mt-2 inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] text-muted-foreground"
        >
          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
          Not enough entries to compute a baseline change (need ≥ 2).
        </div>
      )}

      {latest?.entry_id && onOpenSource && (
        <button
          type="button"
          onClick={() => onOpenSource(latest)}
          data-testid={`${testidPrefix}-open-source`}
          className="mt-2 text-[11px] text-primary underline-offset-2 hover:underline"
        >
          Open source entry
        </button>
      )}
    </section>
  );
}
