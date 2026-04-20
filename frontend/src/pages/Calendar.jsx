import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import { addDays, formatTime, startOfWeek } from "../utils/time";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function sameDay(a, b) {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export default function CalendarPage() {
  const [anchor, setAnchor] = useState(() => startOfWeek(new Date()));
  const [appts, setAppts] = useState(null);

  useEffect(() => {
    const from = new Date(anchor).toISOString();
    const to = addDays(anchor, 7).toISOString();
    (async () => {
      setAppts(null);
      try {
        const { data } = await api.get("/appointments", { params: { from, to } });
        setAppts(data);
      } catch {
        setAppts([]);
      }
    })();
  }, [anchor]);

  const days = useMemo(
    () => Array.from({ length: 7 }, (_, i) => addDays(anchor, i)),
    [anchor]
  );

  const apptsByDay = useMemo(() => {
    const m = new Map();
    (appts || []).forEach((a) => {
      const d = new Date(a.start_time);
      const key = d.toDateString();
      if (!m.has(key)) m.set(key, []);
      m.get(key).push(a);
    });
    for (const list of m.values()) {
      list.sort((x, y) => new Date(x.start_time) - new Date(y.start_time));
    }
    return m;
  }, [appts]);

  const rangeLabel = `${days[0].toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  })} – ${days[6].toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })}`;

  return (
    <div data-testid="calendar-page" className="space-y-8 animate-in fade-in duration-300">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
            Provider calendar
          </span>
          <h1 className="mt-2 font-['Outfit'] text-4xl font-medium tracking-tight">
            {rangeLabel}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon"
            data-testid="cal-prev"
            onClick={() => setAnchor(addDays(anchor, -7))}
            className="rounded-sm"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            data-testid="cal-today"
            onClick={() => setAnchor(startOfWeek(new Date()))}
            className="rounded-sm"
          >
            Today
          </Button>
          <Button
            variant="outline"
            size="icon"
            data-testid="cal-next"
            onClick={() => setAnchor(addDays(anchor, 7))}
            className="rounded-sm"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </header>

      {appts === null ? (
        <Skeleton className="h-[560px] rounded-sm" />
      ) : (
        <div className="overflow-hidden rounded-sm border border-stone-200 bg-white">
          <div className="grid grid-cols-7 border-b border-stone-200 bg-[#FAF9F6] text-xs font-semibold uppercase tracking-wider text-[#5C6A61]">
            {days.map((d, i) => (
              <div
                key={d.toISOString()}
                className={`border-r border-stone-200 px-4 py-3 last:border-r-0 ${
                  sameDay(d, new Date()) ? "text-[#526B58]" : ""
                }`}
              >
                <div>{DAY_LABELS[i]}</div>
                <div className="mt-1 font-['Outfit'] text-lg font-medium text-[#1F2924]">
                  {d.getDate()}
                </div>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-7">
            {days.map((d) => {
              const key = d.toDateString();
              const list = apptsByDay.get(key) || [];
              return (
                <div
                  key={key}
                  data-testid={`cal-day-${d.toISOString().slice(0, 10)}`}
                  className={`min-h-[160px] border-r border-b border-stone-200 p-2 last:border-r-0 ${
                    sameDay(d, new Date()) ? "bg-[#FAF9F6]" : "bg-white"
                  }`}
                >
                  {list.length === 0 ? (
                    <div className="flex h-full items-center justify-center py-6 text-xs text-[#A3AFA7]">
                      —
                    </div>
                  ) : (
                    <ul className="space-y-1">
                      {list.map((a) => (
                        <li
                          key={a.id}
                          data-testid={`cal-appt-${a.id}`}
                          className={`cursor-default rounded-r-sm border-l-2 p-2 text-xs ${
                            a.status === "cancelled"
                              ? "border-[#C76D54] bg-[#FBF1EE] text-[#C76D54] line-through"
                              : "border-[#7B9A82] bg-[#EDF2EE] text-[#1F2924] hover:bg-[#E0EBE2]"
                          }`}
                        >
                          <div className="font-medium">{formatTime(a.start_time)}</div>
                          <div className="truncate text-[#526B58]">{a.patient_name}</div>
                          <div className="truncate text-[11px] text-[#5C6A61]">
                            {a.provider_name}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
