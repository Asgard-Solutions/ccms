/**
 * Patient-portal booking-request form.
 * Patient picks a provider + appointment type + up to 3 preferred slots
 * and sends a request. Front-desk approves to materialise the real
 * appointment row.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { CalendarPlus, Trash2 } from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import {
  createBookingRequest,
  fetchPortalAppointmentTypes,
  fetchPortalProviders,
} from "../api/portal";

function datetimeLocalNow(offsetDays = 1) {
  const d = new Date(Date.now() + offsetDays * 24 * 60 * 60 * 1000);
  d.setMinutes(0, 0, 0);
  // YYYY-MM-DDTHH:mm (HTML datetime-local input format, no timezone)
  const pad = (n) => String(n).padStart(2, "0");
  return (
    d.getFullYear() +
    "-" + pad(d.getMonth() + 1) +
    "-" + pad(d.getDate()) +
    "T" + pad(d.getHours()) + ":" + pad(d.getMinutes())
  );
}

export default function PortalBook() {
  const navigate = useNavigate();
  const [providers, setProviders] = useState([]);
  const [types, setTypes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    provider_id: "",
    appointment_type_id: "",
    reason: "",
    patient_notes: "",
  });
  const [slots, setSlots] = useState([datetimeLocalNow(1)]);

  const load = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([
        fetchPortalProviders(),
        fetchPortalAppointmentTypes(),
      ]);
      setProviders(p);
      setTypes(t);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load options");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const canSubmit = useMemo(() => {
    return (
      slots.filter((s) => s && s.length >= 10).length >= 1 &&
      form.reason.trim().length >= 3
    );
  }, [slots, form.reason]);

  async function submit(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const body = {
        provider_id: form.provider_id || null,
        appointment_type_id: form.appointment_type_id || null,
        reason: form.reason.trim(),
        patient_notes: form.patient_notes.trim() || null,
        preferred_slots: slots
          .filter(Boolean)
          .map((s) => ({ start_time: new Date(s).toISOString() })),
      };
      await createBookingRequest(body);
      toast.success("Request sent. The front desk will confirm shortly.");
      navigate("/portal", { replace: true });
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to send request");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div data-testid="portal-book-page" className="max-w-xl space-y-6">
      <header>
        <h1 className="text-2xl font-display tracking-tight">
          Request an appointment
        </h1>
        <p className="text-sm text-muted-foreground">
          Pick one or more preferred times. We'll confirm the best one by text.
        </p>
      </header>
      <form onSubmit={submit} className="space-y-4" data-testid="portal-book-form">
        <div>
          <Label htmlFor="provider">Preferred provider</Label>
          <select
            id="provider"
            data-testid="portal-book-provider-select"
            className="mt-1.5 w-full rounded-sm border border-input bg-background px-3 py-2 text-sm"
            value={form.provider_id}
            onChange={(e) => setForm((f) => ({ ...f, provider_id: e.target.value }))}
          >
            <option value="">No preference</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <Label htmlFor="type">Visit type</Label>
          <select
            id="type"
            data-testid="portal-book-type-select"
            className="mt-1.5 w-full rounded-sm border border-input bg-background px-3 py-2 text-sm"
            value={form.appointment_type_id}
            onChange={(e) =>
              setForm((f) => ({ ...f, appointment_type_id: e.target.value }))
            }
          >
            <option value="">No preference</option>
            {types.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
                {t.duration_minutes ? ` (${t.duration_minutes} min)` : ""}
              </option>
            ))}
          </select>
        </div>

        <div>
          <Label htmlFor="reason">Reason for visit</Label>
          <Input
            id="reason"
            data-testid="portal-book-reason-input"
            value={form.reason}
            onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
            placeholder="e.g. Low back pain flare-up"
            className="mt-1.5"
            maxLength={500}
            required
          />
        </div>

        <div>
          <Label>Preferred times</Label>
          <div className="space-y-2 mt-1.5" data-testid="portal-book-slots">
            {slots.map((s, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  type="datetime-local"
                  value={s}
                  onChange={(e) => {
                    const v = e.target.value;
                    setSlots((arr) => {
                      const next = [...arr];
                      next[i] = v;
                      return next;
                    });
                  }}
                  data-testid={`portal-book-slot-${i}`}
                />
                {slots.length > 1 && (
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    onClick={() => setSlots((arr) => arr.filter((_, j) => j !== i))}
                    data-testid={`portal-book-remove-slot-${i}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            ))}
            {slots.length < 3 && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setSlots((arr) => [...arr, datetimeLocalNow(arr.length + 1)])}
                data-testid="portal-book-add-slot-btn"
              >
                <CalendarPlus className="mr-1 h-3.5 w-3.5" />
                Add another preferred time
              </Button>
            )}
          </div>
        </div>

        <div>
          <Label htmlFor="notes">Notes for the front desk (optional)</Label>
          <Textarea
            id="notes"
            data-testid="portal-book-notes-input"
            value={form.patient_notes}
            onChange={(e) =>
              setForm((f) => ({ ...f, patient_notes: e.target.value }))
            }
            className="mt-1.5"
            maxLength={1000}
            rows={3}
          />
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button
            type="button"
            variant="ghost"
            onClick={() => navigate("/portal")}
            data-testid="portal-book-cancel-btn"
          >
            Cancel
          </Button>
          <Button
            type="submit"
            disabled={!canSubmit || saving}
            data-testid="portal-book-submit-btn"
          >
            {saving ? "Sending…" : "Send request"}
          </Button>
        </div>
      </form>
    </div>
  );
}
