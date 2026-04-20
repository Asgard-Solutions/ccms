import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowLeft,
  Ban,
  CreditCard,
  FileMinus2,
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
import { formatDate, formatDateTime } from "../../utils/time";
import { formatCents, parseDollarsToCents } from "../../utils/money";
import {
  INVOICE_STATUS_LABELS,
  createAdjustment,
  fetchInvoice,
  fetchInvoiceLines,
  invoiceStatusTone,
  transitionInvoiceStatus,
  voidInvoice,
} from "./useBilling";
import PostPaymentDialog from "./PostPaymentDialog";

export default function InvoiceDetail() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [invoice, setInvoice] = useState(null);
  const [lines, setLines] = useState([]);
  const [loading, setLoading] = useState(true);
  const [payOpen, setPayOpen] = useState(false);
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [voidOpen, setVoidOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [inv, ln] = await Promise.all([
        fetchInvoice(id), fetchInvoiceLines(id),
      ]);
      setInvoice(inv);
      setLines(ln || []);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not load invoice");
      navigate("/billing/invoices");
    } finally {
      setLoading(false);
    }
  }, [id, navigate]);

  useEffect(() => { refresh(); }, [refresh]);

  async function onIssue() {
    try {
      await transitionInvoiceStatus(id, "issued");
      toast.success("Invoice issued");
      await refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not issue invoice");
    }
  }

  const isTerminal = useMemo(
    () => invoice && ["void", "refunded"].includes(invoice.status),
    [invoice],
  );

  if (loading || !invoice) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64 rounded-sm" />
        <Skeleton className="h-32 w-full rounded-sm" />
        <Skeleton className="h-64 w-full rounded-sm" />
      </div>
    );
  }

  return (
    <div data-testid="invoice-detail-page" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link
            to="/billing/invoices"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Invoices
          </Link>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
            Invoice <span className="tabular-nums">{invoice.id.slice(0, 8)}</span>
          </h1>
          <div className="mt-2 flex items-center gap-3 text-sm text-muted-foreground">
            <Link
              to={`/patients/${invoice.patient_id}`}
              className="hover:underline"
              data-testid="invoice-patient-link"
            >
              Patient {invoice.patient_id.slice(0, 8)}
            </Link>
            <span
              className={`rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${invoiceStatusTone(invoice.status)}`}
            >
              {INVOICE_STATUS_LABELS[invoice.status] || invoice.status}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {invoice.status === "draft" && (
            <Button
              onClick={onIssue} data-testid="invoice-issue-btn" className="rounded-sm"
            >
              <Send className="mr-2 h-4 w-4" /> Issue invoice
            </Button>
          )}
          <Button
            onClick={() => setPayOpen(true)}
            disabled={isTerminal || invoice.balance_cents === 0}
            data-testid="invoice-post-payment-btn"
            className="rounded-sm"
          >
            <CreditCard className="mr-2 h-4 w-4" /> Post payment
          </Button>
          <Button
            variant="outline"
            onClick={() => setAdjustOpen(true)}
            disabled={isTerminal}
            data-testid="invoice-adjust-btn"
            className="rounded-sm"
          >
            <FileMinus2 className="mr-2 h-4 w-4" /> Adjust / writeoff
          </Button>
          <Button
            variant="ghost"
            onClick={() => setVoidOpen(true)}
            disabled={isTerminal}
            data-testid="invoice-void-btn"
            className="rounded-sm text-destructive hover:bg-destructive/10"
          >
            <Ban className="mr-2 h-4 w-4" /> Void
          </Button>
        </div>
      </header>

      <section className="grid gap-4 sm:grid-cols-3">
        <TotalsCard label="Subtotal" value={formatCents(invoice.subtotal_cents)} />
        <TotalsCard label="Adjustments" value={formatCents(invoice.adjustment_cents)} />
        <TotalsCard label="Balance" value={formatCents(invoice.balance_cents)} primary />
      </section>

      <section className="rounded-sm border border-border bg-card">
        <header className="border-b border-border px-6 py-4">
          <h2 className="font-display text-xl font-medium tracking-tight">
            Lines
          </h2>
          {invoice.notes && (
            <p className="mt-1 text-sm text-muted-foreground">{invoice.notes}</p>
          )}
        </header>
        <table className="w-full table-auto text-sm">
          <thead className="text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            <tr>
              <th className="px-6 py-2">Code</th>
              <th className="px-6 py-2">Description</th>
              <th className="px-6 py-2">Service date</th>
              <th className="px-6 py-2 text-right">Qty</th>
              <th className="px-6 py-2 text-right">Unit</th>
              <th className="px-6 py-2 text-right">Total</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((ln) => (
              <tr
                key={ln.id}
                data-testid={`invoice-line-${ln.id}`}
                className="border-t border-border"
              >
                <td className="px-6 py-3 font-medium tabular-nums">
                  {ln.code}
                  {ln.modifiers?.length > 0 && (
                    <span className="ml-1 text-xs text-muted-foreground">
                      -{ln.modifiers.join("-")}
                    </span>
                  )}
                </td>
                <td className="px-6 py-3 text-muted-foreground">{ln.description}</td>
                <td className="px-6 py-3 text-muted-foreground">
                  {formatDate(ln.service_date)}
                </td>
                <td className="px-6 py-3 text-right tabular-nums">{ln.quantity}</td>
                <td className="px-6 py-3 text-right tabular-nums">
                  {formatCents(ln.unit_price_cents)}
                </td>
                <td className="px-6 py-3 text-right font-medium tabular-nums">
                  {formatCents(ln.total_cents)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <PostPaymentDialog
        open={payOpen}
        onOpenChange={setPayOpen}
        patient={{ id: invoice.patient_id, name: null }}
        invoices={[invoice]}
        onPosted={refresh}
      />

      <AdjustmentDialog
        open={adjustOpen}
        onOpenChange={setAdjustOpen}
        invoice={invoice}
        onApplied={refresh}
      />

      <VoidDialog
        open={voidOpen}
        onOpenChange={setVoidOpen}
        invoiceId={invoice.id}
        onVoided={refresh}
      />
    </div>
  );
}

function TotalsCard({ label, value, primary }) {
  return (
    <div
      data-testid={`totals-${label.toLowerCase()}`}
      className={`rounded-sm border border-border p-5 ${primary ? "bg-primary text-primary-foreground" : "bg-card"}`}
    >
      <div className={`text-[11px] uppercase tracking-[0.15em] ${primary ? "opacity-80" : "text-muted-foreground"}`}>{label}</div>
      <div className="mt-1 font-display text-3xl font-medium tabular-nums">{value}</div>
    </div>
  );
}

function AdjustmentDialog({ open, onOpenChange, invoice, onApplied }) {
  const [kind, setKind] = useState("writeoff");
  const [amountStr, setAmountStr] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) {
      setKind("writeoff"); setAmountStr(""); setReason(""); setSaving(false);
    }
  }, [open]);

  async function onSubmit() {
    const cents = parseDollarsToCents(amountStr);
    if (cents == null || cents <= 0) {
      toast.error("Enter an amount greater than zero");
      return;
    }
    if (cents > invoice.balance_cents) {
      toast.error("Adjustment exceeds invoice balance");
      return;
    }
    if (reason.trim().length < 5) {
      toast.error("Reason must be at least 5 characters");
      return;
    }
    setSaving(true);
    try {
      await createAdjustment({
        invoice_id: invoice.id, kind,
        amount_cents: cents, reason: reason.trim(),
      });
      toast.success(`${formatCents(cents)} ${kind} applied`);
      onOpenChange(false);
      onApplied?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not apply adjustment");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="adjustment-dialog" className="rounded-sm sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-display">Apply adjustment</DialogTitle>
          <DialogDescription>
            Writeoffs, courtesy, discount or contractual reduction. Current
            balance is <strong>{formatCents(invoice.balance_cents)}</strong>.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label htmlFor="adj-kind">Kind</Label>
            <Select value={kind} onValueChange={setKind}>
              <SelectTrigger id="adj-kind" data-testid="adj-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="writeoff">Writeoff</SelectItem>
                <SelectItem value="discount">Discount</SelectItem>
                <SelectItem value="courtesy">Courtesy</SelectItem>
                <SelectItem value="contractual">Contractual</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="adj-amount">Amount</Label>
            <Input
              id="adj-amount"
              data-testid="adj-amount"
              value={amountStr}
              onChange={(e) => setAmountStr(e.target.value)}
              placeholder="0.00"
            />
          </div>
          <div>
            <Label htmlFor="adj-reason">Reason</Label>
            <Textarea
              id="adj-reason"
              data-testid="adj-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why is this being adjusted?"
              rows={2}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            onClick={onSubmit} disabled={saving}
            data-testid="adj-submit"
            className="rounded-sm"
          >
            {saving ? "Applying…" : "Apply"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function VoidDialog({ open, onOpenChange, invoiceId, onVoided }) {
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) { setReason(""); setSaving(false); }
  }, [open]);

  async function onSubmit() {
    if (reason.trim().length < 5) {
      toast.error("Reason must be at least 5 characters");
      return;
    }
    setSaving(true);
    try {
      await voidInvoice(invoiceId, reason.trim());
      toast.success("Invoice voided");
      onOpenChange(false);
      onVoided?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not void invoice");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="void-dialog" className="rounded-sm sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-display">Void invoice</DialogTitle>
          <DialogDescription>
            Voiding is terminal — the invoice cannot be restored. Any
            applied payments remain on the patient's ledger as credit.
          </DialogDescription>
        </DialogHeader>
        <div>
          <Label htmlFor="void-reason">Reason</Label>
          <Textarea
            id="void-reason"
            data-testid="void-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. billed in error, duplicate"
            rows={2}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            onClick={onSubmit} disabled={saving}
            data-testid="void-submit"
            className="rounded-sm bg-destructive hover:bg-destructive/90"
          >
            {saving ? "Voiding…" : "Void invoice"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
