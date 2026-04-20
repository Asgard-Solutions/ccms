import { useMemo } from "react";
import { groupByDay, isoDateKey, isToday, MONTH_LONG } from "./dateHelpers";

/**
 * Year view — 12 mini-month grids. Each day coloured by appointment presence:
 *  - empty -> muted background
 *  - >0 appts -> primary tint scaled slightly by intensity (1/2-3/4+)
 * Clicking any day opens Month view anchored to that month.
 */
export default function YearView({ date, appointments, onOpenMonth }) {
  const year = date.getFullYear();
  const apptsByDay = useMemo(() => groupByDay(appointments), [appointments]);

  return (
    <div
      data-testid="scheduling-year"
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
    >
      {MONTH_LONG.map((name, mi) => {
        const first = new Date(year, mi, 1);
        const daysInMonth = new Date(year, mi + 1, 0).getDate();
        const leading = (first.getDay() + 6) % 7; // Monday-first
        const cells = [
          ...Array.from({ length: leading }, () => null),
          ...Array.from({ length: daysInMonth }, (_, i) => new Date(year, mi, i + 1)),
        ];
        let totalMonth = 0;
        for (const d of cells) {
          if (!d) continue;
          totalMonth += (apptsByDay.get(isoDateKey(d)) || []).length;
        }
        return (
          <button
            key={name}
            type="button"
            data-testid={`scheduling-year-month-${mi}`}
            onClick={() => onOpenMonth?.(first)}
            className="group rounded-sm border border-border bg-card p-3 text-left transition-colors hover:border-primary"
          >
            <div className="mb-2 flex items-baseline justify-between">
              <span className="font-display text-sm font-medium">{name}</span>
              <span className="rounded-sm bg-muted px-1.5 py-0.5 text-[11px] font-semibold text-muted-foreground group-hover:bg-primary/10 group-hover:text-primary">
                {totalMonth} appt{totalMonth === 1 ? "" : "s"}
              </span>
            </div>
            <div className="grid grid-cols-7 gap-[2px] text-[10px]">
              {["M","T","W","T","F","S","S"].map((l, i) => (
                <div key={`l-${mi}-${i}`} className="text-center text-muted-foreground/70">{l}</div>
              ))}
              {cells.map((d, i) => {
                if (!d) return <div key={`b-${mi}-${i}`} className="h-4" />;
                const count = (apptsByDay.get(isoDateKey(d)) || []).length;
                const tint =
                  count === 0
                    ? "bg-muted text-muted-foreground/70"
                    : count <= 2
                    ? "bg-primary/15 text-primary"
                    : count <= 4
                    ? "bg-primary/35 text-primary-foreground"
                    : "bg-primary text-primary-foreground";
                return (
                  <div
                    key={isoDateKey(d)}
                    className={`flex h-4 items-center justify-center rounded-[3px] ${tint} ${
                      isToday(d) ? "outline outline-1 outline-primary" : ""
                    }`}
                    title={`${d.toLocaleDateString()} — ${count} appt${count === 1 ? "" : "s"}`}
                  >
                    {d.getDate()}
                  </div>
                );
              })}
            </div>
          </button>
        );
      })}
    </div>
  );
}
