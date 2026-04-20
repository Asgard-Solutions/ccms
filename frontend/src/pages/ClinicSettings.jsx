import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Building2, Clock, Save, MapPin, Phone, Mail, Globe } from "lucide-react";
import { api } from "../api/client";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import { Switch } from "../components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import { Skeleton } from "../components/ui/skeleton";
import AppointmentTypesManager from "./AppointmentTypesManager";

// Display order is Sunday→Saturday to match the calendar views.
// Backend day_of_week is 0=Monday; `BACKEND_DOW[i]` maps row i to the
// backend's numeric day.
const DISPLAY_ORDER = [
  { label: "Sunday", backend: 6 },
  { label: "Monday", backend: 0 },
  { label: "Tuesday", backend: 1 },
  { label: "Wednesday", backend: 2 },
  { label: "Thursday", backend: 3 },
  { label: "Friday", backend: 4 },
  { label: "Saturday", backend: 5 },
];

const TIMEZONES = [
  "America/Los_Angeles",
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "America/Anchorage",
  "America/Phoenix",
  "Pacific/Honolulu",
  "America/Toronto",
  "Europe/London",
  "Europe/Berlin",
  "Asia/Kolkata",
  "UTC",
];

function defaultHoursRows() {
  // Sun-Sat display order. Weekdays Mon-Fri open 09-17; weekends closed.
  return DISPLAY_ORDER.map((d) => ({
    ...d,
    is_closed: d.backend === 5 || d.backend === 6, // Sat (5) / Sun (6)
    open_time: "09:00",
    close_time: "17:00",
  }));
}

function hoursFromBackend(backendHours) {
  if (!backendHours) return defaultHoursRows();
  const byDow = {};
  for (const h of backendHours) byDow[h.day_of_week] = h;
  return DISPLAY_ORDER.map((d) => {
    const row = byDow[d.backend];
    if (!row) return { ...d, is_closed: true, open_time: "09:00", close_time: "17:00" };
    const first = row.intervals?.[0];
    return {
      ...d,
      is_closed: !!row.is_closed,
      open_time: first?.open_time || "09:00",
      close_time: first?.close_time || "17:00",
    };
  });
}

function hoursToBackend(rows) {
  return rows.map((r) => ({
    day_of_week: r.backend,
    is_closed: r.is_closed,
    intervals: r.is_closed
      ? []
      : [{ open_time: r.open_time, close_time: r.close_time }],
  }));
}

function Field({ label, icon: Icon, children, className = "", ...rest }) {
  return (
    <div className={`space-y-1 ${className}`}>
      <Label className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {Icon && <Icon className="h-3 w-3" />}
        {label}
      </Label>
      {children || <Input {...rest} className="rounded-sm" />}
    </div>
  );
}

export default function ClinicSettings() {
  const [locations, setLocations] = useState([]);
  const [locationId, setLocationId] = useState(null);
  const [profileExists, setProfileExists] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [form, setForm] = useState({
    name: "",
    address_line1: "",
    address_line2: "",
    city: "",
    state: "",
    postal_code: "",
    country: "US",
    primary_phone: "",
    secondary_phone: "",
    email: "",
    website: "",
    timezone: "America/Los_Angeles",
    notes: "",
  });
  const [hoursRows, setHoursRows] = useState(defaultHoursRows());

  useEffect(() => {
    (async () => {
      try {
        const ctx = await api.get("/tenancy/me/context");
        const locs = ctx.data?.locations || [];
        setLocations(locs);
        if (locs[0]) setLocationId(locs[0].id);
      } catch {
        toast.error("Could not load locations");
      }
    })();
  }, []);

  useEffect(() => {
    if (!locationId) return;
    (async () => {
      setLoading(true);
      try {
        const r = await api.get(`/clinic-profiles/${locationId}`);
        const p = r.data;
        setProfileExists(true);
        setForm({
          name: p.name || "",
          address_line1: p.address_line1 || "",
          address_line2: p.address_line2 || "",
          city: p.city || "",
          state: p.state || "",
          postal_code: p.postal_code || "",
          country: p.country || "US",
          primary_phone: p.primary_phone || "",
          secondary_phone: p.secondary_phone || "",
          email: p.email || "",
          website: p.website || "",
          timezone: p.timezone || "America/Los_Angeles",
          notes: p.notes || "",
        });
        setHoursRows(hoursFromBackend(p.hours));
      } catch (err) {
        if (err.response?.status === 404) {
          setProfileExists(false);
          const loc = locations.find((l) => l.id === locationId);
          setForm((f) => ({
            ...f,
            name: loc?.name || "",
            timezone: loc?.timezone || "America/Los_Angeles",
          }));
          setHoursRows(defaultHoursRows());
        } else {
          toast.error("Could not load clinic profile");
        }
      } finally {
        setLoading(false);
      }
    })();
  }, [locationId, locations]);

  const update = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));
  const setRow = (idx, patch) =>
    setHoursRows((rows) => rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));

  const rowsValid = useMemo(() => {
    for (const r of hoursRows) {
      if (r.is_closed) continue;
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(r.open_time)) return false;
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(r.close_time)) return false;
      const [oh, om] = r.open_time.split(":").map(Number);
      const [ch, cm] = r.close_time.split(":").map(Number);
      if (ch * 60 + cm <= oh * 60 + om) return false;
    }
    return true;
  }, [hoursRows]);

  async function onSave(e) {
    e.preventDefault();
    if (!locationId) {
      toast.error("No location selected — refresh and try again");
      return;
    }
    if (!form.name.trim()) {
      toast.error("Clinic name is required");
      return;
    }
    if (!rowsValid) {
      toast.error("One or more open days have invalid hours (close must be after open)");
      return;
    }
    setSaving(true);
    try {
      const payload = { ...form, hours: hoursToBackend(hoursRows) };
      for (const k of Object.keys(payload)) {
        if (typeof payload[k] === "string" && payload[k].trim() === "" && k !== "name") {
          payload[k] = null;
        }
      }

      async function doPut() {
        await api.put(`/clinic-profiles/${locationId}`, payload);
        setProfileExists(true);
        toast.success("Clinic settings saved");
      }

      async function doPost() {
        await api.post(`/clinic-profiles`, { ...payload, location_id: locationId });
        setProfileExists(true);
        toast.success("Clinic profile created");
      }

      if (profileExists) {
        await doPut();
      } else {
        try {
          await doPost();
        } catch (err) {
          // Auto-recover from "already exists" by switching to PUT.
          if (err.response?.status === 409) {
            await doPut();
          } else {
            throw err;
          }
        }
      }
    } catch (err) {
      console.error("Clinic settings save failed:", err, err?.response?.data);
      const detail = err.response?.data?.detail;
      const message = Array.isArray(detail)
        ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
        : detail || err.message || "Save failed";
      toast.error(message);
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <Skeleton data-testid="clinic-settings-skeleton" className="h-[640px] rounded-sm" />;
  }

  return (
    <div data-testid="clinic-settings-page" className="space-y-8 animate-in fade-in duration-300">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Settings
          </span>
          <h1 className="mt-2 font-display text-4xl font-medium tracking-tight">
            Clinic profile &amp; hours
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Address, contact details, and per-weekday hours that drive Scheduling.
          </p>
        </div>
        {locations.length > 1 && (
          <div className="space-y-1">
            <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Location
            </Label>
            <Select value={locationId || ""} onValueChange={setLocationId}>
              <SelectTrigger data-testid="clinic-settings-location-select" className="w-64 rounded-sm">
                <SelectValue placeholder="Pick a location" />
              </SelectTrigger>
              <SelectContent>
                {locations.map((l) => (
                  <SelectItem key={l.id} value={l.id}>{l.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </header>

      {!profileExists && (
        <div
          data-testid="clinic-settings-unconfigured"
          className="rounded-sm border border-dashed border-border bg-muted px-5 py-4 text-sm text-muted-foreground"
        >
          No profile yet for this location — we've pre-filled sensible defaults.
          Review the fields below and hit <strong>Save</strong> to create one.
        </div>
      )}

      <form onSubmit={onSave} className="space-y-8">
        <section className="rounded-sm border border-border bg-card p-5">
          <div className="mb-4 flex items-center gap-2">
            <Building2 className="h-4 w-4 text-primary" />
            <h2 className="font-display text-lg font-medium">Profile</h2>
          </div>
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
            <Field label="Clinic name" data-testid="clinic-field-name"
                   value={form.name}
                   onChange={(e) => update("name")(e.target.value)}
                   required />
            <div className="space-y-1">
              <Label className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <Clock className="h-3 w-3" /> Timezone
              </Label>
              <Select value={form.timezone} onValueChange={update("timezone")}>
                <SelectTrigger data-testid="clinic-field-timezone" className="rounded-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TIMEZONES.map((tz) => (
                    <SelectItem key={tz} value={tz}>{tz}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Field label="Address line 1" icon={MapPin} data-testid="clinic-field-addr1"
                   value={form.address_line1}
                   onChange={(e) => update("address_line1")(e.target.value)} />
            <Field label="Address line 2" data-testid="clinic-field-addr2"
                   value={form.address_line2}
                   onChange={(e) => update("address_line2")(e.target.value)} />
            <Field label="City" data-testid="clinic-field-city"
                   value={form.city} onChange={(e) => update("city")(e.target.value)} />
            <div className="grid grid-cols-2 gap-3">
              <Field label="State" data-testid="clinic-field-state"
                     value={form.state} onChange={(e) => update("state")(e.target.value)} />
              <Field label="Postal code" data-testid="clinic-field-postal"
                     value={form.postal_code}
                     onChange={(e) => update("postal_code")(e.target.value)} />
            </div>
            <Field label="Primary phone" icon={Phone} data-testid="clinic-field-phone1"
                   value={form.primary_phone}
                   onChange={(e) => update("primary_phone")(e.target.value)} />
            <Field label="Secondary phone" data-testid="clinic-field-phone2"
                   value={form.secondary_phone}
                   onChange={(e) => update("secondary_phone")(e.target.value)} />
            <Field label="Email" icon={Mail} data-testid="clinic-field-email"
                   type="email"
                   value={form.email} onChange={(e) => update("email")(e.target.value)} />
            <Field label="Website" icon={Globe} data-testid="clinic-field-website"
                   value={form.website} onChange={(e) => update("website")(e.target.value)} />
            <div className="md:col-span-2 space-y-1">
              <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Notes
              </Label>
              <Textarea
                data-testid="clinic-field-notes"
                rows={3}
                value={form.notes}
                onChange={(e) => update("notes")(e.target.value)}
                className="rounded-sm"
              />
            </div>
          </div>
        </section>

        <section className="rounded-sm border border-border bg-card p-5">
          <div className="mb-4 flex items-center gap-2">
            <Clock className="h-4 w-4 text-primary" />
            <h2 className="font-display text-lg font-medium">Hours of operation</h2>
          </div>
          <p className="mb-4 text-xs text-muted-foreground">
            Scheduling's Day view shows 2 hours before open through 2 hours after close.
          </p>
          <div className="overflow-hidden rounded-sm border border-border">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-muted/50 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 text-left">Day</th>
                  <th className="px-4 py-2 text-left">Open</th>
                  <th className="px-4 py-2 text-left">Open time</th>
                  <th className="px-4 py-2 text-left">Close time</th>
                </tr>
              </thead>
              <tbody>
                {hoursRows.map((row, idx) => (
                  <tr
                    key={row.backend}
                    data-testid={`clinic-hours-row-${row.backend}`}
                    className="border-b border-border last:border-b-0"
                  >
                    <td className="px-4 py-3 font-medium">{row.label}</td>
                    <td className="px-4 py-3">
                      <Switch
                        data-testid={`clinic-hours-open-${row.backend}`}
                        checked={!row.is_closed}
                        onCheckedChange={(v) => setRow(idx, { is_closed: !v })}
                      />
                    </td>
                    <td className="px-4 py-3">
                      <Input
                        type="time"
                        step={900}
                        disabled={row.is_closed}
                        data-testid={`clinic-hours-open-time-${row.backend}`}
                        value={row.open_time}
                        onChange={(e) => setRow(idx, { open_time: e.target.value })}
                        className="w-32 rounded-sm"
                      />
                    </td>
                    <td className="px-4 py-3">
                      <Input
                        type="time"
                        step={900}
                        disabled={row.is_closed}
                        data-testid={`clinic-hours-close-time-${row.backend}`}
                        value={row.close_time}
                        onChange={(e) => setRow(idx, { close_time: e.target.value })}
                        className="w-32 rounded-sm"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <div className="flex justify-end gap-2">
          <Button
            type="submit"
            disabled={saving}
            data-testid="clinic-settings-save-btn"
            className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
          >
            <Save className="mr-2 h-4 w-4" />
            {saving ? "Saving…" : profileExists ? "Save changes" : "Create profile"}
          </Button>
        </div>
      </form>

      <AppointmentTypesManager />
    </div>
  );
}
