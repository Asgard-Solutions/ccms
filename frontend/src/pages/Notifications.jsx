import { useEffect, useMemo, useState } from "react";
import { BellRing, Eye, EyeOff } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import { relativeFromNow, formatDateTime } from "../utils/time";
import { Skeleton } from "../components/ui/skeleton";
import { Button } from "../components/ui/button";
import BreakGlassDialog from "../components/BreakGlassDialog";

const FILTERS = [
  { v: "all", l: "All events" },
  { v: "appointment.booked", l: "Booked" },
  { v: "appointment.updated", l: "Updated" },
  { v: "appointment.cancelled", l: "Cancelled" },
];

export default function Notifications() {
  const { user } = useAuth();
  const [items, setItems] = useState(null);
  const [filter, setFilter] = useState("all");
  const [unmask, setUnmask] = useState(false);
  const [breakGlass, setBreakGlass] = useState(false);

  async function load(opts = {}) {
    setItems(null);
    try {
      const params = filter === "all" ? {} : { event_type: filter };
      if (opts.unmask) {
        params.unmask = true;
        if (opts.reason) params.reason = opts.reason;
      }
      const { data } = await api.get("/notifications", { params });
      setItems(data);
    } catch (err) {
      setItems([]);
      toast.error(formatApiError(err));
    }
  }

  useEffect(() => {
    load({ unmask });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, unmask]);

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
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
            Communication service
          </span>
          <h1 className="mt-2 font-['Outfit'] text-4xl font-medium tracking-tight">
            Notification log
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-[#5C6A61]">
            Mock email and SMS messages. Recipient addresses and body content
            are masked by default — administrators can unmask with a reason.
          </p>
        </div>
        {user.role === "admin" && (
          <Button
            variant="outline"
            onClick={() => (unmask ? setUnmask(false) : setBreakGlass(true))}
            data-testid="notif-unmask-toggle"
            className="rounded-sm"
          >
            {unmask ? <EyeOff className="mr-2 h-4 w-4" /> : <Eye className="mr-2 h-4 w-4" />}
            {unmask ? "Mask PHI" : "Unmask (audited)"}
          </Button>
        )}
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
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-20 rounded-sm" />)}
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
                  <li key={n.id} data-testid={`notif-${n.id}`} className="rounded-sm border border-stone-200 bg-white p-5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="rounded-sm bg-[#EDF2EE] px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-[#526B58]">
                          {n.event_type}
                        </span>
                        <span className="rounded-sm bg-[#F5F5F0] px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-[#5C6A61]">
                          {n.channel}
                        </span>
                        <span className="text-xs text-[#5C6A61]">→ {n.to_address || "—"}</span>
                        {n.unmasked && (
                          <span className="rounded-sm bg-[#FDF6ED] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[#D4A373]">
                            Unmasked
                          </span>
                        )}
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
                    <p className="mt-1 text-sm leading-relaxed text-[#5C6A61]">{n.body || "—"}</p>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      <BreakGlassDialog
        open={breakGlass}
        title="Unmask notification log"
        description="Viewing recipient contact and message body in clear text will be written to the audit log."
        onClose={() => setBreakGlass(false)}
        onSubmit={(reason) => {
          setBreakGlass(false);
          setUnmask(true);
          load({ unmask: true, reason });
        }}
      />
    </div>
  );
}
