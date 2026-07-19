/**
 * GroupedEncountersCard — Phase 2 Wave A.
 * One row per visit (appointment_id anchored), grouping the source
 * appointment + encounter + note(s) + billing readiness. Read-only
 * presentation; deep-links preserve source record identifiers.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ChevronDown, ChevronRight } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Skeleton } from "../../components/ui/skeleton";
import { formatDate, formatDateTime } from "../../utils/time";
import StatusBadge from "./status/StatusBadge";

const FILTERS = [
  { id: "needs_action", label: "Needs action" },
  { id: "in_progress",  label: "In progress" },
  { id: "completed",    label: "Completed" },
  { id: "missing_note", label: "Missing note" },
  { id: "billing",      label: "Billing issues" },
  { id: "cancelled",    label: "Cancelled" },
  { id: "all",          label: "All" },
];

// User-preference storage key (global — does NOT include a patient id).
const FILTER_PREF_KEY = "ccms.clinical.groupedEncountersFilter";

function matches(filter, group) {
  const s = group.status || {};
  if (filter === "all") return true;
  if (filter === "cancelled") return s.workflow === "cancelled";
  if (filter === "completed") return s.workflow === "completed";
  if (filter === "in_progress") return s.workflow === "in_progress";
  if (filter === "missing_note") return s.documentation === "missing";
  if (filter === "billing") return s.billing === "warning" || s.billing === "blocked";
  if (filter === "needs_action") {
    // Anything that plausibly needs a clinician's attention.
    return (
      s.documentation === "missing" ||
      s.documentation === "draft" ||
      s.workflow === "in_progress" ||
      s.billing === "warning" ||
      s.billing === "blocked"
    );
  }
  return true;
}

export default function GroupedEncountersCard({ patientId }) {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  const [expanded, setExpanded] = useState(() => new Set());
  const [filter, setFilter] = useState(() => {
    // Item 8: honor a filter hint from a Summary tile click ("Notes" tile
    // deep-links here with filter=missing_note).
    try {
      const hintRaw = window.sessionStorage.getItem("ccms.clinical.filterHint.encounters");
      if (hintRaw) {
        window.sessionStorage.removeItem("ccms.clinical.filterHint.encounters");
        const h = JSON.parse(hintRaw);
        if (h?.hint && ["missing_note", "billing", "in_progress", "completed", "cancelled", "needs_action"].includes(h.hint)) {
          return h.hint;
        }
      }
    } catch {
      /* ignore */
    }
    try {
      return window.localStorage.getItem(FILTER_PREF_KEY) || "needs_action";
    } catch {
      return "needs_action";
    }
  });

  const load = useCallback(async () => {
    setErr(null);
    try {
      const { data } = await api.get(`/patients/${patientId}/clinical/encounters/grouped`);
      setRows(data);
    } catch (e) {
      setErr(formatApiError(e));
      setRows({ schema_version: "1.0", groups: [] });
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    try {
      window.localStorage.setItem(FILTER_PREF_KEY, filter);
    } catch {
      /* ignore */
    }
  }, [filter]);

  const groups = rows?.groups || [];
  const counts = useMemo(() => {
    const c = {};
    for (const f of FILTERS) {
      c[f.id] = groups.filter((g) => matches(f.id, g)).length;
    }
    return c;
  }, [groups]);

  // Default to Needs action if there's unresolved work; otherwise most-recent.
  const effectiveFilter = useMemo(() => {
    if (filter !== "needs_action") return filter;
    return counts.needs_action > 0 ? "needs_action" : "all";
  }, [filter, counts]);

  const visible = useMemo(
    () => groups.filter((g) => matches(effectiveFilter, g)),
    [groups, effectiveFilter],
  );

  function toggle(key) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  if (rows === null) {
    return (
      <section data-testid="grouped-encounters-loading" className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-16 rounded-lg" />
        <Skeleton className="h-16 rounded-lg" />
      </section>
    );
  }

  return (
    <section data-testid="grouped-encounters-card" aria-labelledby="grouped-encounters-title" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="grouped-encounters-title" className="font-display text-lg font-semibold text-foreground">
            Visits &amp; encounters
          </h3>
          <p className="text-sm text-muted-foreground">
            One row per visit — grouped from the underlying appointment, encounter, note, and billing records.
          </p>
        </div>
      </div>

      <div role="tablist" aria-label="Encounter filters" className="flex flex-wrap gap-1.5" data-testid="grouped-encounters-filters">
        {FILTERS.map((f) => {
          const active = effectiveFilter === f.id;
          return (
            <button
              key={f.id}
              type="button"
              role="tab"
              aria-selected={active}
              data-testid={`grouped-encounters-filter-${f.id}`}
              onClick={() => setFilter(f.id)}
              className={[
                "inline-flex min-h-11 items-center gap-1.5 rounded-full px-4 py-2 text-sm transition-colors",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                active
                  ? "bg-primary text-primary-foreground font-semibold"
                  : "border border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground",
              ].join(" ")}
            >
              {f.label}
              {counts[f.id] > 0 && (
                <span className={`rounded-full px-1.5 text-xs font-medium ${active ? "bg-primary-foreground/25" : "bg-muted-foreground/15"}`}>
                  {counts[f.id]}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {err && (
        <div role="alert" data-testid="grouped-encounters-error" className="rounded-sm border border-destructive/30 bg-destructive-soft p-3 text-sm text-destructive">
          {err}
        </div>
      )}

      {visible.length === 0 ? (
        <div data-testid="grouped-encounters-empty" className="rounded-lg border border-dashed border-border bg-card/40 px-5 py-4 text-sm text-muted-foreground">
          Nothing matches this filter.
        </div>
      ) : (
        <ul className="space-y-2" data-testid="grouped-encounters-list">
          {visible.map((g) => {
            const isOpen = expanded.has(g.group_key);
            const s = g.status || {};
            return (
              <li key={g.group_key} data-testid={`grouped-encounter-row-${g.group_key}`} className="rounded-lg border border-border bg-card">
                <button
                  type="button"
                  onClick={() => toggle(g.group_key)}
                  aria-expanded={isOpen}
                  data-testid={`grouped-encounter-toggle-${g.group_key}`}
                  className="flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span className="text-sm font-semibold text-foreground">
                        {g.visit_at ? formatDateTime(g.visit_at) : "Undated visit"}
                      </span>
                      <span className="text-sm text-muted-foreground">
                        {g.appointment_type || "Visit"}
                        {g.visit_number != null ? ` · Visit #${g.visit_number}` : ""}
                      </span>
                      {g.orphaned && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-warning/30 bg-warning-soft px-1.5 py-0.5 text-[10px] font-medium text-warning">
                          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                          Unlinked
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <StatusBadge dim="workflow" value={s.workflow} testId={`grouped-status-workflow-${g.group_key}`} />
                      <StatusBadge dim="documentation" value={s.documentation} testId={`grouped-status-doc-${g.group_key}`} />
                      <StatusBadge dim="clinical_response" value={s.clinical_response} testId={`grouped-status-response-${g.group_key}`} />
                      <StatusBadge dim="billing" value={s.billing} testId={`grouped-status-billing-${g.group_key}`} />
                      {g.provider_name && (
                        <span className="text-xs text-muted-foreground">· {g.provider_name}</span>
                      )}
                    </div>
                    {(s.billing === "warning" || s.billing === "blocked") && (g.billing_top_message || g.billing_message) && (
                      <div
                        data-testid={`grouped-billing-message-${g.group_key}`}
                        className={`mt-1.5 text-sm ${s.billing === "blocked" ? "text-destructive" : "text-warning"}`}
                      >
                        {g.billing_top_message || g.billing_message}
                      </div>
                    )}
                  </div>
                  {isOpen ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" /> : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
                </button>
                {isOpen && (
                  <div className="border-t border-border bg-background/40 px-4 py-3 text-sm" data-testid={`grouped-encounter-detail-${g.group_key}`}>
                    <dl className="grid grid-cols-1 gap-x-6 gap-y-1 md:grid-cols-2">
                      {g.source_ids.appointment_id && (
                        <div>
                          <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">Appointment</dt>
                          <dd className="text-xs text-foreground">
                            <Link to={`/scheduling?appointment=${g.source_ids.appointment_id}`} className="text-primary hover:underline">
                              Open appointment
                            </Link>{" "}
                            <span className="text-muted-foreground">· {g.source_ids.appointment_id}</span>
                          </dd>
                        </div>
                      )}
                      {g.source_ids.encounter_id && (
                        <div>
                          <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">Encounter</dt>
                          <dd className="text-xs text-foreground">
                            <span className="text-muted-foreground">{g.source_ids.encounter_id}</span>
                          </dd>
                        </div>
                      )}
                      {g.source_ids.note_ids?.length > 0 && (
                        <div>
                          <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">Notes ({g.source_ids.note_ids.length})</dt>
                          <dd className="text-xs text-foreground">
                            {g.source_ids.note_ids.map((n) => (
                              <div key={n} className="text-muted-foreground">{n}</div>
                            ))}
                          </dd>
                        </div>
                      )}
                      {g.source_ids.billing_readiness_id && (
                        <div>
                          <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">Billing readiness</dt>
                          <dd className="text-xs text-muted-foreground">{g.source_ids.billing_readiness_id}</dd>
                        </div>
                      )}
                    </dl>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
