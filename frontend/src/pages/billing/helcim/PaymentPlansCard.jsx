import { useCallback, useEffect, useState } from "react";
import { CalendarClock, History, Pause, Play, Plus, Sparkles, X } from "lucide-react";
import { toast } from "sonner";
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
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "../../../components/ui/dialog";
import {
  listSchedules,
  createSchedule,
  changeScheduleStatus,
  fetchScheduleRuns,
  runScheduleNow,
  listSavedCards,
} from "./api";
import { formatCents, parseDollarsToCents } from "../../../utils/money";
import { formatDateTime } from "../../../utils/time";

const FREQUENCIES = [
  { v: "weekly", l: "Weekly" },
  { v: "biweekly", l: "Bi-weekly" },
  { v: "monthly", l: "Monthly" },
];

const KINDS = [
  { v: "payment_plan", l: "Payment plan" },
  { v: "treatment_plan", l: "Treatment plan auto-pay" },
  { v: "statement_autopay", l: "Statement auto-pay" },
];

const STATUS_TONE = {
  active: "bg-primary/10 text-primary",
  paused: "bg-warning-soft text-warning",
  completed: "bg-muted text-muted-foreground",
  cancelled: "bg-muted text-muted-foreground",
  failed: "bg-destructive-soft text-destructive",
};

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function CreatePlanDialog({ open, onClose, patientId, savedCards, invoiceId, onCreated }) {
  const [form, setForm] = useState({
    label: "", kind: "payment_plan", card_token_id: "",
    total_str: "", num_charges: 4, frequency: "monthly",
    start_at: todayIso(), notes: "",
  });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (savedCards?.length) {
      const def = savedCards.find((c) => c.is_default) || savedCards[0];
      setForm((f) => f.card_token_id ? f : { ...f, card_token_id: def.id });
    }
  }, [savedCards]);

  const cents = parseDollarsToCents(form.total_str);
  const perCharge = cents && form.num_charges
    ? Math.floor(cents / form.num_charges)
    : 0;
  const lastCharge = cents && form.num_charges
    ? cents - perCharge * (form.num_charges - 1)
    : 0;

  const submit = async () => {
    if (!form.label || !form.card_token_id || !cents || !form.num_charges) {
      toast.error("Label, card, total and number of charges are required.");
      return;
    }
    setBusy(true);
    try {
      await createSchedule({
        patient_id: patientId,
        card_token_id: form.card_token_id,
        kind: form.kind,
        label: form.label,
        invoice_id: invoiceId || null,
        total_cents: cents,
        num_charges: Number(form.num_charges),
        frequency: form.frequency,
        start_at: form.start_at,
        notes: form.notes || null,
      });
      toast.success("Payment plan created. The first charge will run on the start date.");
      onCreated?.();
      onClose();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to create plan.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="plan-create-dialog" className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">New payment plan</DialogTitle>
          <DialogDescription>
            Helcim Customer Vault token will be charged on the cadence below.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Plan type</Label>
              <Select value={form.kind} onValueChange={(v) => setForm({ ...form, kind: v })}>
                <SelectTrigger data-testid="plan-kind"><SelectValue /></SelectTrigger>
                <SelectContent>{KINDS.map((k) => <SelectItem key={k.v} value={k.v}>{k.l}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div>
              <Label>Saved card</Label>
              <Select value={form.card_token_id}
                      onValueChange={(v) => setForm({ ...form, card_token_id: v })}>
                <SelectTrigger data-testid="plan-card"><SelectValue placeholder="Pick a saved card" /></SelectTrigger>
                <SelectContent>
                  {(savedCards || []).map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.brand || "Card"} ****{c.last4} {c.is_default ? "· default" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div>
            <Label htmlFor="plan-label">Label</Label>
            <Input id="plan-label" data-testid="plan-label" value={form.label}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              placeholder="e.g. Lower-back course of care" />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label>Total</Label>
              <Input data-testid="plan-total" value={form.total_str}
                onChange={(e) => setForm({ ...form, total_str: e.target.value })}
                placeholder="0.00" />
            </div>
            <div>
              <Label># charges</Label>
              <Input data-testid="plan-num-charges" type="number" min={1} max={120}
                value={form.num_charges}
                onChange={(e) => setForm({ ...form, num_charges: Number(e.target.value) || 1 })} />
            </div>
            <div>
              <Label>Frequency</Label>
              <Select value={form.frequency}
                      onValueChange={(v) => setForm({ ...form, frequency: v })}>
                <SelectTrigger data-testid="plan-frequency"><SelectValue /></SelectTrigger>
                <SelectContent>{FREQUENCIES.map((f) => <SelectItem key={f.v} value={f.v}>{f.l}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </div>
          <div>
            <Label htmlFor="plan-start">First charge date</Label>
            <Input id="plan-start" data-testid="plan-start" type="date"
              value={form.start_at}
              onChange={(e) => setForm({ ...form, start_at: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="plan-notes">Notes (optional)</Label>
            <Textarea id="plan-notes" data-testid="plan-notes" rows={2}
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </div>
          {cents > 0 && form.num_charges > 0 && (
            <div data-testid="plan-summary" className="rounded-sm border border-dashed border-border bg-muted/30 p-2.5 text-xs text-muted-foreground">
              {form.num_charges - 1} × {formatCents(perCharge)} + 1 × {formatCents(lastCharge)} = {formatCents(cents)} total
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="plan-cancel">Cancel</Button>
          <Button onClick={submit} disabled={busy} data-testid="plan-save">
            {busy ? "Saving…" : "Create plan"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RunsDialog({ open, onClose, schedule }) {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!open || !schedule?.id) return;
    setLoading(true);
    fetchScheduleRuns(schedule.id)
      .then(setRuns)
      .catch(() => setRuns([]))
      .finally(() => setLoading(false));
  }, [open, schedule]);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="plan-runs-dialog" className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-display">Charge history — {schedule?.label}</DialogTitle>
        </DialogHeader>
        {loading ? <div className="text-sm text-muted-foreground">Loading…</div> :
          runs.length === 0 ? (
            <div data-testid="plan-runs-empty" className="rounded-sm border border-dashed border-border bg-card/50 px-4 py-6 text-center text-sm text-muted-foreground">
              No charges attempted yet.
            </div>
          ) : (
            <ul className="max-h-[60vh] space-y-2 overflow-y-auto">
              {runs.map((r) => (
                <li key={r.id} data-testid={`plan-run-row-${r.id}`}
                    className="rounded-sm border border-border bg-card px-3 py-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className={`rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
                      r.outcome === "success" ? "bg-primary/10 text-primary"
                        : r.outcome === "declined" ? "bg-warning-soft text-warning"
                        : "bg-destructive-soft text-destructive"
                    }`}>{r.outcome}</span>
                    <span className="text-muted-foreground">{formatDateTime(r.attempted_at)}</span>
                  </div>
                  <div className="mt-1 text-foreground">
                    {formatCents(r.amount_cents)}
                    {r.helcim_transaction_id && ` · txn ${r.helcim_transaction_id}`}
                  </div>
                  {r.error && (
                    <div className="mt-0.5 text-destructive">{r.error}</div>
                  )}
                </li>
              ))}
            </ul>
          )
        }
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function PaymentPlansCard({ patientId, invoiceId }) {
  const [schedules, setSchedules] = useState([]);
  const [savedCards, setSavedCards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [runsFor, setRunsFor] = useState(null);

  const load = useCallback(async () => {
    if (!patientId) return;
    setLoading(true);
    try {
      const [s, c] = await Promise.all([
        listSchedules({ patient_id: patientId }),
        listSavedCards(patientId),
      ]);
      setSchedules(s);
      setSavedCards(c);
    } catch (_) {
      // best-effort
    } finally {
      setLoading(false);
    }
  }, [patientId]);

  useEffect(() => { load(); }, [load]);

  const transition = async (sched, status) => {
    try {
      await changeScheduleStatus(sched.id, status);
      toast.success(`Schedule ${status}.`);
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not change status.");
    }
  };

  const runNow = async (sched) => {
    if (!confirm(`Run charge ${sched.charges_completed + 1}/${sched.num_charges} for ${sched.label} now?`)) return;
    try {
      const res = await runScheduleNow(sched.id);
      if (res.outcome === "success") toast.success(`Charged ${formatCents(res.amount_cents)} (txn ${res.transaction_id}).`);
      else toast.error(`Charge ${res.outcome}: ${res.error || ""}`);
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to run.");
    }
  };

  return (
    <section
      data-testid="payment-plans-card"
      className="rounded-sm border border-border bg-card p-5 space-y-4"
    >
      <div className="flex items-center justify-between">
        <h2 className="font-display text-base font-medium flex items-center gap-2">
          <CalendarClock className="h-4 w-4 text-primary" />
          Payment plans &amp; auto-pay
          {schedules.length > 0 && (
            <span className="ml-1 text-xs font-normal text-muted-foreground">({schedules.length})</span>
          )}
        </h2>
        <Button
          data-testid="plan-add-btn"
          size="sm"
          onClick={() => setCreateOpen(true)}
          disabled={savedCards.length === 0}
          className="gap-1"
          title={savedCards.length === 0 ? "Save a card on file first." : ""}
        >
          <Plus className="h-4 w-4" /> New plan
        </Button>
      </div>

      {loading ? (
        <div className="text-sm text-muted-foreground">Loading…</div>
      ) : schedules.length === 0 ? (
        <div data-testid="plans-empty" className="rounded-sm border border-dashed border-border bg-card/50 px-4 py-6 text-center text-sm text-muted-foreground">
          {savedCards.length === 0
            ? "Save a card on file first, then you can set up a payment plan."
            : "No active plans. Click New plan to schedule recurring auto-charges."}
        </div>
      ) : (
        <ul className="space-y-2">
          {schedules.map((sc) => {
            const card = savedCards.find((c) => c.id === sc.card_token_id);
            const tone = STATUS_TONE[sc.status] || STATUS_TONE.cancelled;
            return (
              <li
                key={sc.id}
                data-testid={`plan-row-${sc.id}`}
                className="rounded-sm border border-border bg-muted/20 p-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-foreground">{sc.label}</span>
                      <span className={`rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${tone}`}
                            data-testid={`plan-status-${sc.id}`}>
                        {sc.status}
                      </span>
                      <span className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                        {sc.kind.replace(/_/g, " ")}
                      </span>
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {sc.charges_completed}/{sc.num_charges} charged · {formatCents(sc.per_charge_cents)} {sc.frequency}
                      {card ? ` · ${card.brand} ****${card.last4}` : ""}
                      {sc.next_charge_at ? ` · next ${formatDateTime(sc.next_charge_at)}` : ""}
                      {sc.consecutive_failures > 0 && (
                        <span className="ml-1 text-destructive">· {sc.consecutive_failures} failure(s)</span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      data-testid={`plan-runs-${sc.id}`}
                      variant="ghost" size="sm"
                      onClick={() => setRunsFor(sc)}
                      title="Charge history"
                    >
                      <History className="h-3.5 w-3.5" />
                    </Button>
                    {sc.status === "active" && (
                      <>
                        <Button
                          data-testid={`plan-run-now-${sc.id}`}
                          size="sm" variant="outline"
                          onClick={() => runNow(sc)}
                          className="gap-1"
                        >
                          <Sparkles className="h-3.5 w-3.5" /> Charge now
                        </Button>
                        <Button
                          data-testid={`plan-pause-${sc.id}`}
                          size="sm" variant="ghost"
                          onClick={() => transition(sc, "paused")}
                        >
                          <Pause className="h-3.5 w-3.5" />
                        </Button>
                      </>
                    )}
                    {sc.status === "paused" && (
                      <Button
                        data-testid={`plan-resume-${sc.id}`}
                        size="sm" variant="outline"
                        onClick={() => transition(sc, "active")}
                        className="gap-1"
                      >
                        <Play className="h-3.5 w-3.5" /> Resume
                      </Button>
                    )}
                    {sc.status === "failed" && (
                      <Button
                        data-testid={`plan-retry-${sc.id}`}
                        size="sm" variant="outline"
                        onClick={() => runNow(sc)}
                        className="gap-1"
                      >
                        <Sparkles className="h-3.5 w-3.5" /> Retry
                      </Button>
                    )}
                    {(sc.status === "active" || sc.status === "paused") && (
                      <Button
                        data-testid={`plan-cancel-${sc.id}`}
                        size="sm" variant="ghost"
                        className="text-destructive"
                        onClick={() => transition(sc, "cancelled")}
                        title="Cancel plan"
                      >
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <CreatePlanDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        patientId={patientId}
        invoiceId={invoiceId}
        savedCards={savedCards}
        onCreated={load}
      />
      {runsFor && (
        <RunsDialog open={!!runsFor} onClose={() => setRunsFor(null)} schedule={runsFor} />
      )}
    </section>
  );
}
