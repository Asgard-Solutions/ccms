import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Activity, ArrowRight, CalendarDays, Users } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import { formatDateTime, relativeFromNow } from "../utils/time";

/** Return a friendly greeting name. Skips honorifics like "Dr.", "Mrs." etc. */
function greetingName(fullName) {
  if (!fullName) return "there";
  const tokens = fullName.split(/\s+/).filter(Boolean);
  const first = tokens[0] || "";
  if (/\.$/.test(first) && tokens.length > 1) return tokens[1];
  return first;
}
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import { Badge } from "../components/ui/badge";

function Stat({ label, value, helper, icon: Icon }) {
  return (
    <div
      data-testid={`dashboard-stat-${label.toLowerCase().replace(/\s+/g, "-")}`}
      className="rounded-sm border border-subtle bg-card p-6"
    >
      <div className="flex items-start justify-between">
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-strong">
          {label}
        </span>
        <span className="flex h-8 w-8 items-center justify-center rounded-sm surface-sage text-sage-deep">
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <div className="mt-6 font-['Outfit'] text-4xl font-medium tracking-tight text-strong">
        {value}
      </div>
      <div className="mt-2 text-xs text-muted-strong">{helper}</div>
    </div>
  );
}

function statusBadge(status) {
  const map = {
    scheduled: "surface-sage text-sage-deep",
    completed: "surface-muted text-muted-strong",
    cancelled: "surface-danger-soft text-danger",
  };
  return (
    <Badge className={`rounded-sm border-0 font-medium ${map[status] || "bg-stone-100"}`}>
      {status}
    </Badge>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const [data, setData] = useState({ loading: true });

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const now = new Date();
        const from = new Date(now.getTime() - 7 * 86400_000).toISOString();
        const to = new Date(now.getTime() + 30 * 86400_000).toISOString();

        const reqs = [api.get("/appointments", { params: { from, to } })];
        if (user.role !== "patient") reqs.push(api.get("/patients"));
        if (user.role === "admin" || user.role === "staff")
          reqs.push(api.get("/notifications", { params: { limit: 8 } }));

        const [apptsRes, patientsRes, notifsRes] = await Promise.all(reqs);

        if (cancelled) return;
        setData({
          loading: false,
          appointments: apptsRes.data,
          patients: patientsRes ? patientsRes.data : [],
          notifications: notifsRes ? notifsRes.data : [],
        });
      } catch {
        if (!cancelled) setData({ loading: false, error: true });
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [user.role]);

  if (data.loading) {
    return (
      <div className="space-y-8">
        <Skeleton className="h-10 w-64" />
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-40 rounded-sm" />
          ))}
        </div>
      </div>
    );
  }

  const upcoming = (data.appointments || [])
    .filter((a) => a.status === "scheduled" && new Date(a.start_time) >= new Date())
    .slice(0, 5);
  const todayCount = (data.appointments || []).filter((a) => {
    const s = new Date(a.start_time);
    const n = new Date();
    return (
      a.status !== "cancelled" &&
      s.getDate() === n.getDate() &&
      s.getMonth() === n.getMonth() &&
      s.getFullYear() === n.getFullYear()
    );
  }).length;

  const scheduledTotal = (data.appointments || []).filter((a) => a.status === "scheduled").length;

  return (
    <div data-testid="dashboard-page" className="space-y-12 animate-in fade-in slide-in-from-bottom-2 duration-300">
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-strong">
          Overview
        </span>
        <h1 className="mt-2 font-['Outfit'] text-4xl font-medium tracking-tight text-strong sm:text-5xl">
          Hello, {greetingName(user.name)}.
        </h1>
        <p className="mt-4 max-w-xl text-base leading-relaxed text-muted-strong">
          Here is what is happening across the clinic today. Scheduling events
          flow through the in-process event bus to the communication service.
        </p>
      </header>

      <section className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <Stat
          label="Today's appointments"
          value={todayCount}
          helper="Scheduled or completed visits on the books today"
          icon={CalendarDays}
        />
        <Stat
          label="Upcoming"
          value={scheduledTotal}
          helper="All scheduled visits in your view"
          icon={Activity}
        />
        <Stat
          label={user.role === "patient" ? "Your profile" : "Patients in system"}
          value={user.role === "patient" ? "1" : (data.patients?.length ?? 0)}
          helper={user.role === "patient" ? "Update your details in the Patients tab" : "Active patient records"}
          icon={Users}
        />
      </section>

      <section>
        <div className="mb-4 flex items-end justify-between">
          <div>
            <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-strong">
              Upcoming appointments
            </span>
            <h2 className="mt-1 font-['Outfit'] text-2xl font-medium tracking-tight">
              Next on the schedule
            </h2>
          </div>
          <Button variant="ghost" asChild className="text-sage-deep">
            <Link to="/appointments" data-testid="dashboard-view-all-appts">
              View all <ArrowRight className="ml-2 h-4 w-4" />
            </Link>
          </Button>
        </div>

        {upcoming.length === 0 ? (
          <div className="rounded-sm border border-dashed border-subtle bg-card p-12 text-center">
            <p className="text-sm text-muted-strong">No upcoming appointments.</p>
            {user.role !== "patient" && (
              <Button asChild className="mt-4 rounded-sm bg-sage hover:bg-sage-hover">
                <Link to="/appointments" data-testid="dashboard-book-first">
                  Book one
                </Link>
              </Button>
            )}
          </div>
        ) : (
          <div className="overflow-hidden rounded-sm border border-subtle bg-card">
            <table className="w-full text-left">
              <thead className="border-b border-subtle">
                <tr className="text-xs font-semibold uppercase tracking-wider text-muted-strong">
                  <th className="px-6 py-3">When</th>
                  <th className="px-6 py-3">Patient</th>
                  <th className="px-6 py-3">Provider</th>
                  <th className="px-6 py-3">Reason</th>
                  <th className="px-6 py-3">Status</th>
                </tr>
              </thead>
              <tbody>
                {upcoming.map((a) => (
                  <tr
                    key={a.id}
                    data-testid={`dashboard-appt-row-${a.id}`}
                    className="border-b border-stone-100 last:border-b-0 hover:surface-muted/50"
                  >
                    <td className="px-6 py-4 text-sm">
                      <div className="font-medium text-strong">
                        {formatDateTime(a.start_time)}
                      </div>
                      <div className="text-xs text-muted-strong">
                        {relativeFromNow(a.start_time)}
                      </div>
                    </td>
                    <td className="px-6 py-4 text-sm text-strong">
                      {a.patient_name}
                    </td>
                    <td className="px-6 py-4 text-sm text-muted-strong">
                      {a.provider_name}
                    </td>
                    <td className="px-6 py-4 text-sm text-muted-strong">
                      {a.reason || "—"}
                    </td>
                    <td className="px-6 py-4 text-sm">{statusBadge(a.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {(user.role === "admin" || user.role === "staff") && (
        <section>
          <div className="mb-4 flex items-end justify-between">
            <div>
              <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-strong">
                Event bus activity
              </span>
              <h2 className="mt-1 font-['Outfit'] text-2xl font-medium tracking-tight">
                Recent mock notifications
              </h2>
            </div>
            <Button variant="ghost" asChild className="text-sage-deep">
              <Link to="/notifications" data-testid="dashboard-view-all-notifs">
                See all <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </div>
          {(!data.notifications || data.notifications.length === 0) ? (
            <div className="rounded-sm border border-dashed border-subtle bg-card p-12 text-center text-sm text-muted-strong">
              No notifications emitted yet. Book an appointment to see the
              event bus in action.
            </div>
          ) : (
            <ul className="space-y-2">
              {data.notifications.map((n) => (
                <li
                  key={n.id}
                  data-testid={`notif-feed-${n.id}`}
                  className="flex items-start justify-between gap-6 rounded-sm border border-subtle bg-card px-5 py-4"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="rounded-sm surface-sage px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-sage-deep">
                        {n.event_type}
                      </span>
                      <span className="text-[11px] uppercase tracking-wider text-muted-strong">
                        via {n.channel}
                      </span>
                    </div>
                    <p className="mt-2 line-clamp-2 text-sm text-strong">
                      {n.body}
                    </p>
                  </div>
                  <span className="whitespace-nowrap text-xs text-muted-strong">
                    {relativeFromNow(n.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
