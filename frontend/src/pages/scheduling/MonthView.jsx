import { useMemo } from "react";
import { Plus } from "lucide-react";
import {
  buildMonthGrid,
  isoDateKey,
  isToday,
  WEEKDAY_SHORT,
} from "./dateHelpers";
import { formatTime } from "../../utils/time";

const PREVIEW_LIMIT = 2;

/**
 * Month view.
 *
 * Each cell is a <div> (no outer button — avoids invalid nested buttons) so
 * the header, each appointment preview, and the quick-add "+" can each be
 * their own focusable target. Clicking the header opens Day view; clicking
 * a preview opens the reschedule workflow; clicking "+" opens the booking
 * dialog pre-filled for that date.
 */
export default function MonthView({
  date,
  countsByDate,
  canBook,
  onOpenDay,
  onOpenAppointment,
  onCreateAt,
}) {
  const weeks = useMemo(() => buildMonthGrid(date), [date]);
  const currentMonth = date.getMonth();

  return (
    <div
      data-testid="scheduling-month"
      className="overflow-hidden rounded-sm border border-border bg-card"
    >
      <div className="grid grid-cols-7 border-b border-border bg-background text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {WEEKDAY_SHORT.map((label) => (
          <div key={label} className="px-3 py-2 text-center">
            {label}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {weeks.map((week, wi) =>
          week.map((d) => {
            const key = isoDateKey(d);
            const entry = countsByDate?.[key] || { count: 0, samples: [] };
            const count = entry.count;
            const preview = (entry.samples || []).slice(0, PREVIEW_LIMIT);
            const extra = Math.max(0, count - preview.length);
            const inMonth = d.getMonth() === currentMonth;
            const today = isToday(d);
            const openDayLabel = d.toLocaleDateString("en-US", {
              weekday: "long", month: "long", day: "numeric",
            });

            return (
              <div
                key={key}
                data-testid={`scheduling-month-cell-${key}`}
                className={`group relative flex min-h-[120px] flex-col items-stretch gap-1 border-b border-r border-border p-2 ${
                  wi === weeks.length - 1 ? "border-b-0" : ""
                } ${inMonth ? "bg-card" : "bg-background text-muted-foreground"}`}
              >
                <div className="flex items-center justify-between">
                  <button
                    type="button"
                    data-testid={`scheduling-month-open-day-${key}`}
                    onClick={() => onOpenDay?.(d)}
                    aria-label={`Open ${openDayLabel} in Day view`}
                    className={`font-display text-sm transition-colors hover:text-primary ${
                      today
                        ? "rounded-sm bg-primary px-1.5 text-primary-foreground hover:text-primary-foreground"
                        : ""
                    }`}
                  >
                    {d.getDate()}
                  </button>
                  <div className="flex items-center gap-1">
                    {count > 0 && (
                      <button
                        type="button"
                        data-testid={`scheduling-month-count-${key}`}
                        onClick={() => onOpenDay?.(d)}
                        className="rounded-sm bg-primary/10 px-1.5 py-0.5 text-[11px] font-semibold text-primary hover:bg-primary/20"
                        aria-label={`${count} appointments on ${openDayLabel}`}
                      >
                        {count} appt{count === 1 ? "" : "s"}
                      </button>
                    )}
                    {entry.cancelled_count > 0 && (
                      <span
                        data-testid={`scheduling-month-cancelled-count-${key}`}
                        className="rounded-sm bg-destructive-soft px-1.5 py-0.5 text-[10px] font-semibold text-destructive"
                        title={`${entry.cancelled_count} cancelled`}
                      >
                        {entry.cancelled_count} cnl
                      </span>
                    )}
                    {canBook && (
                      <button
                        type="button"
                        data-testid={`scheduling-month-add-${key}`}
                        onClick={() => {
                          const slot = new Date(d);
                          slot.setHours(9, 0, 0, 0);
                          onCreateAt?.(slot);
                        }}
                        aria-label={`Book a new appointment on ${openDayLabel}`}
                        className="hidden h-5 w-5 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary focus-visible:flex focus-visible:bg-primary/10 focus-visible:text-primary group-hover:flex"
                      >
                        <Plus className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                </div>

                {count === 0 ? (
                  <span className="text-[11px] text-muted-foreground/60">—</span>
                ) : (
                  <ul className="flex flex-col gap-[3px]">
                    {preview.map((a) => (
                      <li key={a.id}>
                        <button
                          type="button"
                          data-testid={`scheduling-month-appt-${a.id}`}
                          onClick={() => onOpenAppointment?.(a)}
                          className={`block w-full truncate rounded-[3px] border-l-2 px-1.5 py-0.5 text-left text-[11px] transition-colors ${
                            a.status === "cancelled"
                              ? "border-destructive bg-destructive-soft text-destructive line-through"
                              : "border-primary bg-primary/10 text-foreground hover:bg-primary/20"
                          }`}
                          title={`${formatTime(a.start_time)} · ${a.patient_name}`}
                        >
                          <span className="font-medium">{formatTime(a.start_time)}</span>{" "}
                          <span className="text-muted-foreground">{a.patient_name}</span>
                        </button>
                      </li>
                    ))}
                    {extra > 0 && (
                      <li>
                        <button
                          type="button"
                          data-testid={`scheduling-month-more-${key}`}
                          onClick={() => onOpenDay?.(d)}
                          className="w-full text-left text-[11px] font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground"
                        >
                          +{extra} more
                        </button>
                      </li>
                    )}
                  </ul>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
