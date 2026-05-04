/**
 * Natural-language quick-book widget.
 *
 * Lives on the Scheduling page above the toolbar. Lets staff/doctors
 * type plain-English requests like "Book Hannah Whitaker for an
 * adjustment with Dr. Park next Tuesday at 2pm" and have Claude turn
 * them into a structured appointment intent.
 *
 * Two-phase flow:
 *   1. Click parse → hits POST /api/scheduling/nl/parse → returns
 *      structured intent + clarifications. Patient/provider IDs that
 *      can be uniquely resolved are pre-filled; ambiguous ones surface
 *      candidate selects.
 *   2. Click "Create appointment" → POST /api/scheduling/nl/create
 *      with the resolved IDs. Re-uses the canonical create-appointment
 *      flow so all event-bus hooks fire.
 *
 * Never auto-creates without an explicit confirm click — the LLM is a
 * suggestion engine, not the source of truth.
 */
import { useState } from "react";
import { toast } from "sonner";
import { Loader2, Sparkles, CalendarPlus, AlertTriangle } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../../components/ui/select";
import { nlSchedulingParse, nlSchedulingCreate } from "../../api/ai";

function formatLocalDateTimeForInput(iso) {
  if (!iso) return "";
  // datetime-local needs YYYY-MM-DDTHH:MM in local time (no offset).
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
      + `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch { return ""; }
}

export default function NLBookCard({ onBooked }) {
  const [text, setText] = useState("");
  const [parsing, setParsing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [parsed, setParsed] = useState(null);
  // User-confirmed values (start as the model's resolved IDs):
  const [patientId, setPatientId] = useState("");
  const [providerId, setProviderId] = useState("");
  const [apptTypeId, setApptTypeId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [startLocal, setStartLocal] = useState("");
  const [duration, setDuration] = useState(30);

  function reset(seed = parsed) {
    setPatientId(seed?.patient?.id || "");
    setProviderId(seed?.provider?.id || "");
    setApptTypeId(seed?.appointment_type?.id || "");
    setLocationId(seed?.location?.id || "");
    setStartLocal(formatLocalDateTimeForInput(seed?.start_iso));
    setDuration(seed?.duration_minutes || 30);
  }

  async function parse() {
    if (text.trim().length < 2) return;
    setParsing(true);
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const res = await nlSchedulingParse({ text: text.trim(), timezone: tz });
      setParsed(res);
      reset(res);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Couldn't parse — try rephrasing.");
    } finally {
      setParsing(false);
    }
  }

  async function confirm() {
    if (!patientId || !providerId || !startLocal) {
      toast.error("Pick patient, provider, and a start time first.");
      return;
    }
    setCreating(true);
    try {
      const start = new Date(startLocal).toISOString();
      const appt = await nlSchedulingCreate({
        patient_id: patientId,
        provider_id: providerId,
        start_iso: start,
        duration_minutes: duration,
        location_id: locationId || null,
        appointment_type_id: apptTypeId || null,
        reason: parsed?.reason || null,
      });
      toast.success("Appointment booked.");
      setParsed(null); setText("");
      if (onBooked) onBooked(appt);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Booking failed");
    } finally {
      setCreating(false);
    }
  }

  const candidates = parsed || {};

  return (
    <section
      data-testid="nl-book-card"
      className="rounded-md border border-border bg-card p-4 space-y-3"
    >
      <header className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-medium">Quick book — plain English</h2>
        <span className="ml-auto text-[10px] uppercase tracking-wider text-muted-foreground">
          AI-assisted
        </span>
      </header>
      <form
        onSubmit={(e) => { e.preventDefault(); parse(); }}
        className="flex items-center gap-2"
      >
        <Input
          data-testid="nl-book-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder='e.g. "Book Hannah Whitaker for an adjustment with Dr. Park next Tuesday at 2pm"'
          className="text-sm"
        />
        <Button
          type="submit"
          size="sm"
          disabled={parsing || text.trim().length < 2}
          data-testid="nl-book-parse-btn"
          className="rounded-sm"
        >
          {parsing ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Sparkles className="h-4 w-4" />
          )}
        </Button>
      </form>

      {parsed && (
        <div className="space-y-3 pt-1" data-testid="nl-book-parsed">
          {Array.isArray(parsed.clarifications) && parsed.clarifications.length > 0 && (
            <ul
              data-testid="nl-book-clarifications"
              className="space-y-1 rounded-sm border border-amber-500/30 bg-amber-500/10 p-2 text-xs"
            >
              {parsed.clarifications.map((c, i) => (
                <li key={i} className="flex items-start gap-1.5">
                  <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                  <span>{c}</span>
                </li>
              ))}
            </ul>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {/* Patient */}
            <label className="space-y-1">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Patient
              </span>
              {patientId ? (
                <p
                  data-testid="nl-book-patient-resolved"
                  className="rounded-sm border border-border/60 bg-muted/30 px-2 py-1.5 text-xs"
                >
                  {candidates.patient?.candidates?.find((c) => c.id === patientId)?.name
                   || candidates.patient?.name}
                </p>
              ) : (
                <Select value={patientId} onValueChange={setPatientId}>
                  <SelectTrigger data-testid="nl-book-patient-select">
                    <SelectValue placeholder={
                      (candidates.patient?.candidates?.length || 0) === 0
                        ? "No patient match — refine the request"
                        : "Pick the patient"
                    } />
                  </SelectTrigger>
                  <SelectContent>
                    {(candidates.patient?.candidates || []).map((c) => (
                      <SelectItem key={c.id} value={c.id}>
                        {c.name} {c.reason && `— ${c.reason}`}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </label>

            {/* Provider */}
            <label className="space-y-1">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Provider
              </span>
              {providerId ? (
                <p
                  data-testid="nl-book-provider-resolved"
                  className="rounded-sm border border-border/60 bg-muted/30 px-2 py-1.5 text-xs"
                >
                  {candidates.provider?.candidates?.find((c) => c.id === providerId)?.name
                   || candidates.provider?.name}
                </p>
              ) : (
                <Select value={providerId} onValueChange={setProviderId}>
                  <SelectTrigger data-testid="nl-book-provider-select">
                    <SelectValue placeholder={
                      (candidates.provider?.candidates?.length || 0) === 0
                        ? "No provider match — refine the request"
                        : "Pick the provider"
                    } />
                  </SelectTrigger>
                  <SelectContent>
                    {(candidates.provider?.candidates || []).map((c) => (
                      <SelectItem key={c.id} value={c.id}>
                        {c.name} {c.reason && `— ${c.reason}`}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </label>

            {/* Start */}
            <label className="space-y-1">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Start
              </span>
              <Input
                type="datetime-local"
                data-testid="nl-book-start-input"
                value={startLocal}
                onChange={(e) => setStartLocal(e.target.value)}
              />
            </label>

            {/* Duration */}
            <label className="space-y-1">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Duration (minutes)
              </span>
              <Input
                type="number"
                min={5}
                max={240}
                data-testid="nl-book-duration-input"
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value) || 30)}
              />
            </label>
          </div>

          <Button
            size="sm"
            onClick={confirm}
            disabled={creating || !patientId || !providerId || !startLocal}
            data-testid="nl-book-confirm-btn"
            className="rounded-sm"
          >
            {creating ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <CalendarPlus className="mr-1.5 h-3.5 w-3.5" />
            )}
            Create appointment
          </Button>
        </div>
      )}
    </section>
  );
}
