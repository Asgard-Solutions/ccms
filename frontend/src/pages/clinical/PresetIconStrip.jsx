/**
 * PresetIconStrip — Slice 2.1 polish.
 *
 * Purely presentational summary of *which dimensions* a sanitized
 * timeline preset configures. Nothing derived from raw filter values
 * is ever shown — no search text, dates, provider names, episode
 * labels, or record identifiers can surface here.
 *
 * The strip:
 *   - Shows one icon per configured dimension (event kinds, sources,
 *     providers, date window).
 *   - Attaches an accessible label ("Filters 2 event kinds") + tooltip
 *     via native `title`, so screen readers announce the shape.
 *   - Displays a compact numeric count *only* for the multi-select
 *     dimensions. The date-window icon is present-or-absent — no
 *     value leaks.
 *   - Bubbles up staleness via `detectStaleness()` from the shared
 *     preset schema — never re-derives its own rules.
 *   - Reuses the sanitized preset shape so persistence / migration
 *     logic remains untouched.
 *
 * Guardrails locked in by `PresetIconStrip.test.jsx`:
 *   - Empty preset renders zero icons.
 *   - Partial preset renders only the configured dimensions.
 *   - Unsupported dimensions in the input are silently ignored.
 *   - Stale dimensions receive the warning affordance.
 */
import { Calendar, Database, Layers, Users } from "lucide-react";
import { useMemo } from "react";
import { detectStaleness } from "./timelinePresetsSchema";

// Allow-listed dimension keys. Anything not in this map is ignored by
// the strip so a forward-incompatible preset never gets an ad-hoc icon.
const DIMENSION_META = {
  event_kinds: {
    icon: Layers,
    label: "event kinds",
    countable: true,
  },
  sources: {
    icon: Database,
    label: "sources",
    countable: true,
  },
  provider_ids: {
    icon: Users,
    label: "providers",
    countable: true,
  },
  date_window: {
    icon: Calendar,
    label: "date window",
    countable: false,
  },
};

const DIMENSION_ORDER = [
  "event_kinds",
  "sources",
  "provider_ids",
  "date_window",
];

/**
 * Given a sanitized preset filter blob, return the ordered list of
 * dimensions the strip should render. Never inspects raw values — the
 * count comes from `Array.length` and the presence of `date_window`
 * is checked but not read.
 */
export function buildDimensionsForStrip(filters) {
  if (!filters || typeof filters !== "object") return [];
  const out = [];
  for (const key of DIMENSION_ORDER) {
    if (!(key in DIMENSION_META)) continue;
    const meta = DIMENSION_META[key];
    if (meta.countable) {
      const list = filters[key];
      if (Array.isArray(list) && list.length > 0) {
        out.push({ key, count: list.length });
      }
    } else {
      // Presence check only; value never surfaced.
      if (filters[key]) out.push({ key, count: null });
    }
  }
  return out;
}

/**
 * Given a preset and the current `filter_meta` echoed by the server,
 * return a Set of dimension keys that carry stale content.
 */
function buildStaleSet(preset, filterMeta) {
  const set = new Set();
  if (!preset || !filterMeta) return set;
  const staleness = detectStaleness(preset, filterMeta);
  if (!staleness.stale) return set;
  for (const reason of staleness.reasons) {
    if (reason.key === "provider_ids") set.add("provider_ids");
    if (reason.key === "vocab") {
      // Vocab drop-outs land in ignored_slugs; map back to which
      // dimension the slug came from without exposing the slug itself.
      const v = reason.value;
      if ((preset.filters?.event_kinds || []).includes(v)) set.add("event_kinds");
      if ((preset.filters?.sources || []).includes(v)) set.add("sources");
      if (preset.filters?.date_window === v) set.add("date_window");
    }
  }
  return set;
}

export default function PresetIconStrip({
  preset,
  filterMeta,
  testidPrefix,
  size = "md",
}) {
  const dims = useMemo(
    () => buildDimensionsForStrip(preset?.filters),
    [preset],
  );
  const staleSet = useMemo(
    () => buildStaleSet(preset, filterMeta),
    [preset, filterMeta],
  );

  if (!preset || dims.length === 0) {
    return (
      <span
        data-testid={`${testidPrefix || "preset-icon-strip"}-empty`}
        className="text-[10px] italic text-muted-foreground"
      >
        No filters
      </span>
    );
  }

  const iconClass = size === "sm" ? "h-2.5 w-2.5" : "h-3 w-3";

  return (
    <span
      role="list"
      aria-label="Configured filter dimensions"
      data-testid={testidPrefix || "preset-icon-strip"}
      className="inline-flex items-center gap-1"
    >
      {dims.map(({ key, count }) => {
        const meta = DIMENSION_META[key];
        const Icon = meta.icon;
        const stale = staleSet.has(key);
        const label = meta.countable
          ? `${count} ${meta.label}`
          : `${meta.label}`;
        const staleLabel = stale ? ", stale" : "";
        return (
          <span
            key={key}
            role="listitem"
            data-testid={`${testidPrefix || "preset-icon-strip"}-${key}`}
            title={`${label}${staleLabel}`}
            aria-label={`${label}${staleLabel}`}
            className={[
              "inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] leading-none",
              stale
                ? "border border-warning/40 bg-warning-soft text-warning"
                : "border border-border bg-card text-muted-foreground",
            ].join(" ")}
          >
            <Icon className={iconClass} aria-hidden="true" />
            {meta.countable && (
              <span
                data-testid={`${testidPrefix || "preset-icon-strip"}-${key}-count`}
              >
                {count}
              </span>
            )}
            {stale && (
              <span
                data-testid={`${testidPrefix || "preset-icon-strip"}-${key}-stale`}
                aria-hidden="true"
              >
                ⚠
              </span>
            )}
          </span>
        );
      })}
    </span>
  );
}
