import { useEffect, useState } from "react";
import { Sparkles, AlertTriangle } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../../components/ui/dialog";
import { Button } from "../../../components/ui/button";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { Textarea } from "../../../components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../../components/ui/select";
import { listSavedCards, createSchedule } from "./api";
import { formatCents, parseDollarsToCents } from "../../../utils/money";

const FREQUENCIES = [
  { v: "weekly", l: "Weekly" },
  { v: "biweekly", l: "Bi-weekly" },
  { v: "monthly", l: "Monthly" },
];

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Treatment-plan-aware "Link auto-pay" dialog. Pre-fills the
 * scheduler form with `kind=treatment_plan` and the supplied
 * treatment_plan_id. The clinician supplies the total dollar amount
 * (the system can't infer this — chiro plans are negotiated visit-pack
 * pricing) and confirms the visit count.
 */
export default function LinkAutoPayDialog({
  open, onClose, patientId, treatmentPlanId,
  defaultNumCharges, defaultLabel,
}) {
  const [cards, setCards] = useState([]);
  const [loadingCards, setLoadingCards] = useState(true);
  const [form, setForm] = useState({
    label: defaultLabel || "Treatment plan auto-pay",
    card_token_id: "",
    total_str: "",
    num_charges: defaultNumCharges || 12,
    frequency: "weekly",
    start_at: todayIso(),
    notes: "",
  });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || !patientId) return;
    setLoadingCards(true);
    listSavedCards(patientId)
      .then((rows) => {
        setCards(rows);
        if (rows.length) {
          const def = rows.find((c) => c.is_default) || rows[0];
          setForm((f) => f.card_token_id ? f : { ...f, card_token_id: def.id });
        }
      })
      .catch(() => setCards([]))
      .finally(() => setLoadingCards(false));
  }, [open, patientId]);

  // Re-default the label when the parent's `defaultLabel` changes (rare).
  useEffect(() => {
    if (defaultLabel && !form.label) setForm((f) => ({ ...f, label: defaultLabel }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultLabel]);

  const cents = parseDollarsToCents(form.total_str);
  const perCharge = cents && form.num_charges
    ? Math.floor(cents / form.num_charges)
    : 0;
  const lastCharge = cents && form.num_charges
    ? cents - perCharge * (form.num_charges - 1)
    : 0;

  const submit = async () => {
    if (!form.label || !form.card_token_id || !cents || !form.num_charges) {
      toast.error("Label, saved card, total and number of charges are required.");
      return;
    }
    setBusy(true);
    try {
      await createSchedule({
        patient_id: patientId,
        card_token_id: form.card_token_id,
        kind: "treatment_plan",
        treatment_plan_id: treatmentPlanId,
        label: form.label,
        total_cents: cents,
        num_charges: Number(form.num_charges),
        frequency: form.frequency,
        start_at: form.start_at,
        notes: form.notes || null,
      });
      toast.success("Auto-pay linked to treatment plan. First charge runs on the start date.");
      onClose();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to link auto-pay.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="link-autopay-dialog" className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" /> Link auto-pay
          </DialogTitle>
          <DialogDescription>
            Charge the patient&apos;s saved card on a recurring schedule for this treatment plan.
          </DialogDescription>
        </DialogHeader>

        {loadingCards ? (
          <div className="text-sm text-muted-foreground">Loading saved cards…</div>
        ) : cards.length === 0 ? (
          <div data-testid="link-autopay-no-card"
               className="rounded-sm border border-warning/40 bg-warning-soft p-3 text-sm text-warning flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
            <div>
              No saved card on file for this patient. Take a payment via Helcim with{" "}
              <span className="font-medium">Save card on file</span> ticked, or visit the patient
              ledger to register a card first.
              <div className="mt-2">
                <Button asChild size="sm" variant="outline">
                  <Link to={`/billing/patients/${patientId}/ledger`}>
                    Open patient ledger
                  </Link>
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div>
              <Label>Saved card</Label>
              <Select value={form.card_token_id}
                      onValueChange={(v) => setForm({ ...form, card_token_id: v })}>
                <SelectTrigger data-testid="autopay-card"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {cards.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.brand || "Card"} ****{c.last4} {c.is_default ? "· default" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Label</Label>
              <Input data-testid="autopay-label" value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                placeholder="e.g. Lower-back course of care" />
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <Label>Total</Label>
                <Input data-testid="autopay-total" value={form.total_str}
                  onChange={(e) => setForm({ ...form, total_str: e.target.value })}
                  placeholder="0.00" />
              </div>
              <div>
                <Label># charges</Label>
                <Input data-testid="autopay-num-charges" type="number" min={1} max={120}
                  value={form.num_charges}
                  onChange={(e) => setForm({ ...form, num_charges: Number(e.target.value) || 1 })} />
              </div>
              <div>
                <Label>Cadence</Label>
                <Select value={form.frequency}
                        onValueChange={(v) => setForm({ ...form, frequency: v })}>
                  <SelectTrigger data-testid="autopay-frequency"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {FREQUENCIES.map((f) => <SelectItem key={f.v} value={f.v}>{f.l}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label>First charge date</Label>
              <Input data-testid="autopay-start" type="date" value={form.start_at}
                onChange={(e) => setForm({ ...form, start_at: e.target.value })} />
            </div>
            <div>
              <Label>Notes (optional)</Label>
              <Textarea data-testid="autopay-notes" rows={2} value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </div>
            {cents > 0 && form.num_charges > 0 && (
              <div data-testid="autopay-summary"
                   className="rounded-sm border border-dashed border-border bg-muted/30 p-2.5 text-xs text-muted-foreground">
                {form.num_charges - 1} × {formatCents(perCharge)} + 1 × {formatCents(lastCharge)} = {formatCents(cents)} total
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="autopay-cancel">Cancel</Button>
          {cards.length > 0 && (
            <Button onClick={submit} disabled={busy} data-testid="autopay-save">
              {busy ? "Linking…" : "Link auto-pay"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
