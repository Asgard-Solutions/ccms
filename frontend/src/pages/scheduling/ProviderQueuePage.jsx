import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CheckCircle2,
  ClipboardList,
  Clock,
  FileText,
  Home,
  Play,
  RefreshCw,
  Stethoscope,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "../../api/client";
import { useAuth } from "../../contexts/AuthContext";
import { useProviders } from "../../contexts/ProvidersContext";
import { useAppointmentTypes } from "./useAppointmentTypes";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";

/**
 * Provider Queue — practical, minimal-click worklist for providers.
 *
 * Two sections:
 *   • Up Next (ready_for_provider)
 *   • In Progress (in_progress)
 *
 * Each card surfaces intake status, current room, and three timing
 * indicators (checked-in, roomed, ready-for-provider) so a provider can
 * prioritise the oldest waiter at a glance. Actions:
 *   • Start visit  (ready_for_provider → in_progress)
 *   • Ready for checkout / Complete  (from in_progress)
 *
 * Defaults to the logged-in provider's own queue. Admins / staff can
 * switch providers via the filter.
 */

const POLL_INTERVAL_MS = 20000;
const ALL = "__all__";

function isoDate(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
}

function humanDuration(from) {
  if (!from) return null;
  const diffMs = Date.now() - new Date(from).getTime();
  if (diffMs < 0) return "just now";
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "<1m";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem ? `${hrs}h ${rem}m` : `${hrs}h`;
}

export default function ProviderQueuePage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { providers } = useProviders();
  const { types: appointmentTypes } = useAppointmentTypes({ activeOnly: true });

  const isProvider = user?.role === "doctor";
  const canSwitchProvider = !isProvider; // admin / staff can pick any provider

  const [providerId, setProviderId] = useState(() =>
    isProvider ? user?.id || null : null,
  );
  const [appointments, setAppointments] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Tick every minute so time-since labels stay fresh without refetch.
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 60000);
    return () => clearInterval(t);
  }, []);

  const typeById = useMemo(() => {
    const m = new Map();
    for (const t of appointmentTypes || []) m.set(t.id, t);
    return m;
  }, [appointmentTypes]);

  async function fetchQueue({ silent = false } = {}) {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    try {
      const today = isoDate(new Date());
      const params = { from: `${today}T00:00:00Z`, to: `${today}T23:59:59Z` };
      if (providerId) params.provider_id = providerId;
      const { data } = await api.get("/appointments", { params });
      setAppointments(data || []);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to load queue");
      setAppointments([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    fetchQueue();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId]);

  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") fetchQueue({ silent: true });
    }, POLL_INTERVAL_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId]);

  const upNext = useMemo(
    () =>
      (appointments || [])
        .filter((a) => a.status === "ready_for_provider")
        .sort((a, b) => {
          const xa = a.ready_for_provider_at || a.checked_in_at || a.start_time;
          const xb = b.ready_for_provider_at || b.checked_in_at || b.start_time;
          return new Date(xa) - new Date(xb);
        }),
    [appointments],
  );
  const inProgress = useMemo(
    () =>
      (appointments || [])
        .filter((a) => a.status === "in_progress")
        .sort((a, b) => new Date(a.visit_started_at || 0) - new Date(b.visit_started_at || 0)),
    [appointments],
  );

  async function runAction(appt, endpoint, payload = {}) {
    try {
      const { data } = await api.post(`/appointments/${appt.id}/${endpoint}`, payload);
      setAppointments((prev) =>
        (prev || []).map((a) => (a.id === appt.id ? { ...a, ...data } : a)),
      );
      toast.success("Updated");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Action failed");
    }
  }

  return (
    <div data-testid="provider-queue-page" className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Provider
          </span>
          <h1 className="mt-1 font-display text-3xl font-medium tracking-tight">
            My Queue
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Start, complete, and hand off visits without hunting the calendar.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canSwitchProvider && (
            <Select
              value={providerId || ALL}
              onValueChange={(v) => setProviderId(v === ALL ? null : v)}
            >
              <SelectTrigger
                data-testid="provider-queue-provider"
                className="h-10 w-56 rounded-sm"
              >
                <SelectValue placeholder="All providers" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All providers</SelectItem>
                {providers.map((p) => (
                  <SelectItem
                    key={p.id}
                    value={p.id}
                    data-testid={`provider-queue-provider-${p.id}`}
                  >
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button
            type="button"
            variant="outline"
            data-testid="provider-queue-refresh"
            onClick={() => fetchQueue({ silent: true })}
            disabled={refreshing}
            className="rounded-sm"
          >
            <RefreshCw className={`mr-1.5 h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => navigate("/scheduling/flow-board")}
            className="rounded-sm"
            data-testid="provider-queue-to-board"
          >
            <ClipboardList className="mr-1.5 h-4 w-4" />
            Flow Board
          </Button>
        </div>
      </div>

      <QueueSection
        testid="provider-queue-up-next"
        title="Up Next"
        subtitle="Patients ready for you — roomed or waiting."
        Icon={Stethoscope}
        rows={upNext}
        loading={loading && !appointments}
        typeById={typeById}
        renderActions={(a) => (
          <Button
            size="sm"
            data-testid={`provider-queue-start-${a.id}`}
            onClick={() => runAction(a, "start-visit")}
            className="h-8 rounded-sm bg-primary px-3 text-xs hover:bg-[var(--primary-hover)]"
          >
            <Play className="mr-1 h-3 w-3" />
            Start visit
          </Button>
        )}
      />

      <QueueSection
        testid="provider-queue-in-progress"
        title="In Progress"
        subtitle="Visits you are currently seeing."
        Icon={Clock}
        rows={inProgress}
        loading={loading && !appointments}
        typeById={typeById}
        renderActions={(a) => (
          <div className="flex gap-1.5">
            <Button
              size="sm"
              data-testid={`provider-queue-ready-checkout-${a.id}`}
              onClick={() => runAction(a, "ready-for-checkout")}
              className="h-8 rounded-sm bg-primary px-3 text-xs hover:bg-[var(--primary-hover)]"
            >
              <CheckCircle2 className="mr-1 h-3 w-3" />
              Ready for checkout
            </Button>
            <Button
              size="sm"
              variant="outline"
              data-testid={`provider-queue-complete-${a.id}`}
              onClick={() => runAction(a, "complete")}
              className="h-8 rounded-sm px-3 text-xs"
            >
              <CheckCircle2 className="mr-1 h-3 w-3" />
              Complete
            </Button>
          </div>
        )}
      />
    </div>
  );
}

function QueueSection({ testid, title, subtitle, Icon, rows, loading, typeById, renderActions }) {
  return (
    <section data-testid={testid} className="rounded-sm border border-border bg-card">
      <header className="flex items-center justify-between gap-2 border-b border-border px-5 py-3">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-muted-foreground" />
          <h2 className="font-display text-base font-medium">{title}</h2>
          <span
            data-testid={`${testid}-count`}
            className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
          >
            {rows.length}
          </span>
        </div>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </header>
      <div className="divide-y divide-border">
        {loading ? (
          <div className="space-y-2 p-4">
            {[0, 1].map((i) => <Skeleton key={i} className="h-20 rounded-sm" />)}
          </div>
        ) : rows.length === 0 ? (
          <div
            data-testid={`${testid}-empty`}
            className="px-5 py-10 text-center text-sm text-muted-foreground"
          >
            Queue is empty.
          </div>
        ) : (
          rows.map((a) => (
            <QueueRow
              key={a.id}
              appointment={a}
              typeById={typeById}
              actions={renderActions(a)}
            />
          ))
        )}
      </div>
    </section>
  );
}

function QueueRow({ appointment, typeById, actions }) {
  const apptTime = appointment.start_time
    ? new Date(appointment.start_time).toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
      })
    : null;
  const typeName =
    appointment.appointment_type_id && typeById.get(appointment.appointment_type_id)?.name;

  const timings = [
    { key: "checked", label: "Checked in",   at: appointment.checked_in_at },
    { key: "roomed",  label: "Roomed",       at: appointment.location_updated_at && appointment.current_location_type === "roomed" ? appointment.location_updated_at : null },
    { key: "ready",   label: "Ready",        at: appointment.ready_for_provider_at },
    { key: "started", label: "Visit started", at: appointment.visit_started_at },
  ].filter((t) => t.at);

  return (
    <article
      data-testid={`provider-queue-row-${appointment.id}`}
      className="flex flex-wrap items-center justify-between gap-3 px-5 py-4"
    >
      <div className="min-w-0 space-y-1.5">
        <p className="truncate font-medium">
          <span data-testid={`provider-queue-name-${appointment.id}`}>
            {appointment.patient_name || "Unknown patient"}
          </span>
          {apptTime && (
            <span className="ml-2 text-xs text-muted-foreground">{apptTime}</span>
          )}
          {typeName && (
            <span className="ml-1 text-xs text-muted-foreground">· {typeName}</span>
          )}
        </p>

        <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
          <IntakeBadge appointment={appointment} />
          {appointment.current_room_name && (
            <Badge
              variant="outline"
              data-testid={`provider-queue-room-${appointment.id}`}
              className="rounded-sm"
            >
              <Home className="mr-1 h-2.5 w-2.5" />
              Room: {appointment.current_room_name}
            </Badge>
          )}
          {appointment.current_location_type && !appointment.current_room_name && (
            <Badge variant="outline" className="rounded-sm">
              {appointment.current_location_type.replaceAll("_", " ")}
            </Badge>
          )}
        </div>

        {timings.length > 0 && (
          <div
            data-testid={`provider-queue-timing-${appointment.id}`}
            className="flex flex-wrap items-center gap-3 text-[10px] text-muted-foreground"
          >
            {timings.map((t) => (
              <span
                key={t.key}
                data-testid={`provider-queue-timing-${t.key}-${appointment.id}`}
                className="inline-flex items-center gap-1"
              >
                <Clock className="h-2.5 w-2.5" />
                {t.label} · {humanDuration(t.at)} ago
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="shrink-0">{actions}</div>
    </article>
  );
}

function IntakeBadge({ appointment }) {
  const s = appointment.intake_status || "not_started";
  const label =
    s === "not_started" ? "Intake not started"
      : s === "in_progress" ? "Intake in progress"
      : "Intake completed";
  const tone =
    s === "completed"
      ? "text-emerald-700 dark:text-emerald-300"
      : s === "in_progress"
        ? "text-amber-700 dark:text-amber-300"
        : "text-muted-foreground";
  return (
    <Badge
      variant="outline"
      data-testid={`provider-queue-intake-${appointment.id}`}
      className={`rounded-sm ${tone}`}
    >
      <FileText className="mr-1 h-2.5 w-2.5" />
      {label}
    </Badge>
  );
}
