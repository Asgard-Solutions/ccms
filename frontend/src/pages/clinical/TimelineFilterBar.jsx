/**
 * TimelineFilterBar — Phase 3 Slice 2.
 *
 * Renders the filter surface for the grouped-timeline card. State is
 * *transient* (session-scope via `useClinicalReturnState`); this
 * component does not persist anything to `/me/preferences` — that job
 * belongs to `SavedPresetsMenu`. Free-text search is transient by
 * construction.
 *
 * Empty / no-results / partial-failure states are owned by
 * `GroupedTimelineCard`; this component only reports intent.
 */
import { Search, X } from "lucide-react";
import { useMemo } from "react";
import { Input } from "../../components/ui/input";
import {
  TIMELINE_EVENT_KINDS,
  TIMELINE_EVENT_KIND_LABEL,
  TIMELINE_DATE_WINDOWS,
  TIMELINE_DATE_WINDOW_LABEL,
  anyFilterActive,
} from "./timelinePresetsSchema";

function toggle(list, value) {
  const set = new Set(list || []);
  if (set.has(value)) set.delete(value);
  else set.add(value);
  return Array.from(set);
}

export default function TimelineFilterBar({
  filters,
  providers = [],
  episodes = [],
  onChange,
  onClear,
  filterMeta,
}) {
  const active = anyFilterActive(filters);
  const ignoredProviders = filterMeta?.ignored_provider_ids || [];
  const ignoredEpisodes = filterMeta?.ignored_episode_ids || [];

  const providerOptions = useMemo(
    () =>
      providers
        .filter((p) => p?.id)
        .map((p) => ({
          id: p.id,
          label: p.name || p.display_name || p.email || p.id,
        })),
    [providers],
  );
  const episodeOptions = useMemo(
    () =>
      episodes
        .filter((e) => e?.id)
        .map((e) => ({ id: e.id, label: e.title || "Episode" })),
    [episodes],
  );

  return (
    <div className="space-y-2" data-testid="timeline-filter-bar">
      <div
        role="tablist"
        aria-label="Timeline event kind filters"
        className="flex flex-wrap gap-2"
      >
        {TIMELINE_EVENT_KINDS.map((kind) => {
          const on = (filters.event_kinds || []).includes(kind);
          return (
            <button
              key={kind}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() =>
                onChange({ event_kinds: toggle(filters.event_kinds, kind) })
              }
              data-testid={`timeline-filter-kind-${kind}`}
              className={[
                "rounded-full px-3 py-1 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                on
                  ? "bg-primary text-primary-foreground font-medium"
                  : "border border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground",
              ].join(" ")}
            >
              {TIMELINE_EVENT_KIND_LABEL[kind]}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] flex-1">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            placeholder="Search title, provider…"
            value={filters.q || ""}
            onChange={(e) => onChange({ q: e.target.value.slice(0, 80) })}
            data-testid="timeline-filter-q"
            className="h-8 rounded-full pl-8 text-xs"
            maxLength={80}
          />
        </div>

        <select
          value={filters.date_window || ""}
          onChange={(e) => onChange({ date_window: e.target.value || null })}
          data-testid="timeline-filter-date-window"
          className="h-8 rounded-full border border-border bg-card px-3 text-xs text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
        >
          <option value="">Any date</option>
          {TIMELINE_DATE_WINDOWS.map((w) => (
            <option key={w} value={w}>
              {TIMELINE_DATE_WINDOW_LABEL[w]}
            </option>
          ))}
        </select>

        {providerOptions.length > 0 && (
          <select
            value=""
            onChange={(e) => {
              const v = e.target.value;
              if (v) onChange({ provider_ids: toggle(filters.provider_ids, v) });
            }}
            data-testid="timeline-filter-provider"
            className="h-8 rounded-full border border-border bg-card px-3 text-xs text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
          >
            <option value="">Add provider…</option>
            {providerOptions.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        )}

        {episodeOptions.length > 0 && (
          <select
            value=""
            onChange={(e) => {
              const v = e.target.value;
              if (v) onChange({ episode_ids: toggle(filters.episode_ids, v) });
            }}
            data-testid="timeline-filter-episode"
            className="h-8 rounded-full border border-border bg-card px-3 text-xs text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
          >
            <option value="">Add episode…</option>
            {episodeOptions.map((e) => (
              <option key={e.id} value={e.id}>
                {e.label}
              </option>
            ))}
          </select>
        )}

        {active && (
          <button
            type="button"
            onClick={onClear}
            data-testid="timeline-filter-clear"
            className="inline-flex items-center gap-1 rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground hover:bg-muted"
          >
            <X className="h-3 w-3" aria-hidden="true" />
            Clear
          </button>
        )}
      </div>

      {(filters.provider_ids?.length > 0 || filters.episode_ids?.length > 0) && (
        <div className="flex flex-wrap gap-1.5">
          {(filters.provider_ids || []).map((pid) => {
            const label =
              providerOptions.find((o) => o.id === pid)?.label ||
              (ignoredProviders.includes(pid) ? "Unavailable provider" : pid);
            const stale = ignoredProviders.includes(pid);
            return (
              <span
                key={`prov-${pid}`}
                data-testid={`timeline-filter-chip-provider-${pid}`}
                className={[
                  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px]",
                  stale
                    ? "border border-warning/40 bg-warning-soft text-warning"
                    : "border border-border bg-card text-muted-foreground",
                ].join(" ")}
              >
                {stale ? "⚠ " : ""}Provider · {label}
                <button
                  type="button"
                  aria-label={`Remove provider ${label}`}
                  onClick={() =>
                    onChange({
                      provider_ids: (filters.provider_ids || []).filter(
                        (x) => x !== pid,
                      ),
                    })
                  }
                  className="ml-0.5 rounded-full text-inherit hover:opacity-70"
                >
                  <X className="h-2.5 w-2.5" aria-hidden="true" />
                </button>
              </span>
            );
          })}
          {(filters.episode_ids || []).map((eid) => {
            const label =
              episodeOptions.find((o) => o.id === eid)?.label ||
              (ignoredEpisodes.includes(eid) ? "Removed episode" : eid);
            const stale = ignoredEpisodes.includes(eid);
            return (
              <span
                key={`ep-${eid}`}
                data-testid={`timeline-filter-chip-episode-${eid}`}
                className={[
                  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px]",
                  stale
                    ? "border border-warning/40 bg-warning-soft text-warning"
                    : "border border-border bg-card text-muted-foreground",
                ].join(" ")}
              >
                {stale ? "⚠ " : ""}Episode · {label}
                <button
                  type="button"
                  aria-label={`Remove episode ${label}`}
                  onClick={() =>
                    onChange({
                      episode_ids: (filters.episode_ids || []).filter(
                        (x) => x !== eid,
                      ),
                    })
                  }
                  className="ml-0.5 rounded-full text-inherit hover:opacity-70"
                >
                  <X className="h-2.5 w-2.5" aria-hidden="true" />
                </button>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
