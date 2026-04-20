import { isoDateKey, isToday, MONTH_LONG } from "./dateHelpers";

const MINI_WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

function tintFor(count) {
  if (count === 0) return "bg-muted text-muted-foreground/70 hover:bg-muted";
  if (count <= 2) return "bg-primary/15 text-primary hover:bg-primary/25";
  if (count <= 4) return "bg-primary/35 text-foreground hover:bg-primary/45";
  return "bg-primary text-primary-foreground hover:brightness-110";
}

/**
 * Year view — 12 mini-month grids. Consumes the pre-aggregated
 * `countsByDate` map — counts-only (no samples) — which is the most
 * efficient payload of all: one aggregation covers ~365 days.
 */
export default function YearView({ date, countsByDate, onOpenDay, onOpenMonth }) {
  const year = date.getFullYear();

  return (
    <div
      data-testid="scheduling-year"
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
    >
      {MONTH_LONG.map((name, mi) => {
        const first = new Date(year, mi, 1);
        const daysInMonth = new Date(year, mi + 1, 0).getDate();
        const leading = first.getDay(); // 0 = Sunday; now matches Sunday-first header
        const cells = [
          ...Array.from({ length: leading }, () => null),
          ...Array.from({ length: daysInMonth }, (_, i) => new Date(year, mi, i + 1)),
        ];
        let totalMonth = 0;
        for (const d of cells) {
          if (!d) continue;
          totalMonth += (countsByDate?.[isoDateKey(d)]?.count) || 0;
        }

        return (
          <div
            key={name}
            data-testid={`scheduling-year-month-${mi}`}
            className="rounded-sm border border-border bg-card p-3"
          >
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <button
                type="button"
                data-testid={`scheduling-year-month-header-${mi}`}
                onClick={() => onOpenMonth?.(first)}
                className="font-display text-sm font-medium hover:text-primary"
              >
                {name}
              </button>
              <span
                data-testid={`scheduling-year-month-total-${mi}`}
                className="rounded-sm bg-muted px-1.5 py-0.5 text-[11px] font-semibold text-muted-foreground"
              >
                {totalMonth} appt{totalMonth === 1 ? "" : "s"}
              </span>
            </div>
            <div className="grid grid-cols-7 gap-[2px] text-[10px]">
              {MINI_WEEKDAYS.map((l, i) => (
                <div key={`l-${mi}-${i}`} className="text-center text-muted-foreground/70">
                  {l}
                </div>
              ))}
              {cells.map((d, i) => {
                if (!d) return <div key={`b-${mi}-${i}`} className="h-4" />;
                const key = isoDateKey(d);
                const count = (countsByDate?.[key]?.count) || 0;
                const tint = tintFor(count);
                const label = `${d.toLocaleDateString("en-US", {
                  weekday: "short", month: "short", day: "numeric",
                })} — ${count} appointment${count === 1 ? "" : "s"}`;
                return (
                  <button
                    key={key}
                    type="button"
                    data-testid={`scheduling-year-day-${key}`}
                    onClick={() => onOpenDay?.(d)}
                    title={label}
                    aria-label={label}
                    className={`flex h-4 items-center justify-center rounded-[3px] text-[10px] transition-colors ${tint} ${
                      isToday(d) ? "outline outline-1 outline-primary" : ""
                    }`}
                  >
                    {d.getDate()}
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
