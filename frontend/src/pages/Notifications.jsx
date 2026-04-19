import { useEffect, useMemo, useState } from "react";
import { BellRing } from "lucide-react";
import { api } from "../api/client";
import { relativeFromNow, formatDateTime } from "../utils/time";
import { Skeleton } from "../components/ui/skeleton";

const FILTERS = [
  { v: "all", l: "All events" },
  { v: "appointment.booked", l: "Booked" },
  { v: "appointment.updated", l: "Updated" },
  { v: "appointment.cancelled", l: "Cancelled" },
];

export default function Notifications() {
  const [items, setItems] = useState(null);
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    (async () => {
      setItems(null);
      try {
        const { data } = await api.get("/notifications", {
          params: filter === "all" ? {} : { event_type: filter },
        });
        setItems(data);
      } catch {
        setItems([]);
      }
    })();
  }, [filter]);

  const grouped = useMemo(() => {
    if (!items) return null;
    return items.reduce((map, n) => {
      const day = new Date(n.created_at).toDateString();
      if (!map.has(day)) map.set(day, []);
      map.get(day).push(n);
      return map;
    }, new Map());
  }, [items]);

  return (
    <div data-testid="notifications-page" className="space-y-8 animate-in fade-in duration-300">
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
          Communication service
        </span>
        <h1 className="mt-2 font-['Outfit'] text-4xl font-medium tracking-tight">
          Notification log
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-[#5C6A61]">
          Mock email and SMS messages queued by the communication subscriber each
          time an appointment event is published to the in-process event bus.
        </p>
      </header>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.v}
            data-testid={`notif-filter-${f.v}`}
            onClick={() => setFilter(f.v)}
            className={`rounded-sm border px-4 py-1.5 text-sm font-medium transition-colors ${
              filter === f.v
                ? "border-[#7B9A82] bg-[#EDF2EE] text-[#526B58]"
                : "border-stone-200 bg-white text-[#5C6A61] hover:bg-[#F5F5F0]"
            }`}
          >
            {f.l}
          </button>
        ))}
      </div>

      {items === null ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-sm" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-sm border border-dashed border-stone-200 bg-white p-16 text-center">
          <BellRing className="mx-auto h-10 w-10 text-[#A3AFA7]" />
          <p className="mt-4 font-['Outfit'] text-lg">No notifications yet</p>
          <p className="mt-1 text-sm text-[#5C6A61]">
            Book, reschedule, or cancel an appointment to see the event bus in
            action.
          </p>
        </div>
      ) : (
        <div className="space-y-8">
          {Array.from(grouped.entries()).map(([day, list]) => (
            <div key={day}>
              <div className="mb-3 text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
                {day}
              </div>
              <ul className="space-y-2">
                {list.map((n) => (
                  <li
                    key={n.id}
                    data-testid={`notif-${n.id}`}
                    className="rounded-sm border border-stone-200 bg-white p-5"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="rounded-sm bg-[#EDF2EE] px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-[#526B58]">
                          {n.event_type}
                        </span>
                        <span className="rounded-sm bg-[#F5F5F0] px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-[#5C6A61]">
                          {n.channel}
                        </span>
                        <span className="text-xs text-[#5C6A61]">
                          → {n.to_address || "—"}
                        </span>
                      </div>
                      <span className="text-xs text-[#5C6A61]">
                        {formatDateTime(n.created_at)} · {relativeFromNow(n.created_at)}
                      </span>
                    </div>
                    {n.subject && (
                      <div className="mt-3 font-['Outfit'] text-base font-medium text-[#1F2924]">
                        {n.subject}
                      </div>
                    )}
                    <p className="mt-1 text-sm leading-relaxed text-[#5C6A61]">
                      {n.body}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
