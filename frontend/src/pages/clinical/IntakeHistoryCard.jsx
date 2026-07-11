/**
 * IntakeHistoryCard — Phase 2 chart-level history view.
 *
 * Auto-seeds on first open via GET /clinical/history. Provider can edit any
 * field inline; each edit flips its `source` to `provider_edit`. Explicit
 * re-import from the latest completed intake form is opt-in and preserves
 * provider edits.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Download, Pencil, Save, X } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { Badge } from "../../components/ui/badge";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import { formatDateTime } from "../../utils/time";

const TEXT_FIELDS = [
  { key: "chief_complaint", label: "Chief complaint", rows: 2 },
  { key: "history_of_present_illness", label: "History of present illness (HPI)", rows: 5 },
  { key: "mechanism_of_injury", label: "Mechanism of injury", rows: 3 },
  { key: "pain_radiation", label: "Pain radiation", rows: 2 },
  { key: "prior_treatment", label: "Prior treatment", rows: 3 },
  { key: "medications", label: "Medications", rows: 2 },
  { key: "allergies", label: "Allergies", rows: 2 },
  { key: "past_medical_history", label: "Past medical history", rows: 3 },
  { key: "past_surgical_history", label: "Past surgical history", rows: 2 },
  { key: "family_history", label: "Family history", rows: 2 },
  { key: "social_history", label: "Social history", rows: 2 },
  { key: "review_of_systems", label: "Review of systems", rows: 4 },
];

const INLINE_FIELDS = [
  { key: "onset_date", label: "Onset date", type: "date" },
  { key: "severity", label: "Severity (0–10)", type: "number", min: 0, max: 10 },
  { key: "occupation", label: "Occupation", type: "text" },
  { key: "activity_level", label: "Activity level", type: "text" },
];

const LIST_FIELDS = [
  { key: "pain_locations", label: "Pain locations" },
  { key: "aggravating_factors", label: "Aggravating factors" },
  { key: "relieving_factors", label: "Relieving factors" },
];

const BOOL_FIELDS = [
  { key: "prior_chiropractic_care", label: "Prior chiropractic care" },
];

const DICT_PREVIEW = [
  { key: "accident_details", label: "Accident / injury details" },
  { key: "work_comp_details", label: "Workers' comp details" },
  { key: "red_flag_screening", label: "Red-flag screening" },
];

function SourceBadge({ meta }) {
  // Phase 1 redesign: field-level "From intake" badges are too noisy. Only
  // surface a badge when the value has been touched by a clinician (that's
  // useful, positive provenance). Intake origin lives on the section header.
  if (meta?.source === "provider_edit") {
    return (
      <Badge variant="default" className="text-[10px] font-medium capitalize">
        Provider edit
      </Badge>
    );
  }
  return null;
}

function renderReadValue(value, opts = {}) {
  // Item 4: Missing-info vocabulary
  //   * required  → "Missing required information" (warning tone)
  //   * na        → "Not applicable"              (muted, no action)
  //   * review    → "Needs review"                (warning tone)
  //   * default   → "Not documented"              (muted italic)
  const label = opts.required
    ? "Missing required information"
    : opts.na
      ? "Not applicable"
      : opts.review
        ? "Needs review"
        : "Not documented";
  const tone = opts.required || opts.review
    ? "text-warning italic"
    : "text-muted-foreground italic";
  if (value === null || value === undefined || value === "") {
    return <span className={tone}>{label}</span>;
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) {
    if (!value.length) return <span className={tone}>{label}</span>;
    return value.join(", ");
  }
  if (typeof value === "object") {
    // Special case: red-flag screening renders as a clinical sentence.
    if (opts.dictKey === "red_flag_screening") {
      return renderRedFlagScreening(value);
    }
    const pairs = Object.entries(value).filter(([, v]) => v !== null && v !== undefined && v !== "");
    if (!pairs.length) return <span className={tone}>{label}</span>;
    return (
      <ul className="list-disc pl-5 text-sm">
        {pairs.map(([k, v]) => (
          <li key={k}>
            <span className="text-muted-foreground">{k.replace(/_/g, " ")}:</span>{" "}
            {typeof v === "object" ? JSON.stringify(v) : String(v)}
          </li>
        ))}
      </ul>
    );
  }
  return String(value);
}

function renderRedFlagScreening(rf) {
  const entries = Object.entries(rf || {}).filter(
    ([, v]) => typeof v === "boolean",
  );
  if (entries.length === 0) {
    return <span className="text-muted-foreground italic">Not documented</span>;
  }
  const positives = entries.filter(([, v]) => v === true).map(([k]) => k.replace(/_/g, " "));
  const negatives = entries.filter(([, v]) => v === false).map(([k]) => k.replace(/_/g, " "));
  return (
    <div className="space-y-1 text-sm">
      {positives.length > 0 ? (
        <div className="rounded-sm border border-destructive/30 bg-destructive-soft px-2 py-1 text-destructive">
          <span className="font-medium">Positive findings:</span>{" "}
          {positives.join(", ")}
        </div>
      ) : (
        <div className="text-foreground">
          No {negatives.slice(0, 3).join(", ")}
          {negatives.length > 3 ? ", or other flags" : ""} reported.
        </div>
      )}
    </div>
  );
}

function FieldRow({ label, meta, value, editing, children, testId, required, dictKey, longText, na, review }) {
  return (
    <div
      data-testid={testId}
      className="rounded-sm border border-border bg-card p-4"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <Label className="font-semibold">{label}</Label>
        <SourceBadge meta={meta} />
      </div>
      {editing ? children : (
        <div className={longText ? "max-w-prose text-base leading-relaxed" : "text-base"}>
          {renderReadValue(value, { required, dictKey, na, review })}
        </div>
      )}
    </div>
  );
}

function parseListInput(raw) {
  return raw
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
}

export default function IntakeHistoryCard({ patientId, canWrite, onReauthNeeded }) {
  const [history, setHistory] = useState(null);
  const [err, setErr] = useState(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [importing, setImporting] = useState(false);
  const [form, setForm] = useState({});

  const load = useCallback(async () => {
    try {
      setErr(null);
      const { data } = await api.get(`/patients/${patientId}/clinical/history`);
      setHistory(data);
    } catch (e) {
      setErr(formatApiError(e));
      setHistory({});
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  const meta = history?.field_meta || {};

  const startEdit = () => {
    // Seed form from server values — lists become comma-separated strings,
    // dict fields stay as JSON text for hand-editing, booleans become "yes/no/unset".
    const init = {};
    TEXT_FIELDS.forEach(({ key }) => (init[key] = history?.[key] ?? ""));
    INLINE_FIELDS.forEach(({ key }) => (init[key] = history?.[key] ?? ""));
    LIST_FIELDS.forEach(({ key }) => (init[key] = (history?.[key] || []).join(", ")));
    BOOL_FIELDS.forEach(({ key }) => {
      const v = history?.[key];
      init[key] = v === true ? "yes" : v === false ? "no" : "unset";
    });
    DICT_PREVIEW.forEach(({ key }) => (init[key] = JSON.stringify(history?.[key] || {}, null, 2)));
    setForm(init);
    setEditing(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      const patch = {};
      TEXT_FIELDS.forEach(({ key }) => {
        const v = (form[key] ?? "").trim();
        if (v !== (history?.[key] ?? "")) patch[key] = v || null;
      });
      INLINE_FIELDS.forEach(({ key, type }) => {
        let v = form[key];
        if (type === "number") {
          v = v === "" ? null : Number(v);
        } else if (typeof v === "string") {
          v = v.trim() || null;
        }
        if (v !== (history?.[key] ?? null)) patch[key] = v;
      });
      LIST_FIELDS.forEach(({ key }) => {
        const arr = parseListInput(form[key] || "");
        const current = history?.[key] || [];
        const sameLength = arr.length === current.length;
        const sameContent = sameLength && arr.every((v, i) => v === current[i]);
        if (!sameContent) patch[key] = arr.length ? arr : null;
      });
      BOOL_FIELDS.forEach(({ key }) => {
        const v = form[key];
        const parsed = v === "yes" ? true : v === "no" ? false : null;
        if (parsed !== (history?.[key] ?? null)) patch[key] = parsed;
      });
      DICT_PREVIEW.forEach(({ key }) => {
        const raw = (form[key] || "").trim();
        if (!raw) {
          if (history?.[key]) patch[key] = null;
          return;
        }
        try {
          const parsed = JSON.parse(raw);
          if (JSON.stringify(parsed) !== JSON.stringify(history?.[key] || {})) {
            patch[key] = parsed;
          }
        } catch {
          throw new Error(`${key.replace(/_/g, " ")} is not valid JSON`);
        }
      });

      if (!Object.keys(patch).length) {
        toast.info("No changes to save");
        setEditing(false);
        return;
      }
      const { data } = await api.patch(`/patients/${patientId}/clinical/history`, patch);
      setHistory(data);
      toast.success("History updated");
      setEditing(false);
    } catch (e) {
      if (e?.response?.status === 401 && /re-auth/i.test(e.response?.data?.detail || "")) {
        onReauthNeeded?.();
      } else if (e?.message && !e?.response) {
        toast.error(e.message);
      } else {
        toast.error(formatApiError(e));
      }
    } finally {
      setSaving(false);
    }
  };

  const triggerImport = async () => {
    if (importing) return;
    setImporting(true);
    try {
      const { data } = await api.post(`/patients/${patientId}/clinical/history/import`, {});
      setHistory(data.history);
      const imported = data.imported_fields.length;
      const skipped = data.skipped_fields.length;
      toast.success(
        `Re-imported ${imported} field${imported === 1 ? "" : "s"}` +
          (skipped ? ` · preserved ${skipped} provider edit${skipped === 1 ? "" : "s"}` : ""),
      );
    } catch (e) {
      if (e?.response?.status === 401 && /re-auth/i.test(e.response?.data?.detail || "")) {
        onReauthNeeded?.();
      } else if (e?.response?.status === 409) {
        toast.info("No completed intake form available to import");
      } else {
        toast.error(formatApiError(e));
      }
    } finally {
      setImporting(false);
    }
  };

  const lastImported = useMemo(
    () => (history?.last_imported_at ? formatDateTime(history.last_imported_at) : null),
    [history?.last_imported_at],
  );

  if (history === null) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-20 rounded-lg" />
        <Skeleton className="h-20 rounded-lg" />
      </div>
    );
  }

  return (
    <section data-testid="clinical-history-card" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="font-display text-lg font-semibold text-foreground">
            Intake &amp; History
          </h3>
          <p className="text-sm text-muted-foreground">
            Chart-level narrative. Fields auto-seed once from the most recent completed intake form; provider edits are preserved on re-import.
          </p>
          {lastImported && (
            <p
              data-testid="history-last-imported"
              className="mt-1 inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-xs text-muted-foreground"
            >
              Imported from patient intake on {lastImported}
            </p>
          )}
        </div>
        {canWrite && (
          <div className="flex gap-2">
            {!editing ? (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={triggerImport}
                  disabled={importing}
                  data-testid="history-import-btn"
                  className="rounded-sm"
                >
                  <Download className="mr-1.5 h-3.5 w-3.5" />
                  {importing ? "Importing…" : "Re-import from intake"}
                </Button>
                <Button
                  size="sm"
                  onClick={startEdit}
                  data-testid="history-edit-btn"
                  className="rounded-sm"
                >
                  <Pencil className="mr-1.5 h-3.5 w-3.5" />
                  Edit
                </Button>
              </>
            ) : (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setEditing(false)}
                  disabled={saving}
                  data-testid="history-cancel-btn"
                  className="rounded-sm"
                >
                  <X className="mr-1.5 h-3.5 w-3.5" />
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={save}
                  disabled={saving}
                  data-testid="history-save-btn"
                  className="rounded-sm"
                >
                  <Save className="mr-1.5 h-3.5 w-3.5" />
                  {saving ? "Saving…" : "Save changes"}
                </Button>
              </>
            )}
          </div>
        )}
      </div>

      {err && (
        <div className="rounded-sm border border-destructive/30 bg-destructive-soft p-3 text-sm text-destructive">
          {err}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {INLINE_FIELDS.map(({ key, label, type, min, max }) => (
          <FieldRow
            key={key}
            label={label}
            meta={meta[key]}
            value={history[key]}
            editing={editing}
            testId={`history-field-${key}`}
          >
            <Input
              type={type}
              min={min}
              max={max}
              value={form[key] ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
              data-testid={`history-input-${key}`}
              className="rounded-sm"
            />
          </FieldRow>
        ))}
        {BOOL_FIELDS.map(({ key, label }) => (
          <FieldRow
            key={key}
            label={label}
            meta={meta[key]}
            value={history[key]}
            editing={editing}
            testId={`history-field-${key}`}
          >
            <select
              value={form[key] ?? "unset"}
              onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
              data-testid={`history-input-${key}`}
              className="h-10 w-full rounded-sm border border-border bg-background px-3 text-sm"
            >
              <option value="unset">— not set —</option>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          </FieldRow>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {LIST_FIELDS.map(({ key, label }) => (
          <FieldRow
            key={key}
            label={label}
            meta={meta[key]}
            value={history[key]}
            editing={editing}
            testId={`history-field-${key}`}
          >
            <Input
              placeholder="Comma-separated values"
              value={form[key] ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
              data-testid={`history-input-${key}`}
              className="rounded-sm"
            />
          </FieldRow>
        ))}
      </div>

      <div className="space-y-3">
        {TEXT_FIELDS.map(({ key, label, rows }) => (
          <FieldRow
            key={key}
            label={label}
            meta={meta[key]}
            value={history[key]}
            editing={editing}
            testId={`history-field-${key}`}
            longText
            required={key === "chief_complaint" || key === "history_of_present_illness"}
          >
            <Textarea
              rows={rows}
              value={form[key] ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
              data-testid={`history-input-${key}`}
              className="rounded-sm"
            />
          </FieldRow>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {DICT_PREVIEW.map(({ key, label }) => (
          <FieldRow
            key={key}
            label={label}
            meta={meta[key]}
            value={history[key]}
            editing={editing}
            testId={`history-field-${key}`}
            dictKey={key}
          >
            <Textarea
              rows={6}
              value={form[key] ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
              data-testid={`history-input-${key}`}
              className="rounded-sm font-mono text-xs"
              placeholder='{ "key": "value" }'
            />
          </FieldRow>
        ))}
      </div>
    </section>
  );
}
