import { useEffect, useMemo, useState } from "react";
import {
  Clock,
  DoorOpen,
  Filter,
  LogOut,
  RefreshCw,
  RotateCcw,
  Stethoscope,
  UserCheck,
  UsersRound,
  XCircle,
  CheckCircle2,
  FileText,
  Home,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { useProviders } from "../../contexts/ProvidersContext";
import { useAppointmentTypes } from "./useAppointmentTypes";
import RoomAssignmentControl from "./RoomAssignmentControl";

/**
 * Patient Flow Board — single-page operational view of every appointment's
 * current stage and the front-desk/provider actions valid for that stage.
 *
 * Column assignment is mutually exclusive, by priority:
 *   1. checked_out (within last 2h)  → "Recently Checked Out"
 *   2. ready_for_checkout             → "Ready for Checkout"
 *   3. in_progress                    → "In Progress"
 *   4. ready_for_provider             → "Ready for Provider"
 *   5. checked_in + location=roomed   → "Roomed"
 *   6. checked_in                     → "Waiting Room"
 *
 * Status + intake + location are always shown as explicit text labels (not
 * color-only) so operators using screen readers or high-contrast settings
 * still get the full picture.
 */

const ALL = "__all__";
const POLL_INTERVAL_MS = 20000;

const COLUMNS = [
  { key: "waiting_room",       label: "Waiting Room",         accent: "border-amber-400/70",      Icon: UsersRound },
  { key: "roomed",             label: "Roomed",               accent: "border-blue-400/70",       Icon: DoorOpen },
  { key: "ready_for_provider", label: "Ready for Provider",   accent: "border-indigo-400/70",     Icon: Stethoscope },
  { key: "in_progress",        label: "In Progress",          accent: "border-primary/70",        Icon: Clock },
  { key: "ready_for_checkout", label: "Ready for Checkout",   accent: "border-orange-400/70",     Icon: CheckCircle2 },
  { key: "recently_checked_out", label: "Recently Checked Out", accent: "border-muted-foreground/50", Icon: LogOut },
];

const INTAKE_TONE = {
  not_started: "text-muted-foreground",
  in_progress: "text-amber-700 dark:text-amber-300",
  completed: "text-emerald-700 dark:text-emerald-300",
};

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

function stageStart(appt, columnKey) {
  switch (columnKey) {
    case "waiting_room": return appt.checked_in_at;
    case "roomed":       return appt.location_updated_at || appt.checked_in_at;
    case "ready_for_provider": return appt.ready_for_provider_at;
    case "in_progress":  return appt.visit_started_at;
    case "ready_for_checkout": return appt.ready_for_checkout_at;
    case "recently_checked_out": return appt.checked_out_at;
    default: return appt.updated_at;
  }
}

function classifyAppointment(appt) {
  const s = appt.status;
  if (s === "checked_out") {
    // "Recently" = within last 2 hours of checked_out_at.
    const t = appt.checked_out_at ? new Date(appt.checked_out_at).getTime() : 0;
    if (t && Date.now() - t < 2 * 60 * 60 * 1000) return "recently_checked_out";
    return null;
  }
  if (s === "ready_for_checkout") return "ready_for_checkout";
  if (s === "in_progress") return "in_progress";
  if (s === "ready_for_provider") return "ready_for_provider";
  if (s === "checked_in") {
    return appt.current_location_type === "roomed" ? "roomed" : "waiting_room";
  }
  return null;
}

export default function FlowBoardPage() {
  const { providers } = useProviders();
  const { types: appointmentTypes } = useAppointmentTypes({ activeOnly: true });

  const [date, setDate] = useState(() => isoDate(new Date()));
  const [providerId, setProviderId] = useState(null);
  const [locationId, setLocationId] = useState(null);
  const [locations, setLocations] = useState([]);
  const [statusFilter, setStatusFilter] = useState(null);
  const [intakeFilter, setIntakeFilter] = useState(null);

  const [appointments, setAppointments] = useState(null);
  const [rooms, setRooms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Tick every minute so elapsed-time indicators stay fresh without refetch.
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 60000);
    return () => clearInterval(t);
  }, []);

  // Tenant/location list — used for location filter.
  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/tenancy/me/context");
        setLocations(data?.locations || []);
      } catch {
        /* non-fatal — show global filter only */
      }
    })();
  }, []);

  // Fetch active rooms for the room-picker.
  useEffect(() => {
    (async () => {
      try {
        const params = { active_only: true };
        if (locationId) params.location_id = locationId;
        const { data } = await api.get("/rooms", { params });
        setRooms(data || []);
      } catch {
        setRooms([]);
      }
    })();
  }, [locationId]);

  const providerName = useMemo(() => {
    const m = new Map();
    for (const p of providers) m.set(p.id, p.name);
    return m;
  }, [providers]);

  const typeById = useMemo(() => {
    const m = new Map();
    for (const t of appointmentTypes || []) m.set(t.id, t);
    return m;
  }, [appointmentTypes]);

  async function fetchBoard({ silent = false } = {}) {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    try {
      const params = {
        from: `${date}T00:00:00Z`,
        to: `${date}T23:59:59Z`,
      };
      if (providerId) params.provider_id = providerId;
      if (locationId) params.location_id = locationId;
      const { data } = await api.get("/appointments", { params });
      setAppointments(data || []);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to load board");
      setAppointments([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  // Initial + on-filter fetch.
  useEffect(() => {
    fetchBoard();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, providerId, locationId]);

  // Silent polling every 20s while the page is visible.
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") fetchBoard({ silent: true });
    }, POLL_INTERVAL_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, providerId, locationId]);

  const grouped = useMemo(() => {
    const out = Object.fromEntries(COLUMNS.map((c) => [c.key, []]));
    if (!appointments) return out;
    for (const a of appointments) {
      // Apply in-memory filters that the list endpoint doesn't support.
      if (statusFilter && a.status !== statusFilter) continue;
      if (intakeFilter && a.intake_status !== intakeFilter) continue;
      const col = classifyAppointment(a);
      if (!col) continue;
      out[col].push(a);
    }
    // Sort each column by stage-start ascending (oldest waiting first).
    for (const key of Object.keys(out)) {
      out[key].sort((x, y) => {
        const xs = stageStart(x, key);
        const ys = stageStart(y, key);
        if (!xs && !ys) return 0;
        if (!xs) return 1;
        if (!ys) return -1;
        return new Date(xs) - new Date(ys);
      });
    }
    return out;
  }, [appointments, statusFilter, intakeFilter]);

  // Room occupancy index — a single scan across active appointments so the
  // picker can mark each room's current occupant without extra queries.
  const occupantByRoomId = useMemo(() => {
    const m = new Map();
    for (const a of appointments || []) {
      if (
        a.current_room_id
        && !["no_show", "canceled", "cancelled", "checked_out"].includes(a.status)
      ) {
        m.set(a.current_room_id, {
          appointment_id: a.id,
          patient_name: a.patient_name,
          patient_id: a.patient_id,
        });
      }
    }
    return m;
  }, [appointments]);

  async function runAction(appt, endpoint, payload = {}) {
    try {
      const { data } = await api.post(`/appointments/${appt.id}/${endpoint}`, payload);
      // Merge update locally for instant UI without re-fetch.
      setAppointments((prev) =>
        (prev || []).map((a) => (a.id === appt.id ? { ...a, ...data } : a)),
      );
      toast.success("Updated");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Action failed");
    }
  }

  return (
    <div data-testid="flow-board-page" className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Operations
          </span>
          <h1 className="mt-1 font-display text-3xl font-medium tracking-tight">
            Patient Flow Board
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Live operational view for check-in, room flow, and checkout.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          data-testid="flow-board-refresh"
          onClick={() => fetchBoard({ silent: true })}
          disabled={refreshing}
          className="rounded-sm"
        >
          <RefreshCw className={`mr-1.5 h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Filters */}
      <div
        data-testid="flow-board-filters"
        className="flex flex-wrap items-center gap-3 rounded-sm border border-border bg-muted/40 p-3"
      >
        <Filter className="h-4 w-4 text-muted-foreground" />
        <Input
          type="date"
          data-testid="flow-board-date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="h-9 w-44 rounded-sm"
        />
        <Select value={providerId || ALL} onValueChange={(v) => setProviderId(v === ALL ? null : v)}>
          <SelectTrigger data-testid="flow-board-provider" className="h-9 w-52 rounded-sm">
            <SelectValue placeholder="All providers" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All providers</SelectItem>
            {providers.map((p) => (
              <SelectItem key={p.id} value={p.id} data-testid={`flow-board-provider-${p.id}`}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {locations.length > 1 && (
          <Select
            value={locationId || ALL}
            onValueChange={(v) => setLocationId(v === ALL ? null : v)}
          >
            <SelectTrigger data-testid="flow-board-location" className="h-9 w-52 rounded-sm">
              <SelectValue placeholder="All locations" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All locations</SelectItem>
              {locations.map((l) => (
                <SelectItem key={l.id} value={l.id} data-testid={`flow-board-location-${l.id}`}>
                  {l.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Select
          value={statusFilter || ALL}
          onValueChange={(v) => setStatusFilter(v === ALL ? null : v)}
        >
          <SelectTrigger data-testid="flow-board-status" className="h-9 w-48 rounded-sm">
            <SelectValue placeholder="Any status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Any status</SelectItem>
            <SelectItem value="checked_in">Checked in</SelectItem>
            <SelectItem value="ready_for_provider">Ready for provider</SelectItem>
            <SelectItem value="in_progress">In progress</SelectItem>
            <SelectItem value="ready_for_checkout">Ready for checkout</SelectItem>
            <SelectItem value="checked_out">Checked out</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={intakeFilter || ALL}
          onValueChange={(v) => setIntakeFilter(v === ALL ? null : v)}
        >
          <SelectTrigger data-testid="flow-board-intake" className="h-9 w-44 rounded-sm">
            <SelectValue placeholder="Any intake" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Any intake</SelectItem>
            <SelectItem value="not_started">Not started</SelectItem>
            <SelectItem value="in_progress">In progress</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
          </SelectContent>
        </Select>
        {(providerId || locationId || statusFilter || intakeFilter) && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-testid="flow-board-clear-filters"
            onClick={() => {
              setProviderId(null);
              setLocationId(null);
              setStatusFilter(null);
              setIntakeFilter(null);
            }}
            className="rounded-sm"
          >
            <RotateCcw className="mr-1 h-3 w-3" />
            Clear
          </Button>
        )}
      </div>

      {/* Board columns */}
      {loading && !appointments ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
          {COLUMNS.map((c) => (
            <Skeleton key={c.key} className="h-72 rounded-sm" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
          {COLUMNS.map((col) => (
            <FlowColumn
              key={col.key}
              column={col}
              rows={grouped[col.key] || []}
              providerName={providerName}
              typeById={typeById}
              rooms={rooms}
              occupantByRoomId={occupantByRoomId}
              onAction={runAction}
              onUpdated={(data) =>
                setAppointments((prev) =>
                  (prev || []).map((a) => (a.id === data.id ? { ...a, ...data } : a)),
                )
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

function FlowColumn({ column, rows, providerName, typeById, rooms, occupantByRoomId, onAction, onUpdated }) {
  const { Icon } = column;
  return (
    <section
      data-testid={`flow-col-${column.key}`}
      className={`flex min-h-[280px] flex-col rounded-sm border-t-4 ${column.accent} border border-border bg-card`}
    >
      <header className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-muted-foreground" />
          <h2 className="font-display text-sm font-medium">{column.label}</h2>
        </div>
        <span
          data-testid={`flow-col-count-${column.key}`}
          className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
        >
          {rows.length}
        </span>
      </header>
      <div className="flex-1 space-y-2 p-2">
        {rows.length === 0 ? (
          <div
            data-testid={`flow-col-empty-${column.key}`}
            className="rounded-sm border border-dashed border-border/70 px-3 py-6 text-center text-xs text-muted-foreground"
          >
            No patients
          </div>
        ) : (
          rows.map((a) => (
            <FlowRow
              key={a.id}
              appointment={a}
              columnKey={column.key}
              providerName={providerName}
              typeById={typeById}
              rooms={rooms}
              occupantByRoomId={occupantByRoomId}
              onAction={onAction}
              onUpdated={onUpdated}
            />
          ))
        )}
      </div>
    </section>
  );
}

function FlowRow({ appointment, columnKey, providerName, typeById, rooms, occupantByRoomId, onAction, onUpdated }) {
  const elapsed = humanDuration(stageStart(appointment, columnKey));
  const typeName =
    appointment.appointment_type_id && typeById.get(appointment.appointment_type_id)?.name;
  const apptTime = appointment.start_time
    ? new Date(appointment.start_time).toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
      })
    : null;
  const intakeStatus = appointment.intake_status || "not_started";
  const intakeLabel =
    intakeStatus === "not_started"
      ? "Intake not started"
      : intakeStatus === "in_progress"
        ? "Intake in progress"
        : "Intake completed";

  return (
    <article
      data-testid={`flow-row-${appointment.id}`}
      className="space-y-1.5 rounded-sm border border-border bg-background p-2.5"
    >
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p
            data-testid={`flow-row-name-${appointment.id}`}
            className="truncate text-sm font-medium"
          >
            {appointment.patient_name || "Unknown patient"}
          </p>
          <p className="truncate text-[11px] text-muted-foreground">
            {apptTime}
            {appointment.provider_id ? ` · ${providerName.get(appointment.provider_id) || appointment.provider_name || ""}` : ""}
            {typeName ? ` · ${typeName}` : ""}
          </p>
        </div>
        {elapsed && (
          <span
            data-testid={`flow-row-elapsed-${appointment.id}`}
            className="shrink-0 rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[10px] text-foreground"
            title={`In "${columnKey.replaceAll("_", " ")}" for ${elapsed}`}
          >
            {elapsed}
          </span>
        )}
      </header>

      <div className="flex flex-wrap gap-1.5 text-[10px]">
        <Badge
          data-testid={`flow-row-status-${appointment.id}`}
          variant="outline"
          className="rounded-sm"
        >
          {String(appointment.status || "").replaceAll("_", " ")}
        </Badge>
        <Badge
          data-testid={`flow-row-intake-${appointment.id}`}
          variant="outline"
          className={`rounded-sm ${INTAKE_TONE[intakeStatus] || ""}`}
        >
          <FileText className="mr-1 h-2.5 w-2.5" />
          {intakeLabel}
        </Badge>
        {appointment.current_location_type && (
          <Badge
            data-testid={`flow-row-location-${appointment.id}`}
            variant="outline"
            className="rounded-sm"
          >
            <Home className="mr-1 h-2.5 w-2.5" />
            {appointment.current_location_type.replaceAll("_", " ")}
          </Badge>
        )}
      </div>

      <FlowRowActions
        appointment={appointment}
        columnKey={columnKey}
        onAction={onAction}
      />

      {["waiting_room", "roomed", "ready_for_provider", "in_progress"].includes(columnKey) && (
        <RoomAssignmentControl
          appointment={appointment}
          rooms={rooms}
          occupantByRoomId={occupantByRoomId}
          onUpdated={onUpdated}
          compact
        />
      )}
    </article>
  );
}

function FlowRowActions({ appointment, columnKey, onAction }) {
  const intakeBlocked = (appointment.intake_status || "not_started") !== "completed";
  const onReadyForProvider = async () => {
    if (!intakeBlocked) {
      onAction(appointment, "ready-for-provider");
      return;
    }
    const reason = window.prompt(
      "Intake is not complete. Type a reason to override and mark ready for provider:",
    );
    if (!reason || !reason.trim()) return;
    onAction(appointment, "ready-for-provider", { override: true, reason: reason.trim() });
  };

  const btn = (testid, label, onClick, tone = "primary") => (
    <Button
      key={testid}
      type="button"
      size="sm"
      variant={tone === "ghost" ? "ghost" : tone === "outline" ? "outline" : "default"}
      data-testid={testid}
      onClick={onClick}
      className={`h-7 rounded-sm px-2 text-[11px] ${tone === "danger" ? "text-destructive hover:bg-destructive-soft" : ""}`}
    >
      {label}
    </Button>
  );

  const actions = [];
  switch (columnKey) {
    case "waiting_room":
      actions.push(
        btn(`flow-action-room-${appointment.id}`, (
          <><DoorOpen className="mr-1 h-3 w-3" /> Room</>
        ), () => onAction(appointment, "location", { location: "roomed" }), "outline"),
        btn(`flow-action-ready-${appointment.id}`, (
          <><Stethoscope className="mr-1 h-3 w-3" /> Ready</>
        ), onReadyForProvider),
        btn(`flow-action-undo-${appointment.id}`, (
          <><RotateCcw className="mr-1 h-3 w-3" /> Undo</>
        ), () => onAction(appointment, "undo-check-in"), "ghost"),
      );
      break;
    case "roomed":
      actions.push(
        btn(`flow-action-ready-${appointment.id}`, (
          <><Stethoscope className="mr-1 h-3 w-3" /> Ready</>
        ), onReadyForProvider),
        btn(`flow-action-undo-${appointment.id}`, (
          <><RotateCcw className="mr-1 h-3 w-3" /> Undo</>
        ), () => onAction(appointment, "undo-check-in"), "ghost"),
      );
      break;
    case "ready_for_provider":
      actions.push(
        btn(`flow-action-start-${appointment.id}`, (
          <><Clock className="mr-1 h-3 w-3" /> Start visit</>
        ), () => onAction(appointment, "start-visit")),
        btn(`flow-action-undo-${appointment.id}`, (
          <><RotateCcw className="mr-1 h-3 w-3" /> Undo</>
        ), () => onAction(appointment, "undo-check-in"), "ghost"),
      );
      break;
    case "in_progress":
      actions.push(
        btn(`flow-action-rfco-${appointment.id}`, (
          <><CheckCircle2 className="mr-1 h-3 w-3" /> Ready checkout</>
        ), () => onAction(appointment, "ready-for-checkout")),
        btn(`flow-action-complete-${appointment.id}`, (
          <><CheckCircle2 className="mr-1 h-3 w-3" /> Complete</>
        ), () => onAction(appointment, "complete"), "outline"),
      );
      break;
    case "ready_for_checkout":
      actions.push(
        btn(`flow-action-complete-${appointment.id}`, (
          <><CheckCircle2 className="mr-1 h-3 w-3" /> Complete</>
        ), () => onAction(appointment, "complete")),
        btn(`flow-action-checkout-${appointment.id}`, (
          <><LogOut className="mr-1 h-3 w-3" /> Checkout</>
        ), () => onAction(appointment, "checkout", { override: true, reason: "board quick-checkout" }), "outline"),
      );
      break;
    case "recently_checked_out":
      actions.push(
        btn(`flow-action-depart-${appointment.id}`, (
          <><LogOut className="mr-1 h-3 w-3" /> Depart</>
        ), () => onAction(appointment, "depart"), "outline"),
      );
      break;
    default:
      break;
  }

  // Cross-state no-show for pre-visit stages.
  if (["waiting_room", "roomed", "ready_for_provider"].includes(columnKey)) {
    actions.push(
      btn(`flow-action-noshow-${appointment.id}`, (
        <><XCircle className="mr-1 h-3 w-3" /> No-show</>
      ), () => {
        if (!window.confirm("Mark this appointment as no-show?")) return;
        onAction(appointment, "no-show");
      }, "danger"),
    );
  }

  if (!actions.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5 pt-1">
      {actions}
    </div>
  );
}

// Silence eslint for the unused import retained for symmetry with panel.
// eslint-disable-next-line no-unused-vars
const _keepIconRef = UserCheck;
