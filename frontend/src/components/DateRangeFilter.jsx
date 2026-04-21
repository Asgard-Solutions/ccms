import { useEffect, useMemo, useState } from "react";
import { Calendar as CalendarIcon } from "lucide-react";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

/**
 * DateRangeFilter — quick-pick chips + optional manual start/end inputs.
 *
 * Presets are windows ending today: "last 30 days", etc.
 * "today" is a single-day window (start of today → now).
 * "custom" reveals two date inputs so the user can pick an arbitrary range.
 *
 * Emits { from: Date|null, to: Date|null, preset } to the parent via onChange.
 * `from`/`to` are JS Dates (start-of-day / end-of-day) to make comparisons
 * easy; `null` means unbounded (used for custom ranges with a missing side).
 */

const PRESETS = [
  { id: "30", label: "Last 30 days", days: 30 },
  { id: "60", label: "Last 60 days", days: 60 },
  { id: "90", label: "Last 90 days", days: 90 },
  { id: "180", label: "Last 180 days", days: 180 },
  { id: "365", label: "Last 365 days", days: 365 },
  { id: "today", label: "Today", days: 0 },
  { id: "all", label: "All time", days: null },
  { id: "custom", label: "Custom", days: -1 },
];

function startOfDay(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

function endOfDay(d) {
  const x = new Date(d);
  x.setHours(23, 59, 59, 999);
  return x;
}

function toInputValue(d) {
  if (!d) return "";
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function rangeForPreset(preset, customFrom, customTo) {
  const now = new Date();
  if (preset === "all") return { from: null, to: null };
  if (preset === "today") return { from: startOfDay(now), to: endOfDay(now) };
  if (preset === "custom") {
    return {
      from: customFrom ? startOfDay(customFrom) : null,
      to: customTo ? endOfDay(customTo) : null,
    };
  }
  const entry = PRESETS.find((p) => p.id === preset);
  if (!entry || entry.days == null) return { from: null, to: null };
  const from = startOfDay(new Date(now.getTime() - entry.days * 86400000));
  return { from, to: endOfDay(now) };
}

export default function DateRangeFilter({
  defaultPreset = "30",
  onChange,
  className = "",
  testId = "date-range-filter",
  label = "Date range",
}) {
  const [preset, setPreset] = useState(defaultPreset);
  const [customFrom, setCustomFrom] = useState(null);
  const [customTo, setCustomTo] = useState(null);

  const range = useMemo(
    () => rangeForPreset(preset, customFrom, customTo),
    [preset, customFrom, customTo],
  );

  useEffect(() => {
    onChange?.({ ...range, preset });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range.from?.getTime(), range.to?.getTime(), preset]);

  return (
    <div
      data-testid={testId}
      className={`rounded-sm border border-border bg-card p-3 ${className}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 pr-2 text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          <CalendarIcon className="h-3.5 w-3.5" />
          {label}
        </span>
        {PRESETS.map((p) => (
          <Button
            key={p.id}
            type="button"
            size="sm"
            variant={preset === p.id ? "default" : "outline"}
            onClick={() => setPreset(p.id)}
            data-testid={`${testId}-preset-${p.id}`}
            className="h-7 rounded-sm px-2.5 text-xs"
          >
            {p.label}
          </Button>
        ))}
      </div>

      {preset === "custom" && (
        <div className="mt-3 flex flex-wrap items-end gap-3 text-xs">
          <div className="flex flex-col gap-1">
            <label className="text-[11px] uppercase tracking-wider text-muted-foreground">
              From
            </label>
            <Input
              type="date"
              value={toInputValue(customFrom)}
              onChange={(e) =>
                setCustomFrom(e.target.value ? new Date(e.target.value) : null)
              }
              data-testid={`${testId}-from`}
              className="h-8 w-40 rounded-sm"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] uppercase tracking-wider text-muted-foreground">
              To
            </label>
            <Input
              type="date"
              value={toInputValue(customTo)}
              onChange={(e) =>
                setCustomTo(e.target.value ? new Date(e.target.value) : null)
              }
              data-testid={`${testId}-to`}
              className="h-8 w-40 rounded-sm"
            />
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Helper that returns true when the given ISO/date string falls within the
 * active `{from, to}` range. Nulls on either side make that side unbounded.
 * If `value` is falsy the row is kept (don't hide rows with missing dates).
 */
export function isInRange(value, range) {
  if (!value) return true;
  if (!range) return true;
  const t = new Date(value).getTime();
  if (Number.isNaN(t)) return true;
  if (range.from && t < range.from.getTime()) return false;
  if (range.to && t > range.to.getTime()) return false;
  return true;
}
