/**
 * LicensesTab — Account Settings section for clinician professional
 * licenses + NPI.
 *
 * Role-aware: for non-clinicians the backend returns an empty list
 * and blocks writes with 403. The tab is also hidden from the tab bar
 * for non-clinicians by Security.jsx — this component assumes a
 * clinician viewer but still degrades gracefully.
 *
 * Single-valued NPI lives on the user profile; multi-valued licenses
 * live at /auth/me/licenses (add / edit / remove).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  BadgeCheck,
  CalendarClock,
  IdCard,
  Pill,
  Pencil,
  Plus,
  Stethoscope,
  Trash2,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { useAuth } from "../../contexts/AuthContext";
import { describeNpiError, NPI_CHECKSUM_DISCLAIMER } from "../../utils/npi";
import { describeDeaError, DEA_CHECKSUM_DISCLAIMER } from "../../utils/dea";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { formatDate } from "../../utils/time";
import ConfirmDialog from "../../components/ConfirmDialog";

const LICENSE_TYPES = [
  { value: "DC", label: "DC — Doctor of Chiropractic" },
  { value: "MD", label: "MD — Medical Doctor" },
  { value: "DO", label: "DO — Osteopathic Physician" },
  { value: "PT", label: "PT — Physical Therapist" },
  { value: "DPT", label: "DPT — Doctor of Physical Therapy" },
  { value: "RN", label: "RN — Registered Nurse" },
  { value: "NP", label: "NP — Nurse Practitioner" },
  { value: "PA", label: "PA — Physician Assistant" },
  { value: "LMT", label: "LMT — Licensed Massage Therapist" },
  { value: "ATC", label: "ATC — Athletic Trainer" },
  { value: "DACBR", label: "DACBR — Diplomate, Chiro Board of Radiology" },
  { value: "DACNB", label: "DACNB — Diplomate, Chiro Board of Neurology" },
  { value: "CCSP", label: "CCSP — Certified Chiro Sports Physician" },
  { value: "other", label: "Other" },
];

const US_STATES = [
  "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
  "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
  "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
  "VA","WA","WV","WI","WY","DC",
];

const EMPTY_LICENSE = {
  license_type: "DC",
  license_number: "",
  issuing_state: "",
  expiration_date: "",
  specialty: "",
  board_notes: "",
};

function expirationMeta(iso) {
  if (!iso) return { label: "—", tone: "muted" };
  try {
    const d = new Date(iso + "T23:59:59");
    const diff = Math.ceil(
      (d.getTime() - Date.now()) / (24 * 60 * 60 * 1000),
    );
    if (diff < 0)
      return { label: `Expired ${-diff}d ago`, tone: "destructive" };
    if (diff <= 60)
      return { label: `Expires in ${diff}d`, tone: "warning" };
    return { label: `Expires ${formatDate(iso)}`, tone: "muted" };
  } catch {
    return { label: formatDate(iso), tone: "muted" };
  }
}

function LicenseDialog({ open, mode, initial, onOpenChange, onDone }) {
  const [form, setForm] = useState(EMPTY_LICENSE);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (initial) {
      setForm({
        license_type: initial.license_type || "DC",
        license_number: initial.license_number || "",
        issuing_state: initial.issuing_state || "",
        expiration_date: initial.expiration_date || "",
        specialty: initial.specialty || "",
        board_notes: initial.board_notes || "",
      });
    } else {
      setForm(EMPTY_LICENSE);
    }
  }, [open, initial]);

  const set = (k) => (e) => {
    const v = typeof e === "string" ? e : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
  };

  const valid =
    form.license_type &&
    form.license_number.trim().length >= 2 &&
    /^[A-Za-z]{2}$/.test(form.issuing_state) &&
    /^\d{4}-\d{2}-\d{2}$/.test(form.expiration_date);

  const submit = async (e) => {
    e.preventDefault();
    if (!valid) {
      toast.error("Fill every required field.");
      return;
    }
    setSubmitting(true);
    try {
      const payload = {
        license_type: form.license_type,
        license_number: form.license_number.trim(),
        issuing_state: form.issuing_state.toUpperCase(),
        expiration_date: form.expiration_date,
        specialty: form.specialty.trim() || null,
        board_notes: form.board_notes.trim() || null,
      };
      if (mode === "edit") {
        await api.patch(`/auth/me/licenses/${initial.id}`, payload);
        toast.success("License updated.");
      } else {
        await api.post("/auth/me/licenses", payload);
        toast.success("License added.");
      }
      onDone();
      onOpenChange(false);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !submitting && onOpenChange(v)}>
      <DialogContent
        data-testid={`license-${mode}-dialog`}
        className="max-w-xl rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            {mode === "edit" ? "Edit license" : "Add license"}
          </DialogTitle>
          <DialogDescription>
            Professional licenses are not used for authentication — they
            surface on your provider profile and on clinical documents you
            sign.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1">
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Type
              </Label>
              <Select value={form.license_type} onValueChange={set("license_type")}>
                <SelectTrigger
                  data-testid="license-type-trigger"
                  className="rounded-sm"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="max-h-80">
                  {LICENSE_TYPES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                License number
              </Label>
              <Input
                value={form.license_number}
                onChange={set("license_number")}
                maxLength={40}
                data-testid="license-number-input"
                className="rounded-sm font-mono"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Issuing state
              </Label>
              <Select
                value={form.issuing_state || undefined}
                onValueChange={set("issuing_state")}
              >
                <SelectTrigger
                  data-testid="license-state-trigger"
                  className="rounded-sm"
                >
                  <SelectValue placeholder="Select state…" />
                </SelectTrigger>
                <SelectContent className="max-h-80">
                  {US_STATES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Expiration date
              </Label>
              <Input
                type="date"
                value={form.expiration_date}
                onChange={set("expiration_date")}
                data-testid="license-expiration-input"
                className="rounded-sm"
              />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Specialty (optional)
              </Label>
              <Input
                value={form.specialty}
                onChange={set("specialty")}
                maxLength={120}
                placeholder="e.g. Diversified technique, sports medicine"
                data-testid="license-specialty-input"
                className="rounded-sm"
              />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Board notes (optional)
              </Label>
              <Input
                value={form.board_notes}
                onChange={set("board_notes")}
                maxLength={500}
                placeholder="e.g. Board-certified DACNB since 2020"
                data-testid="license-notes-input"
                className="rounded-sm"
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={submitting}
              onClick={() => onOpenChange(false)}
              className="rounded-sm"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!valid || submitting}
              data-testid="license-submit-btn"
              className="rounded-sm"
            >
              {submitting ? "Saving…" : mode === "edit" ? "Save changes" : "Add license"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function NpiCard() {
  const { user, refresh } = useAuth();
  const [value, setValue] = useState(user?.npi_number || "");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValue(user?.npi_number || "");
  }, [user]);

  const digits = value.replace(/\D/g, "");
  // Empty string = "clear the field" → always valid. Otherwise require
  // the full 10-digit Luhn-valid form before Save enables.
  const errorMessage = digits === "" ? null : describeNpiError(digits);
  const valid = errorMessage === null;
  const dirty = (user?.npi_number || "") !== digits;

  const save = async () => {
    if (!valid) {
      toast.error(errorMessage || "NPI is invalid.");
      return;
    }
    setSaving(true);
    try {
      await api.patch("/auth/me/profile", { npi_number: digits });
      toast.success(digits ? "NPI saved." : "NPI cleared.");
      await refresh();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      data-testid="npi-card"
      className="rounded-sm border border-border bg-card p-6"
    >
      <div className="flex items-center gap-2">
        <IdCard className="h-5 w-5 text-primary" aria-hidden="true" />
        <h2 className="font-display text-2xl font-medium">NPI number</h2>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">
        Your CMS 10-digit National Provider Identifier. Used when rendering
        claims and on clinical notes you sign.
      </p>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <div className="flex-1 space-y-1 min-w-[220px]">
          <Label
            htmlFor="npi-input-field"
            className="text-xs uppercase tracking-wider text-muted-foreground"
          >
            NPI
          </Label>
          <Input
            id="npi-input-field"
            value={digits}
            onChange={(e) =>
              setValue(e.target.value.replace(/\D/g, "").slice(0, 10))
            }
            placeholder="1234567890"
            data-testid="npi-input"
            inputMode="numeric"
            pattern="\d{10}"
            maxLength={10}
            aria-invalid={!valid}
            aria-describedby="npi-help"
            className="rounded-sm font-mono tracking-widest focus-visible:ring-2 focus-visible:ring-primary"
          />
        </div>
        <Button
          onClick={save}
          disabled={!dirty || !valid || saving}
          data-testid="npi-save-btn"
          className="rounded-sm"
        >
          {saving ? "Saving…" : "Save NPI"}
        </Button>
      </div>
      {!valid && (
        <p
          data-testid="npi-error"
          role="alert"
          aria-live="polite"
          className="mt-1 text-[11px] text-destructive"
        >
          {errorMessage}
        </p>
      )}
      <p
        id="npi-help"
        data-testid="npi-disclaimer"
        className="mt-2 text-[11px] text-muted-foreground"
      >
        {NPI_CHECKSUM_DISCLAIMER}
      </p>
    </div>
  );
}

function DeaCard() {
  const { user, refresh } = useAuth();
  const [value, setValue] = useState(user?.dea_number || "");
  const [expires, setExpires] = useState(user?.dea_expires_at || "");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValue(user?.dea_number || "");
    setExpires(user?.dea_expires_at || "");
  }, [user]);

  // Keep internal state in upper-case — the backend also normalises,
  // but mirroring it here means the UI shows the user exactly what
  // will be persisted.
  const normalised = value.trim().toUpperCase();
  const errorMessage =
    normalised === "" ? null : describeDeaError(normalised);
  const valid = errorMessage === null;
  const dirty =
    (user?.dea_number || "") !== normalised ||
    (user?.dea_expires_at || "") !== (expires || "");

  const save = async () => {
    if (!valid) {
      toast.error(errorMessage || "DEA number is invalid.");
      return;
    }
    setSaving(true);
    try {
      await api.patch("/auth/me/profile", {
        dea_number: normalised,
        dea_expires_at: expires || "",
      });
      toast.success(normalised ? "DEA saved." : "DEA cleared.");
      await refresh();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      data-testid="dea-card"
      className="rounded-sm border border-border bg-card p-6"
    >
      <div className="flex items-center gap-2">
        <Pill className="h-5 w-5 text-primary" aria-hidden="true" />
        <h2 className="font-display text-2xl font-medium">DEA number</h2>
        <span className="ml-auto rounded-sm bg-muted px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Optional — prescribers only
        </span>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">
        DEA registration number for prescribing controlled substances. Only
        complete this section if your role requires it. The format check
        below is an anti-typo safeguard — it does not prove federal
        registration status.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
        <div className="space-y-1 min-w-[220px]">
          <Label
            htmlFor="dea-input-field"
            className="text-xs uppercase tracking-wider text-muted-foreground"
          >
            DEA number
          </Label>
          <Input
            id="dea-input-field"
            value={normalised}
            onChange={(e) =>
              setValue(
                e.target.value
                  .toUpperCase()
                  .replace(/[^A-Z0-9]/g, "")
                  .slice(0, 9),
              )
            }
            placeholder="AB1234563"
            data-testid="dea-input"
            inputMode="text"
            maxLength={9}
            autoCapitalize="characters"
            spellCheck={false}
            aria-invalid={!valid}
            aria-describedby="dea-help"
            className="rounded-sm font-mono tracking-widest uppercase focus-visible:ring-2 focus-visible:ring-primary"
          />
        </div>
        <div className="space-y-1">
          <Label
            htmlFor="dea-expiry-field"
            className="text-xs uppercase tracking-wider text-muted-foreground"
          >
            Expires
          </Label>
          <Input
            id="dea-expiry-field"
            type="date"
            value={expires || ""}
            onChange={(e) => setExpires(e.target.value)}
            data-testid="dea-expiry"
            className="rounded-sm focus-visible:ring-2 focus-visible:ring-primary"
          />
        </div>
        <Button
          onClick={save}
          disabled={!dirty || !valid || saving}
          data-testid="dea-save-btn"
          className="rounded-sm"
        >
          {saving ? "Saving…" : "Save DEA"}
        </Button>
      </div>
      {!valid && (
        <p
          data-testid="dea-error"
          role="alert"
          aria-live="polite"
          className="mt-1 text-[11px] text-destructive"
        >
          {errorMessage}
        </p>
      )}
      <p
        id="dea-help"
        data-testid="dea-disclaimer"
        className="mt-2 text-[11px] text-muted-foreground"
      >
        {DEA_CHECKSUM_DISCLAIMER}
      </p>
    </div>
  );
}

function LicenseRow({ row, onEdit, onDelete }) {
  const exp = expirationMeta(row.expiration_date);
  return (
    <li
      data-testid={`license-row-${row.id}`}
      className="grid grid-cols-1 gap-3 rounded-sm border border-border bg-background p-4 sm:grid-cols-[auto_1fr_auto]"
    >
      <div className="flex h-10 w-10 items-center justify-center rounded-sm bg-primary/10 text-primary">
        <BadgeCheck className="h-5 w-5" />
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 font-display text-lg">
          <span className="font-medium">{row.license_type}</span>
          <span className="text-muted-foreground">·</span>
          <span className="font-mono">{row.issuing_state}</span>
          <span className="text-muted-foreground">·</span>
          <span className="font-mono">{row.license_number}</span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <CalendarClock className="h-3 w-3" />
            <span
              data-testid={`license-${row.id}-exp`}
              className={
                exp.tone === "destructive"
                  ? "text-destructive"
                  : exp.tone === "warning"
                    ? "text-warning"
                    : ""
              }
            >
              {exp.label}
            </span>
          </span>
          {row.specialty && (
            <span className="flex items-center gap-1">
              <Stethoscope className="h-3 w-3" />
              {row.specialty}
            </span>
          )}
        </div>
        {row.board_notes && (
          <p className="mt-2 text-xs text-muted-foreground">
            {row.board_notes}
          </p>
        )}
      </div>
      <div className="flex items-start gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onEdit(row)}
          data-testid={`license-${row.id}-edit-btn`}
          className="rounded-sm"
        >
          <Pencil className="mr-1 h-3 w-3" /> Edit
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onDelete(row)}
          data-testid={`license-${row.id}-delete-btn`}
          className="rounded-sm border-destructive text-destructive hover:bg-destructive-soft"
        >
          <Trash2 className="h-3 w-3" />
        </Button>
      </div>
    </li>
  );
}

export default function LicensesTab() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me/licenses");
      setRows(data);
      setErr(null);
    } catch (e) {
      setErr(formatApiError(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const expiringCount = useMemo(() => {
    if (!rows) return 0;
    return rows.filter(
      (r) => expirationMeta(r.expiration_date).tone !== "muted",
    ).length;
  }, [rows]);

  return (
    <section
      data-testid="licenses-tab"
      className="space-y-6 animate-in fade-in duration-200"
    >
      <NpiCard />
      <DeaCard />

      <div
        data-testid="licenses-card"
        className="rounded-sm border border-border bg-card p-6"
      >
        <div className="flex flex-wrap items-center gap-3">
          <BadgeCheck className="h-5 w-5 text-primary" />
          <h2 className="font-display text-2xl font-medium">
            Professional licenses
          </h2>
          {rows && rows.length > 0 && (
            <Badge
              variant="outline"
              data-testid="licenses-count-badge"
              className="rounded-sm"
            >
              {rows.length} active
            </Badge>
          )}
          {expiringCount > 0 && (
            <Badge
              data-testid="licenses-expiring-badge"
              className="rounded-sm bg-warning-soft text-warning-foreground"
            >
              {expiringCount} need attention
            </Badge>
          )}
          <div className="ml-auto">
            <Button
              onClick={() => setAdding(true)}
              data-testid="license-add-btn"
              className="rounded-sm"
            >
              <Plus className="mr-1.5 h-4 w-4" />
              Add license
            </Button>
          </div>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          Add one row per jurisdiction. Rows with expirations in the next 60
          days are flagged so you can start the renewal early.
        </p>

        {err && (
          <p className="mt-3 text-xs text-destructive">{err}</p>
        )}

        {rows === null ? (
          <div className="mt-4 h-24 animate-pulse rounded-sm bg-muted/50" />
        ) : rows.length === 0 ? (
          <div
            data-testid="licenses-empty-state"
            className="mt-4 rounded-sm border border-dashed border-border bg-background p-6 text-center text-sm text-muted-foreground"
          >
            No licenses yet. Add one to keep your provider profile current.
          </div>
        ) : (
          <ul className="mt-4 space-y-2">
            {rows.map((r) => (
              <LicenseRow
                key={r.id}
                row={r}
                onEdit={(row) => setEditing(row)}
                onDelete={(row) => setConfirmDelete(row)}
              />
            ))}
          </ul>
        )}
      </div>

      <LicenseDialog
        open={adding}
        mode="add"
        initial={null}
        onOpenChange={setAdding}
        onDone={load}
      />
      <LicenseDialog
        open={!!editing}
        mode="edit"
        initial={editing}
        onOpenChange={(v) => !v && setEditing(null)}
        onDone={load}
      />
      <ConfirmDialog
        open={!!confirmDelete}
        onOpenChange={(v) => !v && setConfirmDelete(null)}
        title="Remove license?"
        description={
          confirmDelete
            ? `Delete ${confirmDelete.license_type} · ${confirmDelete.issuing_state} · ${confirmDelete.license_number}?`
            : undefined
        }
        confirmLabel="Remove"
        destructive
        onConfirm={async () => {
          if (!confirmDelete) return;
          try {
            await api.delete(`/auth/me/licenses/${confirmDelete.id}`);
            toast.success("License removed.");
            await load();
          } catch (err) {
            toast.error(formatApiError(err));
            throw err;
          }
        }}
        testId="license-delete-confirm"
      />
    </section>
  );
}
