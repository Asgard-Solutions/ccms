import { useState } from "react";
import { ChevronDown, ChevronRight, ClipboardList, History } from "lucide-react";
import { formatDateTime } from "../../../utils/time";

const TIMELINE_KIND_META = {
  history: { label: "Status change", tone: "text-muted-foreground" },
  validation_run: { label: "Scrubber run", tone: "text-warning" },
  submission: { label: "Submission", tone: "text-primary" },
  submission_outcome: { label: "Outcome", tone: "text-success" },
};

/** Collapsible reverse-chronological status timeline. Renders every
 *  `history` + `validation_run` + `submission` + `submission_outcome`
 *  entry the claim accumulates across its lifecycle. */
export function TimelineSection({ loading, timeline }) {
  const [expanded, setExpanded] = useState(true);
  const entries = timeline?.entries || [];

  return (
    <section
      data-testid="claim-timeline-card"
      className="rounded-sm border border-border bg-card p-6"
    >
      <button
        type="button"
        className="mb-3 flex w-full items-center justify-between text-left"
        onClick={() => setExpanded((v) => !v)}
        data-testid="timeline-toggle"
      >
        <div className="flex items-center gap-2">
          <History className="h-4 w-4 text-muted-foreground" />
          <h2 className="font-display text-lg font-medium tracking-tight">
            Status timeline
          </h2>
          <span className="rounded-sm bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
            {entries.length}
          </span>
        </div>
        {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
      </button>
      {expanded && (
        loading ? (
          <p className="text-sm text-muted-foreground">Loading timeline…</p>
        ) : entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">No timeline entries yet.</p>
        ) : (
          <ol className="space-y-2">
            {entries.map((e, i) => {
              const meta = TIMELINE_KIND_META[e.kind] || { label: e.kind, tone: "text-foreground" };
              return (
                <li
                  key={i}
                  data-testid={`timeline-entry-${i}`}
                  className="flex items-start gap-3 rounded-sm border border-border bg-muted/30 p-3"
                >
                  <ClipboardList className={`mt-0.5 h-4 w-4 ${meta.tone}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className={`font-semibold uppercase tracking-wide ${meta.tone}`}>
                        {meta.label}
                      </span>
                      <span className="text-muted-foreground">
                        {formatDateTime(e.at)}
                      </span>
                      {e.by && (
                        <span className="text-muted-foreground">
                          · by {e.by.slice(0, 8)}
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-sm">
                      <span className="font-medium">{e.action}</span>
                      {e.from_status && e.to_status && (
                        <span className="ml-2 text-xs text-muted-foreground">
                          {e.from_status} → {e.to_status}
                        </span>
                      )}
                    </div>
                    {e.metadata && Object.keys(e.metadata).length > 0 && (
                      <pre className="mt-1 overflow-x-auto rounded-sm bg-background p-2 text-[11px] text-muted-foreground">
{JSON.stringify(e.metadata, null, 2)}
                      </pre>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )
      )}
    </section>
  );
}
