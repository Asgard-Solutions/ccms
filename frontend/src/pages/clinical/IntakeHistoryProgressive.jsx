/**
 * IntakeHistoryProgressive — Phase 2 Wave B §2
 * Compact default view + progressive disclosure ("View complete intake
 * history"). Expanded panel renders the existing IntakeHistoryCard so
 * every field and its Edit / Re-import actions remain intact.
 */
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import IntakeHistoryCard from "./IntakeHistoryCard";

const COMPACT_FIELDS = [
  { key: "chief_complaint", label: "Chief complaint" },
  { key: "history_of_present_illness", label: "HPI" },
  { key: "onset_date", label: "Onset" },
  { key: "severity", label: "Severity" },
  { key: "pain_location", label: "Pain location" },
  { key: "aggravating_factors", label: "Aggravating factors" },
  { key: "relieving_factors", label: "Relieving factors" },
  { key: "mechanism_of_injury", label: "Mechanism of injury" },
];

function displayValue(value) {
  if (value == null || value === "") return null;
  if (Array.isArray(value)) return value.length ? value.join(", ") : null;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export default function IntakeHistoryProgressive({ history, patientId, canWrite, onReauthNeeded }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <section data-testid="intake-history-progressive" aria-labelledby="intake-progressive-title" className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <h3 id="intake-progressive-title" className="font-display text-lg font-semibold text-foreground">
            Clinical history
          </h3>
          <p className="text-sm text-muted-foreground">
            Compact summary from the patient&apos;s chart. Expand for the complete intake.
          </p>
        </div>
      </div>

      <dl
        data-testid="intake-history-compact"
        className="grid grid-cols-1 divide-y divide-border/60 rounded-lg border border-border bg-card md:grid-cols-2 md:divide-x md:divide-y-0"
      >
        {COMPACT_FIELDS.map((f, idx) => {
          const v = displayValue(history?.[f.key]);
          return (
            <div key={f.key} data-testid={`intake-compact-${f.key}`} className={`flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-2 ${idx >= 2 ? "md:border-t md:border-border" : ""}`}>
              <dt className="min-w-[150px] text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                {f.label}
              </dt>
              <dd className="min-w-0 flex-1 text-sm text-foreground">
                {v ?? <span className="text-muted-foreground italic">Not documented</span>}
              </dd>
            </div>
          );
        })}
      </dl>

      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-controls="intake-history-expanded"
        data-testid="intake-history-toggle"
        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs text-primary hover:bg-primary/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
      >
        {expanded ? <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" /> : <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />}
        {expanded ? "Hide complete intake history" : "View complete intake history"}
      </button>

      {expanded && (
        <div id="intake-history-expanded" data-testid="intake-history-expanded" role="region" aria-label="Complete intake history">
          <IntakeHistoryCard patientId={patientId} canWrite={canWrite} onReauthNeeded={onReauthNeeded} />
        </div>
      )}
    </section>
  );
}
