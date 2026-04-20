import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "../../api/client";
import { isoToLocalInput, localInputToIso } from "../../utils/time";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Label } from "../../components/ui/label";
import { Input } from "../../components/ui/input";
import { Textarea } from "../../components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";

/**
 * Create / reschedule appointment dialog.
 *
 * Props:
 *   - open: boolean
 *   - onClose(): void
 *   - onSaved(appt): void   — called with the new/updated appointment
 *   - initial: appointment | null  — when provided, acts as "reschedule"
 *   - defaultStart: Date | null   — pre-fill start/end in "create" mode
 */
export default function BookDialog({ open, onClose, onSaved, onCancelAppointment, initial = null, defaultStart = null }) {
  const mode = initial ? "reschedule" : "create";
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
        /* ignore; UI will just show empty selects */
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
      return;
    }

    const base = defaultStart ? new Date(defaultStart) : new Date();
    if (!defaultStart) {
      base.setMinutes(base.getMinutes() - (base.getMinutes() % 15) + 30, 0, 0);
    }
    const later = new Date(base.getTime() + 30 * 60000);
    setForm({
      patient_id: "",
      provider_id: "",
      start_time: isoToLocalInput(base.toISOString()),
      end_time: isoToLocalInput(later.toISOString()),
      reason: "",
      notes: "",
    });
  }, [open, initial, defaultStart]);

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
      let saved;
      if (mode === "reschedule") {
        const res = await api.put(`/appointments/${initial.id}`, {
          start_time: payload.start_time,
          end_time: payload.end_time,
          reason: payload.reason,
          notes: payload.notes,
        });
        saved = res.data;
        toast.success("Appointment rescheduled");
      } else {
        const res = await api.post("/appointments", payload);
        saved = res.data;
        toast.success("Appointment booked — reminder queued");
      }
      onSaved?.(saved);
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
          <DialogFooter className="flex-wrap gap-2 sm:justify-between">
            {mode === "reschedule" && initial?.status === "scheduled" && onCancelAppointment ? (
              <Button
                type="button"
                variant="ghost"
                data-testid="appt-cancel-appointment-btn"
                onClick={() => onCancelAppointment(initial)}
                className="rounded-sm text-destructive hover:bg-destructive-soft"
              >
                Cancel appointment
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={onClose} className="rounded-sm">
                Close
              </Button>
              <Button
                type="submit"
                disabled={submitting || !form.patient_id || !form.provider_id}
                data-testid="appt-submit-btn"
                className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
              >
                {submitting ? "Saving…" : mode === "reschedule" ? "Save changes" : "Book"}
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
