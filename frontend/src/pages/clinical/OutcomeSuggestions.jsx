/**
 * OutcomeSuggestions — Phase 3 Slice 3.
 *
 * Optional, deterministic reminders that a configured measure has no
 * recent entry. Guardrails from the Slice 3 brief:
 *   - Only fires for instruments in `configured_outcome_measures` on
 *     the active plan AND in `SUPPORTED_INSTRUMENTS`.
 *   - Suggestion is optional AND dismissible (session-scoped).
 *   - Does NOT auto-start, auto-populate, or auto-submit a measure —
 *     clicking "Record" opens the existing OutcomesCard workflow, and
 *     no fields are pre-filled from AI inference.
 */
import { PlusCircle, X } from "lucide-react";
import { Button } from "../../components/ui/button";
import { trackOutcomeSuggestion } from "../../utils/telemetry";

export default function OutcomeSuggestions({
  suggestions,
  onRecord,
  onDismiss,
}) {
  if (!suggestions?.length) return null;
  return (
    <div
      data-testid="outcome-suggestions"
      role="region"
      aria-label="Optional outcome-measure suggestions"
      className="rounded-lg border border-border bg-card/60 p-3"
    >
      <div className="mb-2 text-[11px] uppercase tracking-wider text-muted-foreground">
        Optional measures configured on this plan
      </div>
      <ul className="space-y-1.5">
        {suggestions.map((s) => (
          <li
            key={s.instrument_key}
            data-testid={`outcome-suggestion-${s.instrument_key}`}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-dashed border-border bg-card px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium text-foreground">
                {s.label}
              </div>
              <div className="text-[11px] text-muted-foreground">{s.why}</div>
            </div>
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  trackOutcomeSuggestion({
                    instrument_key: s.instrument_key,
                    interaction: "opened",
                  });
                  onRecord?.(s);
                }}
                data-testid={`outcome-suggestion-${s.instrument_key}-open`}
                className="h-7 rounded-full px-2 text-xs"
              >
                <PlusCircle className="mr-1 h-3 w-3" aria-hidden="true" />
                Record
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Dismiss ${s.short_label} suggestion`}
                onClick={() => {
                  trackOutcomeSuggestion({
                    instrument_key: s.instrument_key,
                    interaction: "dismissed",
                  });
                  onDismiss?.(s);
                }}
                data-testid={`outcome-suggestion-${s.instrument_key}-dismiss`}
                className="h-7 rounded-full px-2 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-2 text-[10px] text-muted-foreground">
        Suggestions are workflow reminders only — no measure is auto-filled or submitted.
      </div>
    </div>
  );
}
