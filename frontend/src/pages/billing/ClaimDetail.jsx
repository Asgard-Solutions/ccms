import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ClipboardCheck,
  ListChecks,
  Pencil,
  Send,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import { Skeleton } from "../../components/ui/skeleton";
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
import { formatCents, parseDollarsToCents } from "../../utils/money";
import {
  CLAIM_STATUS_LABELS,
  claimStatusTone,
  fetchClaimDetail,
  replaceClaimDiagnoses,
  replaceClaimLines,
  submitClaim,
  updateClaimHeader,
  validateClaim,
} from "./useClaims";

const EDITABLE_STATUSES = new Set(["draft", "validation_failed", "rejected"]);

export default function ClaimDetail() {
  const { id } = useParams();
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [headerOpen, setHeaderOpen] = useState(false);
  const [diagnosesOpen, setDiagnosesOpen] = useState(false);
  const [linesOpen, setLinesOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchClaimDetail(id);
      setDetail(data);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not load claim");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { refresh(); }, [refresh]);

  const editable = detail && EDITABLE_STATUSES.has(detail.claim.status);

  async function onValidate() {
    setBusy(true);
    try {
      const res = await validateClaim(id);
      toast[res.passed ? "success" : "error"](
        res.passed
          ? "Claim passed validation — ready to submit."
          : `Validation found ${res.errors.length} error${res.errors.length === 1 ? "" : "s"}`,
      );
      await refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Validation failed");
    } finally { setBusy(false); }
  }

  async function onSubmitClaim() {
    setBusy(true);
    try {
      await submitClaim(id);
      toast.success("Claim submitted");
      await refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Submit failed");
    } finally { setBusy(false); }
  }

  if (loading || !detail) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64 rounded-sm" />
        <Skeleton className="h-24 w-full rounded-sm" />
        <Skeleton className="h-40 w-full rounded-sm" />
      </div>
    );
  }

  const { claim, diagnoses, lines, latest_validation } = detail;
  const errors = latest_validation?.errors || [];
  const warnings = latest_validation?.warnings || [];

  return (
    <div data-testid="claim-detail" className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link
            to="/billing/claims"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Claims queue
          </Link>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
            Claim <span className="tabular-nums">{claim.id.slice(0, 8)}</span>
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
            <span
              className={`rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${claimStatusTone(claim.status)}`}
            >
              {CLAIM_STATUS_LABELS[claim.status] || claim.status}
            </span>
            <Link
              to={`/patients/${claim.patient_id}`}
              className="hover:underline"
            >
              Patient {claim.patient_id.slice(0, 8)}
            </Link>
            <span>Billed {formatCents(claim.billed_cents)}</span>
            <span>{claim.service_date_from} → {claim.service_date_to}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={onValidate}
            disabled={busy}
            data-testid="claim-validate-btn"
            className="rounded-sm"
          >
            <ClipboardCheck className="mr-2 h-4 w-4" />
            {busy ? "Validating…" : "Run scrubber"}
          </Button>
          <Button
            onClick={onSubmitClaim}
            disabled={busy || claim.status !== "ready"}
            data-testid="claim-submit-btn"
            className="rounded-sm"
          >
            <Send className="mr-2 h-4 w-4" /> Submit
          </Button>
        </div>
      </header>

      {/* Validation panel */}
      <ValidationPanel
        errors={errors}
        warnings={warnings}
        lastRunAt={latest_validation?.run_at}
      />

      {/* Header card */}
      <section
        data-testid="claim-header-card"
        className="rounded-sm border border-border bg-card p-6"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-xl font-medium tracking-tight">Header</h2>
          {editable && (
            <Button
              variant="outline" size="sm"
              onClick={() => setHeaderOpen(true)}
              data-testid="claim-edit-header-btn"
            >
              <Pencil className="mr-1 h-3.5 w-3.5" /> Edit
            </Button>
          )}
        </div>
        <div className="grid gap-4 text-sm sm:grid-cols-3">
          <Field label="Claim type" value={claim.claim_type || "—"} />
          <Field label="Place of service" value={claim.place_of_service || "—"} />
          <Field label="Frequency code" value={claim.frequency_code || "—"} />
          <Field label="Billing provider" value={claim.billing_provider_id || "—"} />
          <Field label="Rendering provider" value={claim.rendering_provider_id || "—"} />
          <Field label="Facility" value={claim.facility_id || "—"} />
          <Field label="Authorization #" value={claim.authorization_number || "—"} />
          <Field label="Referral #" value={claim.referral_number || "—"} />
          <Field label="Source invoice" value={claim.source_invoice_id?.slice(0, 8) || "—"} />
        </div>
        {claim.notes && (
          <p className="mt-3 rounded-sm bg-muted/50 p-2 text-xs">{claim.notes}</p>
        )}
      </section>

      {/* Diagnoses card */}
      <section
        data-testid="claim-diagnoses-card"
        className="rounded-sm border border-border bg-card p-6"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-xl font-medium tracking-tight">Diagnoses</h2>
          {editable && (
            <Button
              variant="outline" size="sm"
              onClick={() => setDiagnosesOpen(true)}
              data-testid="claim-edit-diagnoses-btn"
            >
              <Pencil className="mr-1 h-3.5 w-3.5" /> Edit
            </Button>
          )}
        </div>
        {diagnoses.length === 0 ? (
          <p className="text-sm text-muted-foreground">None.</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {diagnoses.map((d) => (
              <li
                key={d.id}
                data-testid={`claim-dx-${d.sequence}`}
                className="rounded-sm border border-border bg-muted/40 px-2 py-1 text-xs"
              >
                <span className="mr-2 font-semibold text-muted-foreground">#{d.sequence}</span>
                <span className="font-medium">{d.code || "—"}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Lines card */}
      <section
        data-testid="claim-lines-card"
        className="rounded-sm border border-border bg-card p-6"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-xl font-medium tracking-tight">Service lines</h2>
          {editable && (
            <Button
              variant="outline" size="sm"
              onClick={() => setLinesOpen(true)}
              data-testid="claim-edit-lines-btn"
            >
              <Pencil className="mr-1 h-3.5 w-3.5" /> Edit
            </Button>
          )}
        </div>
        {lines.length === 0 ? (
          <p className="text-sm text-muted-foreground">No lines.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="py-1 pr-2 w-8">#</th>
                <th className="py-1 pr-2">Code</th>
                <th className="py-1 pr-2">Service date</th>
                <th className="py-1 pr-2 text-right">Units</th>
                <th className="py-1 pr-2 text-right">Billed</th>
                <th className="py-1 pr-2">Dx pointers</th>
                <th className="py-1">Modifiers</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((ln) => (
                <tr
                  key={ln.id}
                  data-testid={`claim-line-${ln.sequence}`}
                  className="border-t border-border"
                >
                  <td className="py-1.5 pr-2 text-muted-foreground">{ln.sequence}</td>
                  <td className="py-1.5 pr-2 font-medium tabular-nums">{ln.code}</td>
                  <td className="py-1.5 pr-2 text-muted-foreground">{ln.service_date}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">{ln.units}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">{formatCents(ln.billed_cents)}</td>
                  <td className="py-1.5 pr-2 text-muted-foreground">
                    {(ln.diagnosis_pointers || []).join(", ") || "—"}
                  </td>
                  <td className="py-1.5 text-muted-foreground">
                    {(ln.modifiers || []).map((m) => `-${m}`).join(" ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <HeaderEditDialog
        open={headerOpen} onOpenChange={setHeaderOpen}
        claim={claim} onSaved={refresh}
      />
      <DiagnosesEditDialog
        open={diagnosesOpen} onOpenChange={setDiagnosesOpen}
        claimId={id} diagnoses={diagnoses} onSaved={refresh}
      />
      <LinesEditDialog
        open={linesOpen} onOpenChange={setLinesOpen}
        claimId={id} lines={lines} diagnoses={diagnoses} onSaved={refresh}
      />
    </div>
  );
}

function Field({ label, value }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </div>
      <div className="mt-0.5 font-medium">{value}</div>
    </div>
  );
}

function ValidationPanel({ errors, warnings, lastRunAt }) {
  const hasAny = errors.length + warnings.length > 0;
  return (
    <section
      data-testid="validation-panel"
      className="rounded-sm border border-border bg-card p-6"
    >
      <header className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-muted-foreground" />
          <h2 className="font-display text-lg font-medium tracking-tight">
            Scrubber results
          </h2>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {lastRunAt ? `Last run ${lastRunAt}` : "Not yet validated"}
        </div>
      </header>

      {!hasAny && lastRunAt && (
        <div
          data-testid="validation-clean"
          className="flex items-center gap-2 rounded-sm border border-success/30 bg-success-soft p-3 text-sm text-success"
        >
          <CheckCircle2 className="h-4 w-4" />
          Passed with no findings — ready to submit.
        </div>
      )}
      {!lastRunAt && (
        <p className="text-sm text-muted-foreground">
          Click "Run scrubber" to validate this claim.
        </p>
      )}

      {errors.length > 0 && (
        <ul
          data-testid="validation-errors"
          className="space-y-2 rounded-sm border border-destructive/30 bg-destructive/5 p-3"
        >
          {errors.map((f, i) => (
            <li
              key={i}
              data-testid={`validation-error-${f.code}`}
              className="flex items-start gap-2 text-sm text-destructive"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 flex-none" />
              <div>
                <span className="font-semibold">{f.code}</span>
                <span className="mx-1">·</span>
                <span>{f.message}</span>
                {f.entity_path && (
                  <span className="ml-2 text-xs font-mono text-muted-foreground">
                    {f.entity_path}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {warnings.length > 0 && (
        <ul
          data-testid="validation-warnings"
          className="mt-3 space-y-2 rounded-sm border border-warning/30 bg-warning-soft p-3"
        >
          {warnings.map((f, i) => (
            <li key={i} className="flex items-start gap-2 text-sm text-warning">
              <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
              <div>
                <span className="font-semibold">{f.code}</span>
                <span className="mx-1">·</span>
                <span>{f.message}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Edit dialogs
// ---------------------------------------------------------------------------
function HeaderEditDialog({ open, onOpenChange, claim, onSaved }) {
  const [form, setForm] = useState(() => ({ ...claim }));
  const [saving, setSaving] = useState(false);

  useEffect(() => { if (open) setForm({ ...claim }); }, [open, claim]);

  function patch(k, v) { setForm((f) => ({ ...f, [k]: v })); }

  async function onSubmit() {
    setSaving(true);
    try {
      await updateClaimHeader(claim.id, {
        claim_type: form.claim_type,
        place_of_service: form.place_of_service,
        frequency_code: form.frequency_code,
        billing_provider_id: form.billing_provider_id || null,
        rendering_provider_id: form.rendering_provider_id || null,
        facility_id: form.facility_id || null,
        authorization_number: form.authorization_number || null,
        referral_number: form.referral_number || null,
        notes: form.notes || null,
      });
      toast.success("Header saved");
      onOpenChange(false);
      onSaved?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Save failed");
    } finally { setSaving(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="claim-header-dialog"
        className="max-h-[85vh] overflow-y-auto rounded-sm sm:max-w-2xl"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Edit header</DialogTitle>
          <DialogDescription>
            Update provider IDs, POS code, authorization numbers.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="ch-type">Claim type</Label>
            <Select value={form.claim_type} onValueChange={(v) => patch("claim_type", v)}>
              <SelectTrigger id="ch-type"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="professional">Professional</SelectItem>
                <SelectItem value="institutional">Institutional</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="ch-pos">Place of service</Label>
            <Input id="ch-pos" data-testid="ch-pos"
                   value={form.place_of_service || ""}
                   onChange={(e) => patch("place_of_service", e.target.value)} />
          </div>
          <div>
            <Label htmlFor="ch-bp">Billing provider ID</Label>
            <Input id="ch-bp" data-testid="ch-billing-provider"
                   value={form.billing_provider_id || ""}
                   onChange={(e) => patch("billing_provider_id", e.target.value)} />
          </div>
          <div>
            <Label htmlFor="ch-rp">Rendering provider ID</Label>
            <Input id="ch-rp" data-testid="ch-rendering-provider"
                   value={form.rendering_provider_id || ""}
                   onChange={(e) => patch("rendering_provider_id", e.target.value)} />
          </div>
          <div>
            <Label htmlFor="ch-fac">Facility ID</Label>
            <Input id="ch-fac"
                   value={form.facility_id || ""}
                   onChange={(e) => patch("facility_id", e.target.value)} />
          </div>
          <div>
            <Label htmlFor="ch-freq">Frequency code</Label>
            <Input id="ch-freq"
                   value={form.frequency_code || ""}
                   onChange={(e) => patch("frequency_code", e.target.value)} />
          </div>
          <div>
            <Label htmlFor="ch-auth">Authorization #</Label>
            <Input id="ch-auth"
                   value={form.authorization_number || ""}
                   onChange={(e) => patch("authorization_number", e.target.value)} />
          </div>
          <div>
            <Label htmlFor="ch-ref">Referral #</Label>
            <Input id="ch-ref"
                   value={form.referral_number || ""}
                   onChange={(e) => patch("referral_number", e.target.value)} />
          </div>
          <div className="sm:col-span-2">
            <Label htmlFor="ch-notes">Notes</Label>
            <Textarea id="ch-notes" rows={2}
                      value={form.notes || ""}
                      onChange={(e) => patch("notes", e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            onClick={onSubmit} disabled={saving}
            data-testid="ch-save" className="rounded-sm"
          >
            {saving ? "Saving…" : "Save header"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DiagnosesEditDialog({ open, onOpenChange, claimId, diagnoses, onSaved }) {
  const [rows, setRows] = useState([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) setRows(diagnoses.length
      ? diagnoses.map((d) => ({ sequence: d.sequence, code: d.code || "" }))
      : [{ sequence: 1, code: "" }]);
  }, [open, diagnoses]);

  function add() { setRows([...rows, { sequence: rows.length + 1, code: "" }]); }
  function remove(i) {
    setRows(rows.filter((_, idx) => idx !== i)
      .map((r, idx) => ({ ...r, sequence: idx + 1 })));
  }
  function update(i, v) { setRows(rows.map((r, idx) => idx === i ? { ...r, code: v } : r)); }

  async function onSubmit() {
    const clean = rows
      .filter((r) => (r.code || "").trim())
      .map((r, i) => ({ sequence: i + 1, code: r.code.trim() }));
    if (clean.length === 0) return toast.error("At least one diagnosis required");
    setSaving(true);
    try {
      await replaceClaimDiagnoses(claimId, clean);
      toast.success(`Saved ${clean.length} diagnosis${clean.length === 1 ? "" : "es"}`);
      onOpenChange(false);
      onSaved?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Save failed");
    } finally { setSaving(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="claim-diagnoses-dialog"
        className="rounded-sm sm:max-w-lg"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Edit diagnoses</DialogTitle>
          <DialogDescription>
            Up to 12 ICD-10 codes. Sequence drives line diagnosis pointers.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          {rows.map((r, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="w-8 text-xs text-muted-foreground">#{r.sequence}</span>
              <Input
                value={r.code}
                data-testid={`cdx-${i}`}
                onChange={(e) => update(i, e.target.value)}
                placeholder="e.g. M54.16"
              />
              <Button variant="ghost" size="sm" onClick={() => remove(i)}>×</Button>
            </div>
          ))}
        </div>
        <Button variant="outline" size="sm" onClick={add}
                data-testid="cdx-add" className="w-fit">+ Add</Button>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            onClick={onSubmit} disabled={saving}
            data-testid="cdx-save" className="rounded-sm"
          >
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function LinesEditDialog({ open, onOpenChange, claimId, lines, diagnoses, onSaved }) {
  const [rows, setRows] = useState([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) setRows(lines.length
      ? lines.map((ln) => ({
        sequence: ln.sequence,
        service_date: ln.service_date || "",
        code_type: ln.code_type || "cpt",
        code: ln.code || "",
        units: ln.units || 1,
        billed_cents: ln.billed_cents || 0,
        diagnosis_pointers: ln.diagnosis_pointers || [],
        modifiers: ln.modifiers || [],
      }))
      : [{
        sequence: 1, service_date: "", code_type: "cpt", code: "",
        units: 1, billed_cents: 0, diagnosis_pointers: [], modifiers: [],
      }]);
  }, [open, lines]);

  const diagPointerOptions = useMemo(
    () => diagnoses.map((d) => d.sequence),
    [diagnoses],
  );

  function add() {
    setRows([...rows, {
      sequence: rows.length + 1, service_date: "", code_type: "cpt",
      code: "", units: 1, billed_cents: 0,
      diagnosis_pointers: [], modifiers: [],
    }]);
  }
  function remove(i) {
    setRows(rows.filter((_, idx) => idx !== i).map((r, idx) => ({ ...r, sequence: idx + 1 })));
  }
  function update(i, patch) { setRows(rows.map((r, idx) => idx === i ? { ...r, ...patch } : r)); }

  async function onSubmit() {
    const clean = rows
      .filter((r) => (r.code || "").trim())
      .map((r, i) => ({
        sequence: i + 1,
        service_date: r.service_date,
        code_type: r.code_type,
        code: r.code.trim(),
        units: Math.max(1, Number(r.units) || 1),
        billed_cents: Number(r.billed_cents) || 0,
        diagnosis_pointers: (r.diagnosis_pointers || []).map(Number),
        modifiers: (r.modifiers || []).filter(Boolean),
      }));
    if (clean.length === 0) return toast.error("At least one line required");
    setSaving(true);
    try {
      await replaceClaimLines(claimId, clean);
      toast.success(`Saved ${clean.length} line${clean.length === 1 ? "" : "s"}`);
      onOpenChange(false);
      onSaved?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Save failed");
    } finally { setSaving(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="claim-lines-dialog"
        className="max-h-[90vh] overflow-y-auto rounded-sm sm:max-w-4xl"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Edit service lines</DialogTitle>
          <DialogDescription>
            Each line must point to at least one diagnosis.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {rows.map((r, i) => (
            <div
              key={i}
              data-testid={`cl-row-${i}`}
              className="grid grid-cols-12 gap-2 border-b border-border pb-2"
            >
              <div className="col-span-1 text-xs font-semibold text-muted-foreground pt-3">
                #{r.sequence}
              </div>
              <Input
                className="col-span-2" placeholder="Code"
                value={r.code}
                data-testid={`cl-code-${i}`}
                onChange={(e) => update(i, { code: e.target.value })}
              />
              <Input
                className="col-span-2" type="date"
                value={r.service_date}
                onChange={(e) => update(i, { service_date: e.target.value })}
              />
              <Input
                className="col-span-1" type="number" min="1"
                value={r.units}
                onChange={(e) => update(i, { units: Number(e.target.value) || 1 })}
              />
              <Input
                className="col-span-2" placeholder="Billed ($)"
                value={r.billed_cents ? (r.billed_cents / 100).toFixed(2) : ""}
                data-testid={`cl-billed-${i}`}
                onChange={(e) => update(i, {
                  billed_cents: parseDollarsToCents(e.target.value) || 0,
                })}
              />
              <Input
                className="col-span-2" placeholder="Dx ptrs (1,2)"
                value={(r.diagnosis_pointers || []).join(",")}
                data-testid={`cl-dxp-${i}`}
                onChange={(e) => {
                  const ptrs = e.target.value.split(",")
                    .map((x) => parseInt(x.trim(), 10))
                    .filter((n) => !Number.isNaN(n));
                  update(i, { diagnosis_pointers: ptrs });
                }}
              />
              <Input
                className="col-span-1" placeholder="-25"
                value={(r.modifiers || []).join(",")}
                onChange={(e) => update(i, {
                  modifiers: e.target.value.split(",").map((x) => x.trim()).filter(Boolean),
                })}
              />
              <Button variant="ghost" size="sm"
                      onClick={() => remove(i)}
                      className="col-span-1 text-destructive">
                ×
              </Button>
            </div>
          ))}
        </div>
        <Button variant="outline" size="sm" onClick={add}
                data-testid="cl-add" className="w-fit">+ Add line</Button>
        {diagPointerOptions.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Available diagnosis sequences: {diagPointerOptions.join(", ")}
          </p>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            onClick={onSubmit} disabled={saving}
            data-testid="cl-save" className="rounded-sm"
          >
            {saving ? "Saving…" : "Save lines"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
