import { CalendarDays, RefreshCcw, X } from "lucide-react";
import { Button } from "../../components/ui/button";
import { formatTime, relativeFromNow } from "../../utils/time";
import { isoDateKey, groupByDay } from "./dateHelpers";

function statusChip(status) {
  const map = {
    scheduled: "bg-primary/10 text-primary",
    completed: "bg-muted text-muted-foreground",
    cancelled: "bg-destructive-soft text-destructive",
  };
  return (
    <span
      className={`rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${map[status] || "bg-muted"}`}
    >
      {status}
    </span>
  );
}

export default function DayView({
  date,
  appointments,
  canBook,
  onReschedule,
  onCancel,
}) {
  const key = isoDateKey(date);
  const list = groupByDay(appointments).get(key) || [];

  return (
    <div data-testid={`scheduling-day-${key}`} className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {date.toLocaleDateString("en-US", { weekday: "long" })}
          </div>
          <div className="font-display text-2xl">
            {date.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })}
          </div>
        </div>
        <div
          data-testid="scheduling-day-count"
          className="rounded-sm bg-primary/10 px-3 py-1 text-sm font-semibold text-primary"
        >
          {list.length} {list.length === 1 ? "appointment" : "appointments"}
        </div>
      </div>

      {list.length === 0 ? (
        <div
          data-testid="scheduling-day-empty"
          className="rounded-sm border border-dashed border-border bg-card p-16 text-center"
        >
          <CalendarDays className="mx-auto h-10 w-10 text-muted-foreground/70" />
          <p className="mt-4 font-display text-lg">No appointments</p>
          <p className="mt-1 text-sm text-muted-foreground">
            This day is open. Use “New appointment” to book.
          </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-sm border border-border bg-card">
          <table className="w-full text-left">
            <thead className="border-b border-border bg-background">
              <tr className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <th className="px-6 py-3">When</th>
                <th className="px-6 py-3">Patient</th>
                <th className="px-6 py-3">Provider</th>
                <th className="px-6 py-3">Reason</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3" />
              </tr>
            </thead>
            <tbody>
              {list.map((a) => (
                <tr
                  key={a.id}
                  data-testid={`scheduling-appt-row-${a.id}`}
                  className="border-b border-border last:border-b-0 hover:bg-muted/50"
                >
                  <td className="px-6 py-4 text-sm">
                    <div className="font-medium">{formatTime(a.start_time)}</div>
                    <div className="text-xs text-muted-foreground">
                      {relativeFromNow(a.start_time)}
                    </div>
                  </td>
                  <td className="px-6 py-4 text-sm">{a.patient_name}</td>
                  <td className="px-6 py-4 text-sm text-muted-foreground">{a.provider_name}</td>
                  <td className="px-6 py-4 text-sm text-muted-foreground">{a.reason || "—"}</td>
                  <td className="px-6 py-4">{statusChip(a.status)}</td>
                  <td className="px-6 py-4 text-right">
                    {a.status === "scheduled" && (
                      <div className="flex items-center justify-end gap-2">
                        {canBook && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onReschedule(a)}
                            data-testid={`scheduling-reschedule-${a.id}`}
                            className="rounded-sm text-primary hover:bg-primary/10"
                          >
                            <RefreshCcw className="mr-1 h-3 w-3" /> Reschedule
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onCancel(a)}
                          data-testid={`scheduling-cancel-${a.id}`}
                          className="rounded-sm text-destructive hover:bg-destructive-soft"
                        >
                          <X className="mr-1 h-3 w-3" /> Cancel
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
