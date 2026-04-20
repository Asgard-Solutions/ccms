import { useMemo } from "react";
import {
  buildMonthGrid,
  groupByDay,
  isoDateKey,
  isToday,
  WEEKDAY_SHORT,
} from "./dateHelpers";
import { formatTime } from "../../utils/time";

const PREVIEW_LIMIT = 2;

export default function MonthView({ date, appointments, onOpenDay }) {
  const weeks = useMemo(() => buildMonthGrid(date), [date]);
  const apptsByDay = useMemo(() => groupByDay(appointments), [appointments]);
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
            const list = apptsByDay.get(key) || [];
            const count = list.length;
            const preview = list.slice(0, PREVIEW_LIMIT);
            const extra = Math.max(0, count - preview.length);
            const inMonth = d.getMonth() === currentMonth;
            const today = isToday(d);

            return (
              <button
                key={key}
                type="button"
                data-testid={`scheduling-month-cell-${key}`}
                onClick={() => onOpenDay?.(d)}
                aria-label={`${d.toLocaleDateString("en-US", {
                  weekday: "long",
                  month: "long",
                  day: "numeric",
                })} — ${count} appointment${count === 1 ? "" : "s"}`}
                className={`group flex min-h-[120px] flex-col items-stretch gap-1 border-b border-r border-border p-2 text-left transition-colors hover:bg-muted ${
                  wi === weeks.length - 1 ? "border-b-0" : ""
                } ${inMonth ? "bg-card" : "bg-background text-muted-foreground"}`}
              >
                <div className="flex items-center justify-between">
                  <span
                    className={`font-display text-sm ${
                      today
                        ? "rounded-sm bg-primary px-1.5 text-primary-foreground"
                        : ""
                    }`}
                  >
                    {d.getDate()}
                  </span>
                  {count > 0 && (
                    <span
                      data-testid={`scheduling-month-count-${key}`}
                      className="rounded-sm bg-primary/10 px-1.5 py-0.5 text-[11px] font-semibold text-primary"
                    >
                      {count} appt{count === 1 ? "" : "s"}
                    </span>
                  )}
                </div>

                {count === 0 ? (
                  <span className="text-[11px] text-muted-foreground/60">—</span>
                ) : (
                  <ul className="flex flex-col gap-[3px]">
                    {preview.map((a) => (
                      <li
                        key={a.id}
                        data-testid={`scheduling-month-appt-${a.id}`}
                        className={`truncate rounded-[3px] border-l-2 px-1.5 py-0.5 text-[11px] ${
                          a.status === "cancelled"
                            ? "border-destructive bg-destructive-soft text-destructive line-through"
                            : "border-primary bg-primary/10 text-foreground"
                        }`}
                        title={`${formatTime(a.start_time)} · ${a.patient_name}`}
                      >
                        <span className="font-medium">{formatTime(a.start_time)}</span>{" "}
                        <span className="text-muted-foreground">{a.patient_name}</span>
                      </li>
                    ))}
                    {extra > 0 && (
                      <li
                        data-testid={`scheduling-month-more-${key}`}
                        className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground group-hover:text-foreground"
                      >
                        +{extra} more
                      </li>
                    )}
                  </ul>
                )}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
