import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Coins, Plus, Receipt, Send, Trash2 } from "lucide-react";
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
import { formatCents } from "../../utils/money";
import {
  captureEncounter,
  previewChargeCandidates,
  signRecord,
  updateRecordCoding,
} from "./useBillingAdmin";

const RESPONSIBILITY = [
  { v: "self_pay", l: "Self-pay" },
  { v: "insurance", l: "Insurance" },
  { v: "mixed", l: "Mixed" },
];

const MODIFIER_OPTIONS = ["25", "59", "GA", "GP", "GY", "GZ"];

/**
 * ChargeCaptureDialog — attach procedures + diagnoses to an encounter,
 * preview the generated invoice, then capture.
 *
 * Flow:
 *   1. Operator edits procedures / diagnoses / responsibility.
 *   2. "Save coding" persists (only if record is unsigned).
 *   3. "Sign record" locks coding and enables capture.
 *   4. "Capture charges" calls the capture endpoint and shows the
 *      resulting invoice link.
 */
export default function ChargeCaptureDialog({
  open, onOpenChange, record, patientId, onUpdated,
}) {
  const [procedures, setProcedures] = useState([]);
  const [diagnoses, setDiagnoses] = useState([]);
  const [responsibility, setResponsibility] = useState("self_pay");
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const isSigned = !!record?.signed_at;
  const isCaptured = record?.charge_status === "captured";

  useEffect(() => {
    if (!open || !record) return;
    setProcedures(record.procedures || []);
    setDiagnoses(record.diagnoses || []);
    setResponsibility(record.responsibility || "self_pay");
    setPreview(null);
  }, [open, record]);

  const refreshPreview = useCallback(async () => {
    if (!record) return;
    setLoading(true);
    try {
      const data = await previewChargeCandidates(record.id);
      setPreview(data);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not preview charges");
    } finally {
      setLoading(false);
    }
  }, [record]);

  useEffect(() => {
    // Auto-preview whenever the dialog opens with a coded record.
    if (open && record?.procedures?.length) refreshPreview();
  }, [open, record, refreshPreview]);

  function addProcedure() {
    setProcedures([...procedures, {
      code_type: "cpt", code: "", units: 1, modifiers: [],
    }]);
  }

  function updateProcedure(i, patch) {
    setProcedures(procedures.map((p, idx) => idx === i ? { ...p, ...patch } : p));
  }

  function removeProcedure(i) {
    setProcedures(procedures.filter((_, idx) => idx !== i));
  }

  function addDiagnosis() {
    setDiagnoses([...diagnoses, {
      sequence: diagnoses.length + 1, code: "",
    }]);
  }

  function updateDiagnosis(i, patch) {
    setDiagnoses(diagnoses.map((d, idx) => idx === i ? { ...d, ...patch } : d));
  }

  function removeDiagnosis(i) {
    setDiagnoses(diagnoses.filter((_, idx) => idx !== i)
      .map((d, idx) => ({ ...d, sequence: idx + 1 })));
  }

  async function onSaveCoding() {
    if (isSigned) {
      toast.error("Record is signed; unsign to edit coding");
      return;
    }
    const clean = procedures
      .filter((p) => (p.code || "").trim())
      .map((p) => ({
        code_type: p.code_type || "cpt",
        code: p.code.trim(),
        units: Math.max(1, Number(p.units) || 1),
        modifiers: (p.modifiers || []).filter(Boolean),
      }));
    const cleanDx = diagnoses
      .filter((d) => (d.code || "").trim())
      .map((d, i) => ({ sequence: i + 1, code: d.code.trim() }));
    setSaving(true);
    try {
      await updateRecordCoding(patientId, record.id, {
        procedures: clean, diagnoses: cleanDx,
        responsibility,
      });
      toast.success("Coding saved");
      await onUpdated?.();
      await refreshPreview();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not save coding");
    } finally {
      setSaving(false);
    }
  }

  async function onSign() {
    setSaving(true);
    try {
      await signRecord(patientId, record.id);
      toast.success("Record signed");
      await onUpdated?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not sign record");
    } finally {
      setSaving(false);
    }
  }

  async function onCapture() {
    setSaving(true);
    try {
      const inv = await captureEncounter(record.id);
      toast.success(`Captured as invoice ${inv.id.slice(0, 8)}`);
      await onUpdated?.();
      onOpenChange(false);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Capture failed");
    } finally {
      setSaving(false);
    }
  }

  const canCapture = useMemo(
    () => isSigned && !isCaptured && preview?.can_capture,
    [isSigned, isCaptured, preview],
  );

  if (!record) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="charge-capture-dialog"
        className="max-h-[90vh] overflow-y-auto rounded-sm sm:max-w-3xl"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            Charge capture · {record.title}
          </DialogTitle>
          <DialogDescription>
            Code the encounter, sign, then capture charges. Pricing is
            resolved from the active fee schedules at capture time.
          </DialogDescription>
        </DialogHeader>

        {/* Status strip */}
        <div className="flex flex-wrap gap-2 text-xs">
          <Chip tone={isSigned ? "success" : "muted"}>
            {isSigned ? "Signed" : "Draft"}
          </Chip>
          <Chip tone={isCaptured ? "success" : "muted"}>
            {isCaptured ? "Captured" : "Not captured"}
          </Chip>
        </div>

        {/* Coding editor */}
        <section className="mt-2 space-y-3 rounded-sm border border-border p-4">
          <header className="flex items-center justify-between">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Coding
            </h3>
            <div className="flex items-center gap-2">
              <Label className="text-xs text-muted-foreground">Responsibility</Label>
              <Select
                value={responsibility}
                onValueChange={setResponsibility}
                disabled={isSigned}
              >
                <SelectTrigger
                  data-testid="cc-responsibility" className="w-36 h-8"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RESPONSIBILITY.map((r) => (
                    <SelectItem key={r.v} value={r.v}>{r.l}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </header>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <Label className="text-sm">Procedures (CPT)</Label>
              {!isSigned && (
                <Button
                  variant="ghost" size="sm" onClick={addProcedure}
                  data-testid="cc-add-procedure"
                >
                  <Plus className="mr-1 h-3.5 w-3.5" /> Add
                </Button>
              )}
            </div>
            {procedures.length === 0 && (
              <p className="text-xs text-muted-foreground">No procedures yet.</p>
            )}
            {procedures.map((p, i) => (
              <div
                key={i}
                data-testid={`cc-proc-row-${i}`}
                className="mb-2 grid grid-cols-12 gap-2"
              >
                <Input
                  className="col-span-3" placeholder="CPT code"
                  value={p.code}
                  onChange={(e) => updateProcedure(i, { code: e.target.value })}
                  disabled={isSigned}
                  data-testid={`cc-proc-code-${i}`}
                />
                <Input
                  className="col-span-2" type="number" min="1" max="99"
                  placeholder="Units"
                  value={p.units}
                  onChange={(e) => updateProcedure(i, {
                    units: Number(e.target.value) || 1,
                  })}
                  disabled={isSigned}
                />
                <Select
                  value={(p.modifiers?.[0]) || "none"}
                  onValueChange={(v) => updateProcedure(i, {
                    modifiers: v === "none" ? [] : [v],
                  })}
                  disabled={isSigned}
                >
                  <SelectTrigger className="col-span-3 h-9">
                    <SelectValue placeholder="Modifier" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">No modifier</SelectItem>
                    {MODIFIER_OPTIONS.map((m) => (
                      <SelectItem key={m} value={m}>-{m}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <div className="col-span-4 flex items-center gap-1">
                  {!isSigned && (
                    <Button
                      variant="ghost" size="sm"
                      onClick={() => removeProcedure(i)}
                      className="text-destructive hover:bg-destructive/10"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <Label className="text-sm">Diagnoses (ICD-10)</Label>
              {!isSigned && (
                <Button
                  variant="ghost" size="sm" onClick={addDiagnosis}
                  data-testid="cc-add-diagnosis"
                >
                  <Plus className="mr-1 h-3.5 w-3.5" /> Add
                </Button>
              )}
            </div>
            {diagnoses.map((d, i) => (
              <div key={i} className="mb-2 flex items-center gap-2">
                <span className="w-8 text-xs text-muted-foreground">#{d.sequence}</span>
                <Input
                  className="max-w-xs"
                  placeholder="e.g. M54.16"
                  value={d.code}
                  onChange={(e) => updateDiagnosis(i, { code: e.target.value })}
                  disabled={isSigned}
                  data-testid={`cc-dx-${i}`}
                />
                {!isSigned && (
                  <Button
                    variant="ghost" size="sm"
                    onClick={() => removeDiagnosis(i)}
                    className="text-destructive hover:bg-destructive/10"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* Preview panel */}
        <section
          data-testid="cc-preview"
          className="rounded-sm border border-border p-4"
        >
          <header className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Charge preview
            </h3>
            <Button
              size="sm" variant="outline" onClick={refreshPreview}
              disabled={loading || !procedures.length}
              data-testid="cc-preview-refresh"
            >
              <Coins className="mr-1 h-3.5 w-3.5" />
              {loading ? "Pricing…" : "Refresh preview"}
            </Button>
          </header>
          {preview ? (
            <>
              {preview.warnings?.length > 0 && (
                <ul className="mb-3 rounded-sm border border-warning/30 bg-warning-soft p-2 text-xs text-warning">
                  {preview.warnings.map((w, i) => (
                    <li key={i}>⚠︎ {w}</li>
                  ))}
                </ul>
              )}
              <table className="w-full text-sm">
                <thead className="text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3">Code</th>
                    <th className="py-1 pr-3">Description</th>
                    <th className="py-1 pr-3 text-right">Qty</th>
                    <th className="py-1 pr-3 text-right">Unit</th>
                    <th className="py-1 pr-3 text-right">Total</th>
                    <th className="py-1">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.lines.map((ln, i) => (
                    <tr key={i} className="border-t border-border">
                      <td className="py-1.5 pr-3 font-medium tabular-nums">{ln.code}</td>
                      <td className="py-1.5 pr-3 text-muted-foreground">{ln.description}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{ln.quantity}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{formatCents(ln.unit_price_cents)}</td>
                      <td className="py-1.5 pr-3 text-right font-medium tabular-nums">{formatCents(ln.total_cents)}</td>
                      <td className="py-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">{ln.price_source.replaceAll("_", " ")}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-border">
                    <td className="py-2" colSpan={4}></td>
                    <td
                      data-testid="cc-preview-total"
                      className="py-2 text-right font-display text-lg font-medium tabular-nums"
                    >
                      {formatCents(preview.total_cents)}
                    </td>
                    <td></td>
                  </tr>
                </tfoot>
              </table>
            </>
          ) : (
            <p className="text-xs text-muted-foreground">
              Add procedures and refresh to see the preview.
            </p>
          )}
        </section>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Close</Button>
          {!isSigned && (
            <Button
              variant="outline"
              onClick={onSaveCoding}
              disabled={saving || isCaptured}
              data-testid="cc-save-coding"
            >
              Save coding
            </Button>
          )}
          {!isSigned && (
            <Button
              variant="outline"
              onClick={onSign}
              disabled={saving || !procedures.length}
              data-testid="cc-sign"
            >
              <Send className="mr-2 h-4 w-4" /> Sign record
            </Button>
          )}
          <Button
            onClick={onCapture}
            disabled={saving || !canCapture}
            data-testid="cc-capture"
            className="rounded-sm"
          >
            <Receipt className="mr-2 h-4 w-4" />
            {isCaptured ? "Captured" : "Capture charges"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Chip({ tone, children }) {
  const toneClass =
    tone === "success" ? "bg-success-soft text-success"
      : tone === "warn" ? "bg-warning-soft text-warning"
      : "bg-muted text-muted-foreground";
  return (
    <span className={`inline-flex items-center rounded-sm px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${toneClass}`}>
      {children}
    </span>
  );
}
