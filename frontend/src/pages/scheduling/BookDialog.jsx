import { useEffect, useMemo, useRef, useState } from "react";
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
import { useAppointmentTypes } from "./useAppointmentTypes";

const CUSTOM_TYPE_VALUE = "__custom__";
const DEFAULT_DURATION_MIN = 30;

/**
 * Create / reschedule appointment dialog.
 *
 * Props:
 *   - open: boolean
 *   - onClose(): void
 *   - onSaved(appt): void   — called with the new/updated appointment
 *   - initial: appointment | null  — when provided, acts as "reschedule"
 *   - defaultStart: Date | null   — pre-fill start/end in "create" mode
 *
 * Appointment-type behavior (create mode):
 *   - Active appointment types populate a dropdown above Reason.
 *   - Selecting a type sets Reason to the type name and recomputes End
 *     to Start + type.default_duration_minutes.
 *   - Subsequent Start edits keep recomputing End until the user
 *     manually edits End — at which point the manual override is
 *     preserved (we stop fighting the user).
 *   - "Custom" keeps Reason free-text with the legacy 30-min duration.
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
  const [selectedTypeId, setSelectedTypeId] = useState(CUSTOM_TYPE_VALUE);
  const endManuallyEditedRef = useRef(false);
  const [submitting, setSubmitting] = useState(false);

  // Only fetch types while the dialog is open to avoid background chatter.
  const { types } = useAppointmentTypes({ activeOnly: true, enabled: open });
  const typeById = useMemo(() => {
    const m = new Map();
    for (const t of types) m.set(t.id, t);
    return m;
  }, [types]);

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

    // Reset type selection + manual-edit flag each time the dialog opens.
    setSelectedTypeId(CUSTOM_TYPE_VALUE);
    endManuallyEditedRef.current = false;

    if (initial) {
      setForm({
        patient_id: initial.patient_id,
        provider_id: initial.provider_id,
        start_time: isoToLocalInput(initial.start_time),
        end_time: isoToLocalInput(initial.end_time),
        reason: initial.reason || "",
        notes: initial.notes || "",
      });
      // Reschedule mode respects the saved end time — treat as manual.
      endManuallyEditedRef.current = true;
      return;
    }

    const base = defaultStart ? new Date(defaultStart) : new Date();
    if (!defaultStart) {
      base.setMinutes(base.getMinutes() - (base.getMinutes() % 15) + 30, 0, 0);
    }
    const later = new Date(base.getTime() + DEFAULT_DURATION_MIN * 60000);
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

  function addMinutesToLocalInput(localInput, minutes) {
    if (!localInput) return localInput;
    // Convert "YYYY-MM-DDTHH:MM" (browser local input) → Date → +mins → back.
    const d = new Date(localInput);
    if (Number.isNaN(d.getTime())) return localInput;
    const next = new Date(d.getTime() + minutes * 60000);
    // Format back to local input (truncate seconds).
    const pad = (n) => String(n).padStart(2, "0");
    return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}T${pad(next.getHours())}:${pad(next.getMinutes())}`;
  }

  function onTypeChange(typeId) {
    setSelectedTypeId(typeId);
    if (typeId === CUSTOM_TYPE_VALUE) return;
    const t = typeById.get(typeId);
    if (!t) return;
    // Auto-fill reason with the type name. Recompute end from the current
    // start + default duration. Reset manual-override since the user just
    // asked for an opinionated default.
    endManuallyEditedRef.current = false;
    setForm((f) => ({
      ...f,
      reason: t.name,
      end_time: addMinutesToLocalInput(f.start_time, t.default_duration_minutes),
    }));
  }

  function onStartChange(value) {
    setForm((f) => {
      const next = { ...f, start_time: value };
      // If a type is selected AND user hasn't hand-edited End, recompute.
      const t = typeById.get(selectedTypeId);
      if (t && !endManuallyEditedRef.current) {
        next.end_time = addMinutesToLocalInput(value, t.default_duration_minutes);
      }
      return next;
    });
  }

  function onEndChange(value) {
    endManuallyEditedRef.current = true;
    update("end_time")(value);
  }

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

          {mode === "create" && (
            <div className="space-y-1">
              <Label>Appointment type</Label>
              <Select value={selectedTypeId} onValueChange={onTypeChange}>
                <SelectTrigger
                  data-testid="appt-type-select"
                  className="rounded-sm"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem
                    value={CUSTOM_TYPE_VALUE}
                    data-testid="appt-type-option-custom"
                  >
                    Custom (free text)
                  </SelectItem>
                  {types.map((t) => (
                    <SelectItem
                      key={t.id}
                      value={t.id}
                      data-testid={`appt-type-option-${t.id}`}
                    >
                      {t.name} · {t.default_duration_minutes} min
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {types.length === 0 && (
                <p
                  data-testid="appt-type-empty-hint"
                  className="text-[11px] text-muted-foreground"
                >
                  No appointment types configured yet — ask an admin to add
                  some in Clinic settings, or keep typing a custom reason
                  below.
                </p>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <Label>Start</Label>
              <Input
                type="datetime-local"
                required
                data-testid="appt-start"
                value={form.start_time}
                onChange={(e) => onStartChange(e.target.value)}
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
                onChange={(e) => onEndChange(e.target.value)}
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
