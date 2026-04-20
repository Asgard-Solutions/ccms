import { useMemo } from "react";
import { Plus } from "lucide-react";
import {
  addDays,
  isoDateKey,
  isToday,
  startOfWeek,
  WEEKDAY_SHORT,
} from "./dateHelpers";
import { formatTime } from "../../utils/time";
import { extractDaySpan } from "./useClinicHours";

const PREVIEW_LIMIT = 3;

/**
 * Week view — renders 7 days. Now consumes a pre-aggregated `countsByDate`
 * map instead of paging through every appointment in the range. Each cell
 * shows the day count plus up to PREVIEW_LIMIT sample appointments from the
 * backend aggregation's `samples[]`.
 *
 * Clinic-hours awareness (Task 14):
 *   - Closed days get a subtle muted background + "Closed" pill and the
 *     quick-add "+" is suppressed on those days (book flow still available
 *     from the global New-appointment CTA for exceptions).
 *   - Open-day cells show the configured open/close range under the header
 *     so staff can see the business window at a glance.
 */
export default function WeekView({
  date,
  countsByDate,
  canBook,
  hours,
  includeCancelled = false,
  onOpenDay,
  onOpenAppointment,
  onCreateAt,
}) {
  const weekStart = useMemo(() => startOfWeek(date), [date]);
  const days = useMemo(
    () => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)),
    [weekStart]
  );

  return (
    <div data-testid="scheduling-week" className="overflow-hidden rounded-sm border border-border bg-card">
      <div className="grid grid-cols-1 sm:grid-cols-7 divide-y divide-border sm:divide-x sm:divide-y-0 border-b border-border bg-background text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {days.map((d, i) => (
          <div
            key={`h-${d.toISOString()}`}
            className={`px-4 py-3 ${isToday(d) ? "text-primary" : ""}`}
          >
            <div>{WEEKDAY_SHORT[i]}</div>
            <div className="mt-1 font-display text-lg font-medium text-foreground">
              {d.getDate()}
            </div>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-7 divide-y divide-border sm:divide-x sm:divide-y-0">
        {days.map((d) => {
          const key = isoDateKey(d);
          const entry = countsByDate?.[key] || { count: 0, samples: [] };
          const count = entry.count;
          const preview = (entry.samples || []).slice(0, PREVIEW_LIMIT);
          const extra = Math.max(0, count - preview.length);
          const dayLabel = d.toLocaleDateString("en-US", {
            weekday: "short", month: "short", day: "numeric",
          });
          const span = extractDaySpan(hours, d);
          const isClosed = span.isClosed;
          const hoursLabel = !hours || isClosed || span.openMinutes == null
            ? null
            : (() => {
                const fmt = (m) => {
                  const h = Math.floor(m / 60);
                  const mm = String(m % 60).padStart(2, "0");
                  return `${h}:${mm}`;
                };
                return `${fmt(span.openMinutes)}–${fmt(span.closeMinutes)}`;
              })();
          return (
            <div
              key={key}
              data-testid={`scheduling-week-cell-${key}`}
              data-closed={isClosed ? "true" : "false"}
              className={`group relative flex min-h-[180px] flex-col gap-2 p-3 ${
                isClosed
                  ? "bg-muted/40"
                  : isToday(d)
                  ? "bg-background"
                  : "bg-card"
              }`}
            >
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  data-testid={`scheduling-week-open-day-${key}`}
                  onClick={() => onOpenDay?.(d)}
                  className="group/header flex flex-1 items-center justify-between gap-2 rounded-sm border border-transparent px-2 py-1 text-left hover:border-border hover:bg-muted"
                  aria-label={`Open ${dayLabel} in day view`}
                >
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground group-hover/header:text-foreground">
                    {dayLabel}
                  </span>
                  <span
                    data-testid={`scheduling-week-count-${key}`}
                    className={`rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
                      count === 0
                        ? "bg-muted text-muted-foreground"
                        : "bg-primary/10 text-primary"
                    }`}
                  >
                    {count === 0 ? "0" : `${count} appt${count === 1 ? "" : "s"}`}
                  </span>
                  {includeCancelled && entry.cancelled_count > 0 && (
                    <span
                      data-testid={`scheduling-week-cancelled-count-${key}`}
                      className="rounded-sm bg-destructive-soft px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-destructive"
                      title={`${entry.cancelled_count} cancelled`}
                    >
                      {entry.cancelled_count} cnl
                    </span>
                  )}
                </button>
                {canBook && !isClosed && (
                  <button
                    type="button"
                    data-testid={`scheduling-week-add-${key}`}
                    onClick={() => {
                      const slot = new Date(d);
                      const startH = span.openMinutes != null
                        ? Math.floor(span.openMinutes / 60)
                        : 9;
                      const startMin = span.openMinutes != null
                        ? span.openMinutes % 60
                        : 0;
                      slot.setHours(startH, startMin, 0, 0);
                      onCreateAt?.(slot);
                    }}
                    aria-label={`Book a new appointment on ${dayLabel}`}
                    className="hidden h-6 w-6 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary focus-visible:flex focus-visible:bg-primary/10 focus-visible:text-primary group-hover:flex"
                  >
                    <Plus className="h-3 w-3" />
                  </button>
                )}
              </div>

              {isClosed ? (
                <span
                  data-testid={`scheduling-week-closed-${key}`}
                  className="self-start rounded-sm bg-warning-soft px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-warning"
                >
                  Closed
                </span>
              ) : hoursLabel ? (
                <span
                  data-testid={`scheduling-week-hours-${key}`}
                  className="self-start text-[10px] font-mono text-muted-foreground/80"
                >
                  {hoursLabel}
                </span>
              ) : null}

              {count === 0 ? (
                <div
                  data-testid={`scheduling-week-empty-${key}`}
                  className="flex flex-1 items-center justify-center rounded-sm border border-dashed border-border py-4 text-xs text-muted-foreground/70"
                >
                  No appointments
                </div>
              ) : (
                <ul className="flex flex-1 flex-col gap-1">
                  {preview.map((a) => (
                    <li key={a.id}>
                      <button
                        type="button"
                        data-testid={`scheduling-week-appt-${a.id}`}
                        onClick={() => onOpenAppointment?.(a)}
                        className={`block w-full rounded-sm border-l-2 px-2 py-1.5 text-left text-xs transition-colors ${
                          a.status === "cancelled"
                            ? "border-destructive bg-destructive-soft text-destructive line-through"
                            : "border-primary bg-primary/10 hover:bg-primary/20"
                        }`}
                      >
                        <div className="font-medium text-foreground">{formatTime(a.start_time)}</div>
                        <div className="truncate text-primary">{a.patient_name}</div>
                        <div className="truncate text-[11px] text-muted-foreground">
                          {a.provider_name}
                        </div>
                      </button>
                    </li>
                  ))}
                  {extra > 0 && (
                    <li>
                      <button
                        type="button"
                        data-testid={`scheduling-week-more-${key}`}
                        onClick={() => onOpenDay?.(d)}
                        className="w-full rounded-sm px-2 py-1 text-left text-[11px] font-semibold uppercase tracking-wider text-muted-foreground hover:bg-muted"
                      >
                        +{extra} more
                      </button>
                    </li>
                  )}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
