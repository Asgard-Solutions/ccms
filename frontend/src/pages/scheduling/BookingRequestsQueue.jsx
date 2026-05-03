/**
 * Staff booking-requests queue.
 *
 * Shows pending requests, approve/decline actions, and a history view
 * when status_filter is toggled.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  CalendarClock, CheckCircle2, CircleSlash, Clock, Inbox, XCircle,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { Badge } from "../../components/ui/badge";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Skeleton } from "../../components/ui/skeleton";
import {
  staffApproveBooking, staffDeclineBooking, staffListBookingRequests,
} from "../../api/portal";
import { formatDateTime } from "../../utils/time";

function StatusBadge({ status }) {
  const map = {
    pending: { label: "Pending", icon: Clock, cls: "bg-amber-100 text-amber-800" },
    approved: { label: "Approved", icon: CheckCircle2, cls: "bg-green-100 text-green-800" },
    declined: { label: "Declined", icon: XCircle, cls: "bg-red-100 text-red-800" },
    cancelled: { label: "Cancelled", icon: CircleSlash, cls: "bg-slate-100 text-slate-700" },
  };
  const conf = map[status] || map.pending;
  const Icon = conf.icon;
  return (
    <span className={`inline-flex items-center gap-1 rounded-sm px-2 py-0.5 text-xs font-medium ${conf.cls}`}>
      <Icon className="h-3 w-3" />
      {conf.label}
    </span>
  );
}

function ApproveDialog({ row, onClose, onDone }) {
  const first = row?.preferred_slots?.[0]?.start_time || "";
  const pad = (n) => String(n).padStart(2, "0");
  // Convert ISO to datetime-local
  const toLocal = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    return (
      d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate())
      + "T" + pad(d.getHours()) + ":" + pad(d.getMinutes())
    );
  };
  const [form, setForm] = useState({
    start_time: toLocal(first),
    duration_minutes: 30,
    note_to_patient: "",
  });
  const [saving, setSaving] = useState(false);

  async function approve() {
    setSaving(true);
    try {
      await staffApproveBooking(row.id, {
        start_time: new Date(form.start_time).toISOString(),
        duration_minutes: Number(form.duration_minutes) || 30,
        note_to_patient: form.note_to_patient.trim() || null,
      });
      toast.success("Approved — appointment booked and patient notified.");
      onDone();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Approve failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      data-testid="booking-approve-dialog"
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-6"
    >
      <div className="w-full max-w-md rounded-md border border-border bg-card p-6 space-y-3">
        <h3 className="text-lg font-medium">Approve & schedule</h3>
        <p className="text-sm text-muted-foreground">
          {row.reason ? <span>Reason: {row.reason}</span> : "Approve this request."}
        </p>
        <div>
          <Label>Start time</Label>
          <Input
            type="datetime-local"
            value={form.start_time}
            onChange={(e) => setForm((f) => ({ ...f, start_time: e.target.value }))}
            data-testid="booking-approve-start"
            className="mt-1.5"
          />
        </div>
        <div>
          <Label>Duration (min)</Label>
          <Input
            type="number"
            min={5} max={480} step={5}
            value={form.duration_minutes}
            onChange={(e) => setForm((f) => ({ ...f, duration_minutes: e.target.value }))}
            data-testid="booking-approve-duration"
            className="mt-1.5"
          />
        </div>
        <div>
          <Label>Note to patient (optional)</Label>
          <Input
            value={form.note_to_patient}
            onChange={(e) => setForm((f) => ({ ...f, note_to_patient: e.target.value }))}
            data-testid="booking-approve-note"
            className="mt-1.5"
          />
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose} data-testid="booking-approve-cancel">
            Cancel
          </Button>
          <Button
            disabled={saving || !form.start_time}
            onClick={approve}
            data-testid="booking-approve-confirm"
          >
            {saving ? "Booking…" : "Book appointment"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function BookingRequestsQueue() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("pending");
  const [approving, setApproving] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = filter ? { status_filter: filter } : {};
      setRows(await staffListBookingRequests(params));
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  async function decline(row) {
    const reason = window.prompt("Reason for declining (sent to patient)?");
    if (!reason) return;
    try {
      await staffDeclineBooking(row.id, reason);
      toast.success("Request declined.");
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Decline failed");
    }
  }

  return (
    <div data-testid="booking-requests-queue" className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-display tracking-tight">
            <Inbox className="inline mr-2 h-5 w-5 align-[-2px]" />
            Booking requests
          </h1>
          <p className="text-sm text-muted-foreground">
            Patient-submitted appointment requests awaiting confirmation.
          </p>
        </div>
        <div className="flex items-center gap-1 text-sm">
          {["pending", "approved", "declined", ""].map((s) => (
            <Button
              key={s || "all"}
              variant={filter === s ? "default" : "ghost"}
              size="sm"
              className="h-8 rounded-sm"
              onClick={() => setFilter(s)}
              data-testid={`booking-filter-${s || "all"}`}
            >
              {s || "All"}
            </Button>
          ))}
        </div>
      </header>

      {loading ? (
        <Skeleton className="h-40 w-full" />
      ) : rows.length === 0 ? (
        <div className="rounded-md border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
          <CalendarClock className="mx-auto mb-2 h-6 w-6" />
          No requests with status "{filter || "any"}".
        </div>
      ) : (
        <ul className="space-y-2">
          {rows.map((r) => (
            <li
              key={r.id}
              data-testid={`booking-row-${r.id}`}
              className="rounded-md border border-border bg-card p-4"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <StatusBadge status={r.status} />
                    <span className="text-xs text-muted-foreground">
                      Requested {formatDateTime(r.created_at)}
                    </span>
                  </div>
                  <p className="font-medium">{r.reason || "Appointment request"}</p>
                  {r.patient_notes && (
                    <p className="text-sm text-muted-foreground mt-0.5">
                      "{r.patient_notes}"
                    </p>
                  )}
                  {r.preferred_slots?.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {r.preferred_slots.map((s, i) => (
                        <Badge key={i} variant="outline">
                          {formatDateTime(s.start_time)}
                        </Badge>
                      ))}
                    </div>
                  )}
                  {r.decision_reason && (
                    <p className="text-xs text-muted-foreground mt-2">
                      Decision note: {r.decision_reason}
                    </p>
                  )}
                </div>
                {r.status === "pending" && (
                  <div className="flex gap-2 shrink-0">
                    <Button
                      size="sm"
                      onClick={() => setApproving(r)}
                      data-testid={`booking-approve-btn-${r.id}`}
                    >
                      Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => decline(r)}
                      data-testid={`booking-decline-btn-${r.id}`}
                    >
                      Decline
                    </Button>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {approving && (
        <ApproveDialog
          row={approving}
          onClose={() => setApproving(null)}
          onDone={() => { setApproving(null); load(); }}
        />
      )}
    </div>
  );
}
