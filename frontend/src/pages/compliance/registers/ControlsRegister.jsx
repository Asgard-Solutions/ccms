import { useCallback, useEffect, useState } from "react";
import { History as HistoryIcon } from "lucide-react";
import { fetchControls } from "../api";
import { StatusChip, EmptyState, SectionHeader, HistoryDialog } from "../common";
import { Button } from "../../../components/ui/button";
import { formatDate } from "../../../utils/time";

const STATUS_FILTERS = [
  { v: "all", l: "All" },
  { v: "implemented", l: "Implemented" },
  { v: "in_progress", l: "In progress" },
  { v: "planned", l: "Planned" },
  { v: "needs_review", l: "Needs review" },
];

const FRAMEWORKS = ["all", "HIPAA", "SOC2", "ISO27001", "CCPA"];

export default function ControlsRegister() {
  const [rows, setRows] = useState([]);
  const [statusFilter, setStatusFilter] = useState("all");
  const [framework, setFramework] = useState("all");
  const [error, setError] = useState(null);
  const [historyId, setHistoryId] = useState(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const q = {};
      if (statusFilter !== "all") q.status_filter = statusFilter;
      if (framework !== "all") q.framework = framework;
      const data = await fetchControls(q);
      setRows(data);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to load controls");
    }
  }, [statusFilter, framework]);

  useEffect(() => { load(); }, [load]);

  return (
    <section data-testid="controls-register" className="space-y-4">
      <SectionHeader testid="controls-header" title="Control register" count={rows.length} />

      <div className="flex flex-wrap items-center gap-4">
        <div data-testid="controls-status-filters" className="flex flex-wrap gap-1.5">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.v}
              data-testid={`controls-filter-status-${f.v}`}
              onClick={() => setStatusFilter(f.v)}
              className={`rounded-sm px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider transition-colors ${
                statusFilter === f.v ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground hover:bg-secondary-hover"
              }`}
            >
              {f.l}
            </button>
          ))}
        </div>
        <div data-testid="controls-framework-filters" className="flex flex-wrap gap-1.5">
          {FRAMEWORKS.map((f) => (
            <button
              key={f}
              data-testid={`controls-filter-framework-${f.toLowerCase()}`}
              onClick={() => setFramework(f)}
              className={`rounded-sm px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider transition-colors ${
                framework === f ? "bg-foreground text-background" : "bg-secondary text-muted-foreground hover:bg-secondary-hover"
              }`}
            >
              {f === "all" ? "All frameworks" : f}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div data-testid="controls-error" className="rounded-sm border border-destructive-soft bg-destructive-soft p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState testid="controls-empty" label="No controls in this view." />
      ) : (
        <div className="overflow-x-auto rounded-sm border border-border bg-card">
          <table className="w-full min-w-[860px] text-left text-sm">
            <thead className="border-b border-border text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Control</th>
                <th className="px-4 py-3 font-medium">Family</th>
                <th className="px-4 py-3 font-medium">Frameworks</th>
                <th className="px-4 py-3 font-medium">Next review</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium text-right">History</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} data-testid={`control-row-${c.id}`} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 align-top">
                    <div className="text-sm text-foreground">{c.name}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{c.description}</div>
                  </td>
                  <td className="px-4 py-3 align-top text-xs uppercase tracking-wider text-muted-foreground">{c.family.replace("_", " ")}</td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(c.framework_mappings || {}).map(([fw, refs]) => (
                        <span
                          key={fw}
                          data-testid={`control-${c.id}-fw-${fw}`}
                          className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
                          title={(refs || []).join(", ")}
                        >
                          {fw}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 align-top text-xs text-muted-foreground">
                    {c.next_review_at ? formatDate(c.next_review_at) : "—"}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <StatusChip status={c.status} testid={`control-status-${c.id}`} />
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center justify-end">
                      <Button
                        data-testid={`control-history-${c.id}`}
                        variant="ghost"
                        size="sm"
                        onClick={() => setHistoryId(c.id)}
                      >
                        <HistoryIcon className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <HistoryDialog
        entityType="control"
        entityId={historyId}
        open={!!historyId}
        onClose={() => setHistoryId(null)}
      />
    </section>
  );
}
