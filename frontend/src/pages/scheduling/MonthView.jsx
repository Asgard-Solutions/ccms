import { useMemo } from "react";
import {
  buildMonthGrid,
  groupByDay,
  isoDateKey,
  isToday,
  WEEKDAY_SHORT,
} from "./dateHelpers";

export default function MonthView({ date, appointments, onOpenDay }) {
  const weeks = useMemo(() => buildMonthGrid(date), [date]);
  const apptsByDay = useMemo(() => groupByDay(appointments), [appointments]);
  const currentMonth = date.getMonth();

  return (
    <div data-testid="scheduling-month" className="overflow-hidden rounded-sm border border-border bg-card">
      <div className="grid grid-cols-7 border-b border-border bg-background text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {WEEKDAY_SHORT.map((label) => (
          <div key={label} className="px-3 py-2 text-center">{label}</div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {weeks.map((week, wi) =>
          week.map((d) => {
            const key = isoDateKey(d);
            const count = (apptsByDay.get(key) || []).length;
            const inMonth = d.getMonth() === currentMonth;
            return (
              <button
                key={key}
                type="button"
                data-testid={`scheduling-month-cell-${key}`}
                onClick={() => onOpenDay?.(d)}
                className={`flex min-h-[96px] flex-col items-start gap-1 border-b border-r border-border p-2 text-left transition-colors hover:bg-muted ${
                  wi === weeks.length - 1 ? "border-b-0" : ""
                } ${inMonth ? "bg-card" : "bg-background text-muted-foreground"}`}
              >
                <span
                  className={`font-display text-sm ${isToday(d) ? "rounded-sm bg-primary px-1.5 text-primary-foreground" : ""}`}
                >
                  {d.getDate()}
                </span>
                {count > 0 ? (
                  <span
                    data-testid={`scheduling-month-count-${key}`}
                    className="rounded-sm bg-primary/10 px-1.5 py-0.5 text-[11px] font-semibold text-primary"
                  >
                    {count} appt{count === 1 ? "" : "s"}
                  </span>
                ) : (
                  <span className="text-[11px] text-muted-foreground/60">—</span>
                )}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
