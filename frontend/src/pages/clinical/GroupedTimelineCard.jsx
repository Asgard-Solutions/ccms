/**
 * GroupedTimelineCard — Phase 2 Wave A grouped care timeline.
 * Merges visit-linked artefacts (appointment + encounter + note) into a
 * single event; non-visit-linked artefacts (imaging, outcomes, plans)
 * remain their own events. Source IDs preserved.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Calendar, ChevronDown, ChevronRight, ClipboardList, FileText, Image as ImageIcon, Search, Target, X } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Skeleton } from "../../components/ui/skeleton";
import { Input } from "../../components/ui/input";
import { formatDateTime } from "../../utils/time";
import StatusBadge from "./status/StatusBadge";

const KIND_FILTERS = [
  { id: "all",             label: "All activity" },
  { id: "visit",           label: "Encounters" },
  { id: "initial_exam",    label: "Exams" },
  { id: "treatment_plan",  label: "Plans" },
  { id: "clinical_media",  label: "Imaging" },
  { id: "outcome_entry",   label: "Outcomes" },
];
const KIND_ICON = {
  visit: Calendar,
  initial_exam: ClipboardList,
  treatment_plan: Target,
  clinical_media: ImageIcon,
  outcome_entry: Activity,
};

export default function GroupedTimelineCard({ patientId }) {
  const [payload, setPayload] = useState(null);
  const [err, setErr] = useState(null);
  const [kind, setKind] = useState("all");
  const [query, setQuery] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [expanded, setExpanded] = useState(() => new Set());

  const load = useCallback(async () => {
    setErr(null);
    try {
      const { data } = await api.get(`/patients/${patientId}/clinical/timeline/grouped`);
      setPayload(data);
    } catch (e) {
      setErr(formatApiError(e));
      setPayload({ schema_version: "1.0", events: [] });
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  const events = payload?.events || [];
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return events.filter((e) => {
      if (kind !== "all" && e.kind !== kind) return false;
      if (fromDate && (!e.visit_at || e.visit_at < fromDate)) return false;
      if (toDate && (!e.visit_at || e.visit_at > toDate + "T23:59:59Z")) return false;
      if (q) {
        const hay = [e.title, e.provider_name, e.kind].filter(Boolean).join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [events, kind, query, fromDate, toDate]);

  function toggle(key) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function clearFilters() {
    setKind("all");
    setQuery("");
    setFromDate("");
    setToDate("");
  }

  if (payload === null) {
    return <Skeleton className="h-40 rounded-lg" data-testid="grouped-timeline-loading" />;
  }

  return (
    <section data-testid="grouped-timeline-card" aria-labelledby="grouped-timeline-title" className="space-y-4">
      <div>
        <h3 id="grouped-timeline-title" className="font-display text-lg font-semibold text-foreground">
          Care timeline
        </h3>
        <p className="text-sm text-muted-foreground">
          Grouped chronology. Related visit records are merged into one event; every source id is preserved.
        </p>
      </div>

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Timeline kind filters" data-testid="grouped-timeline-filters">
        {KIND_FILTERS.map((k) => {
          const active = kind === k.id;
          return (
            <button
              key={k.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setKind(k.id)}
              data-testid={`grouped-timeline-filter-${k.id}`}
              className={[
                "rounded-full px-3 py-1 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                active
                  ? "bg-primary text-primary-foreground font-medium"
                  : "border border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground",
              ].join(" ")}
            >
              {k.label}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] flex-1">
          <Search aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search title, provider…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            data-testid="grouped-timeline-search"
            className="h-8 rounded-full pl-8 text-xs"
          />
        </div>
        <Input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} data-testid="grouped-timeline-from" className="h-8 w-[150px] rounded-full text-xs" />
        <span className="text-xs text-muted-foreground">to</span>
        <Input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} data-testid="grouped-timeline-to" className="h-8 w-[150px] rounded-full text-xs" />
        <button
          type="button"
          onClick={clearFilters}
          data-testid="grouped-timeline-clear"
          className="inline-flex items-center gap-1 rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground hover:bg-muted"
        >
          <X className="h-3 w-3" aria-hidden="true" />
          Clear filters
        </button>
      </div>

      {err && <div role="alert" className="rounded-sm border border-destructive/30 bg-destructive-soft p-3 text-sm text-destructive">{err}</div>}

      {visible.length === 0 ? (
        <div data-testid="grouped-timeline-empty" className="rounded-lg border border-dashed border-border bg-card/40 px-5 py-4 text-sm text-muted-foreground">
          No events match these filters.
        </div>
      ) : (
        <ol className="space-y-2" data-testid="grouped-timeline-list">
          {visible.map((e, idx) => {
            const key = `${e.kind}:${idx}:${e.visit_at || ""}`;
            const isOpen = expanded.has(key);
            const Icon = KIND_ICON[e.kind] || FileText;
            return (
              <li key={key} className="rounded-lg border border-border bg-card" data-testid={`grouped-timeline-row-${e.kind}-${idx}`}>
                <button
                  type="button"
                  onClick={() => toggle(key)}
                  aria-expanded={isOpen}
                  className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-foreground">
                        {e.visit_at ? formatDateTime(e.visit_at) : "Undated"} — {e.title}
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                        {e.status?.workflow && <StatusBadge dim="workflow" value={e.status.workflow} />}
                        {e.status?.documentation && <StatusBadge dim="documentation" value={e.status.documentation} />}
                        {e.status?.clinical_response && <StatusBadge dim="clinical_response" value={e.status.clinical_response} />}
                        {e.status?.billing && <StatusBadge dim="billing" value={e.status.billing} />}
                        {e.status?.record_state && <StatusBadge dim="record_state" value={e.status.record_state} />}
                        {e.provider_name && <span className="text-[11px] text-muted-foreground">· {e.provider_name}</span>}
                      </div>
                    </div>
                  </div>
                  {isOpen ? <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />}
                </button>
                {isOpen && (
                  <div className="border-t border-border bg-background/40 px-4 py-2 text-xs text-muted-foreground" data-testid={`grouped-timeline-detail-${e.kind}-${idx}`}>
                    <div className="grid grid-cols-2 gap-2">
                      {Object.entries(e.source_ids || {}).map(([k, v]) =>
                        v && (
                          <div key={k}>
                            <span className="uppercase tracking-wider text-[10px]">{k.replace(/_/g, " ")}: </span>
                            <span className="text-foreground/80">
                              {Array.isArray(v) ? v.join(", ") : String(v)}
                            </span>
                          </div>
                        ),
                      )}
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
