import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { CalendarDays, Plus, X, RefreshCcw } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import { formatDateTime, isoToLocalInput, localInputToIso, relativeFromNow } from "../utils/time";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../components/ui/alert-dialog";
import { Label } from "../components/ui/label";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";

const STAFF = ["admin", "doctor", "staff"];

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

function BookDialog({ open, onClose, onBooked, initial }) {
  const [patients, setPatients] = useState([]);
  const [providers, setProviders] = useState([]);
  const [form, setForm] = useState({
    patient_id: "",
    provider_id: "",
    start_time: "",
    end_time: "",
    reason: "",
    notes: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const mode = initial ? "reschedule" : "create";

  useEffect(() => {
    if (!open) return;
    (async () => {
      try {
        const [ps, pr] = await Promise.all([
          api.get("/patients"),
          api.get("/auth/providers"),
        ]);
        setPatients(ps.data);
        setProviders(pr.data);
      } catch {
        /* ignore */
      }
    })();
    if (initial) {
      setForm({
        patient_id: initial.patient_id,
        provider_id: initial.provider_id,
        start_time: isoToLocalInput(initial.start_time),
        end_time: isoToLocalInput(initial.end_time),
        reason: initial.reason || "",
        notes: initial.notes || "",
      });
    } else {
      const now = new Date();
      now.setMinutes(now.getMinutes() - now.getMinutes() % 15 + 30, 0, 0);
      const later = new Date(now.getTime() + 30 * 60000);
      setForm({
        patient_id: "",
        provider_id: "",
        start_time: isoToLocalInput(now.toISOString()),
        end_time: isoToLocalInput(later.toISOString()),
        reason: "",
        notes: "",
      });
    }
  }, [open, initial]);

  const update = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const payload = {
        patient_id: form.patient_id,
        provider_id: form.provider_id,
        start_time: localInputToIso(form.start_time),
        end_time: localInputToIso(form.end_time),
        reason: form.reason || null,
        notes: form.notes || null,
      };
      let data;
      if (mode === "reschedule") {
        const res = await api.put(`/appointments/${initial.id}`, {
          start_time: payload.start_time,
          end_time: payload.end_time,
          reason: payload.reason,
          notes: payload.notes,
        });
        data = res.data;
        toast.success("Appointment rescheduled");
      } else {
        const res = await api.post("/appointments", payload);
        data = res.data;
        toast.success("Appointment booked — reminder queued");
      }
      onBooked(data);
      onClose();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to save appointment");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="appt-dialog" className="max-w-lg rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">
            {mode === "reschedule" ? "Reschedule appointment" : "Book appointment"}
          </DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1">
            <Label>Patient</Label>
            <Select
              value={form.patient_id}
              onValueChange={update("patient_id")}
              disabled={mode === "reschedule"}
            >
              <SelectTrigger data-testid="appt-patient-select" className="rounded-sm">
                <SelectValue placeholder="Select patient" />
              </SelectTrigger>
              <SelectContent>
                {patients.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.first_name} {p.last_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Provider</Label>
            <Select
              value={form.provider_id}
              onValueChange={update("provider_id")}
              disabled={mode === "reschedule"}
            >
              <SelectTrigger data-testid="appt-provider-select" className="rounded-sm">
                <SelectValue placeholder="Select provider" />
              </SelectTrigger>
              <SelectContent>
                {providers.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <Label>Start</Label>
              <Input
                type="datetime-local"
                required
                data-testid="appt-start"
                value={form.start_time}
                onChange={(e) => update("start_time")(e.target.value)}
                className="rounded-sm"
              />
            </div>
            <div className="space-y-1">
              <Label>End</Label>
              <Input
                type="datetime-local"
                required
                data-testid="appt-end"
                value={form.end_time}
                onChange={(e) => update("end_time")(e.target.value)}
                className="rounded-sm"
              />
            </div>
          </div>
          <div className="space-y-1">
            <Label>Reason</Label>
            <Input
              data-testid="appt-reason"
              value={form.reason}
              onChange={(e) => update("reason")(e.target.value)}
              placeholder="Initial consultation, follow-up, adjustment…"
              className="rounded-sm"
            />
          </div>
          <div className="space-y-1">
            <Label>Notes</Label>
            <Textarea
              data-testid="appt-notes"
              value={form.notes}
              onChange={(e) => update("notes")(e.target.value)}
              rows={2}
              className="rounded-sm"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} className="rounded-sm">
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={submitting || !form.patient_id || !form.provider_id}
              data-testid="appt-submit-btn"
              className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            >
              {submitting ? "Saving…" : mode === "reschedule" ? "Save changes" : "Book"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function Appointments() {
  const { user } = useAuth();
  const canBook = STAFF.includes(user.role);
  const [appts, setAppts] = useState(null);
  const [filter, setFilter] = useState("upcoming");
  const [dialog, setDialog] = useState({ open: false, initial: null });
  const [confirmCancel, setConfirmCancel] = useState(null);

  async function refresh() {
    try {
      const { data } = await api.get("/appointments");
      setAppts(data);
    } catch {
      setAppts([]);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const filtered = useMemo(() => {
    if (!appts) return null;
    if (filter === "all") return appts;
    if (filter === "cancelled") return appts.filter((a) => a.status === "cancelled");
    if (filter === "past")
      return appts.filter((a) => new Date(a.end_time) < new Date() && a.status !== "cancelled");
    // upcoming
    return appts.filter(
      (a) => new Date(a.start_time) >= new Date() && a.status !== "cancelled"
    );
  }, [appts, filter]);

  async function doCancel(a) {
    try {
      const { data } = await api.post(`/appointments/${a.id}/cancel`);
      setAppts((xs) => xs.map((x) => (x.id === a.id ? data : x)));
      toast.success("Appointment cancelled — notifications queued");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to cancel");
    } finally {
      setConfirmCancel(null);
    }
  }

  return (
    <div data-testid="appointments-page" className="space-y-8 animate-in fade-in duration-300">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Scheduling
          </span>
          <h1 className="mt-2 font-display text-4xl font-medium tracking-tight">
            Appointments
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Every booking, reschedule, and cancellation publishes an event to the
            communication service.
          </p>
        </div>
        {canBook && (
          <Button
            data-testid="appt-new-btn"
            onClick={() => setDialog({ open: true, initial: null })}
            className="h-11 rounded-sm bg-primary px-5 hover:bg-[var(--primary-hover)]"
          >
            <Plus className="mr-2 h-4 w-4" /> New appointment
          </Button>
        )}
      </header>

      <div className="flex flex-wrap gap-2">
        {[
          { v: "upcoming", l: "Upcoming" },
          { v: "past", l: "Past" },
          { v: "cancelled", l: "Cancelled" },
          { v: "all", l: "All" },
        ].map((f) => (
          <button
            key={f.v}
            data-testid={`appt-filter-${f.v}`}
            onClick={() => setFilter(f.v)}
            className={`rounded-sm border px-4 py-1.5 text-sm font-medium transition-colors ${
              filter === f.v
                ? "border-primary bg-primary/10 text-primary"
                : "border-border bg-card text-muted-foreground hover:bg-muted"
            }`}
          >
            {f.l}
          </button>
        ))}
      </div>

      {filtered === null ? (
        <Skeleton className="h-32" />
      ) : filtered.length === 0 ? (
        <div className="rounded-sm border border-dashed border-border bg-card p-16 text-center">
          <CalendarDays className="mx-auto h-10 w-10 text-muted-foreground/70" />
          <p className="mt-4 font-display text-lg">
            No {filter} appointments
          </p>
          {canBook && filter === "upcoming" && (
            <Button
              onClick={() => setDialog({ open: true, initial: null })}
              className="mt-4 rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            >
              Book an appointment
            </Button>
          )}
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
              {filtered.map((a) => (
                <tr
                  key={a.id}
                  data-testid={`appt-row-${a.id}`}
                  className="border-b border-border last:border-b-0 hover:bg-muted/50"
                >
                  <td className="px-6 py-4 text-sm">
                    <div className="font-medium">{formatDateTime(a.start_time)}</div>
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
                            onClick={() => setDialog({ open: true, initial: a })}
                            data-testid={`appt-reschedule-${a.id}`}
                            className="rounded-sm text-primary hover:bg-primary/10"
                          >
                            <RefreshCcw className="mr-1 h-3 w-3" /> Reschedule
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setConfirmCancel(a)}
                          data-testid={`appt-cancel-${a.id}`}
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

      <BookDialog
        open={dialog.open}
        initial={dialog.initial}
        onClose={() => setDialog({ open: false, initial: null })}
        onBooked={(a) => {
          setAppts((xs) => {
            if (!xs) return [a];
            const found = xs.find((x) => x.id === a.id);
            return found ? xs.map((x) => (x.id === a.id ? a : x)) : [a, ...xs];
          });
        }}
      />

      <AlertDialog open={!!confirmCancel} onOpenChange={(v) => !v && setConfirmCancel(null)}>
        <AlertDialogContent data-testid="appt-cancel-confirm" className="rounded-sm">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Cancel appointment?</AlertDialogTitle>
            <AlertDialogDescription>
              This will publish an <code className="text-primary">appointment.cancelled</code>{" "}
              event and queue a mock notification. The slot will open up
              immediately.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-sm">Keep it</AlertDialogCancel>
            <AlertDialogAction
              data-testid="appt-cancel-confirm-btn"
              onClick={() => confirmCancel && doCancel(confirmCancel)}
              className="rounded-sm bg-destructive hover:brightness-95"
            >
              Cancel appointment
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
