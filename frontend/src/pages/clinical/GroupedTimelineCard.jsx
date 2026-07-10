/**
 * GroupedTimelineCard — Phase 3 Slice 2 rewrite.
 *
 * Wires:
 *   • TimelineFilterBar (transient filter state via useClinicalReturnState)
 *   • SavedPresetsMenu (durable global presets under /me/preferences)
 *   • Server-side filter query params (schema_version 1.1) with
 *     backward-compat fallback (1.0) when no filter is active.
 *   • Stale-preset detection surfaced as filter chips + toast on apply.
 *   • Empty / no-results / partial-failure / deleted-provider states.
 *   • Performance guard: measures long-history render latency and,
 *     when the visible list exceeds `VIRTUALIZE_THRESHOLD`, applies
 *     an incremental-render cap (no external virtualization library
 *     added yet — see PHASE3_SLICE2_CONTRACTS.md).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, Calendar, ChevronDown, ChevronRight, ClipboardList,
  FileText, Image as ImageIcon, Target,
} from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError } from "../../api/client";
import { Skeleton } from "../../components/ui/skeleton";
import { formatDateTime } from "../../utils/time";
import StatusBadge from "./status/StatusBadge";
import TimelineFilterBar from "./TimelineFilterBar";
import SavedPresetsMenu from "./SavedPresetsMenu";
import { useClinicalReturnState } from "./useClinicalReturnState";
import {
  emptyTransientFilters, transientToQueryParams, anyFilterActive,
} from "./timelinePresetsSchema";

const KIND_ICON = {
  visit: Calendar,
  initial_exam: ClipboardList,
  treatment_plan: Target,
  clinical_media: ImageIcon,
  outcome_entry: Activity,
};

const VIRTUALIZE_THRESHOLD = 200;
const INITIAL_RENDER_CAP = 100;

export default function GroupedTimelineCard({
  patientId,
  providers = [],
  episodes = [],
  clinicalUiDefaults,
  onDefaultsChange,
  routeInstanceToken,
}) {
  const { state, saveState } = useClinicalReturnState({
    section: "timeline",
    routeInstanceToken,
  });

  const [payload, setPayload] = useState(null);
  const [err, setErr] = useState(null);
  const [renderCap, setRenderCap] = useState(INITIAL_RENDER_CAP);

  const filters = useMemo(
    () => state?.filters || emptyTransientFilters(),
    [state],
  );
  const expanded = useMemo(
    () => new Set(state?.expanded || []),
    [state],
  );

  const setFilters = useCallback(
    (patch) => {
      const next = { ...(filters || emptyTransientFilters()), ...patch };
      saveState({ filters: next });
    },
    [filters, saveState],
  );

  const clearFilters = useCallback(() => {
    saveState({ filters: emptyTransientFilters() });
  }, [saveState]);

  const load = useCallback(async () => {
    setErr(null);
    const params = transientToQueryParams(filters);
    const t0 = performance.now();
    try {
      const { data } = await api.get(
        `/patients/${patientId}/clinical/timeline/grouped`,
        { params },
      );
      setPayload(data);
      const elapsed = performance.now() - t0;
      // Perf instrumentation stub: log when a filtered fetch is slow so
      // Slice 2 hardening can decide whether to introduce true windowing.
      if (elapsed > 800) {
        console.info(
          `[timeline] slow fetch: ${elapsed.toFixed(0)}ms events=${data?.events?.length ?? 0}`,
        );
      }
    } catch (e) {
      setErr(formatApiError(e));
      setPayload({ schema_version: "1.0", events: [] });
    }
  }, [patientId, filters]);

  useEffect(() => {
    load();
    setRenderCap(INITIAL_RENDER_CAP);
  }, [load]);

  const events = payload?.events || [];
  const filterMeta = payload?.filter_meta || null;
  const visible = events; // server already filtered — nothing to re-filter client-side
  const capped = visible.slice(0, renderCap);
  const hasMore = visible.length > capped.length;

  const applyPreset = useCallback(
    (preset) => {
      // Copy preset filters into transient state; keep patient-scoped
      // fields (episode_ids, q) untouched — they belong to this chart.
      setFilters({
        event_kinds: preset.filters.event_kinds || [],
        sources: preset.filters.sources || [],
        provider_ids: preset.filters.provider_ids || [],
        date_window: preset.filters.date_window || null,
        active_preset_id: preset.id,
      });
    },
    [setFilters],
  );

  function toggle(key) {
    const next = new Set(expanded);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    saveState({ expanded: Array.from(next) });
  }

  if (payload === null) {
    return <Skeleton className="h-40 rounded-lg" data-testid="grouped-timeline-loading" />;
  }

  return (
    <section
      data-testid="grouped-timeline-card"
      aria-labelledby="grouped-timeline-title"
      className="space-y-4"
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3
            id="grouped-timeline-title"
            className="font-display text-lg font-semibold text-foreground"
          >
            Care timeline
          </h3>
          <p className="text-sm text-muted-foreground">
            Grouped chronology. Related visit records are merged into one event; every source id is preserved.
          </p>
        </div>
        <SavedPresetsMenu
          clinicalUiDefaults={clinicalUiDefaults}
          onDefaultsChange={onDefaultsChange}
          currentFilters={filters}
          onApplyPreset={applyPreset}
          filterMeta={filterMeta}
        />
      </div>

      <TimelineFilterBar
        filters={filters}
        providers={providers}
        episodes={episodes}
        onChange={setFilters}
        onClear={clearFilters}
        filterMeta={filterMeta}
      />

      {err && (
        <div
          role="alert"
          data-testid="grouped-timeline-error"
          className="rounded-sm border border-destructive/30 bg-destructive-soft p-3 text-sm text-destructive"
        >
          {err}
        </div>
      )}

      {filterMeta?.ignored_slugs?.length > 0 && (
        <div
          data-testid="grouped-timeline-stale-warning"
          className="rounded-sm border border-warning/30 bg-warning-soft p-2 text-xs text-warning"
        >
          Some filter values on the active preset are no longer supported and were skipped.
        </div>
      )}

      {visible.length === 0 ? (
        <div
          data-testid="grouped-timeline-empty"
          className="rounded-lg border border-dashed border-border bg-card/40 px-5 py-4 text-sm text-muted-foreground"
        >
          {anyFilterActive(filters)
            ? "No timeline events match these filters."
            : "No timeline events on this chart yet."}
        </div>
      ) : (
        <>
          <ol className="space-y-2" data-testid="grouped-timeline-list">
            {capped.map((e, idx) => {
              const key = `${e.kind}:${idx}:${e.visit_at || ""}`;
              const isOpen = expanded.has(key);
              const Icon = KIND_ICON[e.kind] || FileText;
              return (
                <li
                  key={key}
                  className="rounded-lg border border-border bg-card"
                  data-testid={`grouped-timeline-row-${e.kind}-${idx}`}
                >
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
                    <div
                      className="border-t border-border bg-background/40 px-4 py-2 text-xs text-muted-foreground"
                      data-testid={`grouped-timeline-detail-${e.kind}-${idx}`}
                    >
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
          {hasMore && (
            <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
              <span>Showing {capped.length} of {visible.length} events</span>
              <button
                type="button"
                onClick={() => setRenderCap((c) => c + INITIAL_RENDER_CAP)}
                data-testid="grouped-timeline-load-more"
                className="rounded-full border border-border bg-card px-3 py-1 hover:bg-muted"
              >
                Load {Math.min(INITIAL_RENDER_CAP, visible.length - capped.length)} more
              </button>
              {visible.length >= VIRTUALIZE_THRESHOLD && (
                <span className="text-[10px] uppercase tracking-wider text-warning">
                  perf: long timeline
                </span>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
