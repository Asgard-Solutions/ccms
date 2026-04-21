import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { CreditCard, DollarSign } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import {
  clampCents,
  formatCents,
  parseDollarsToCents,
  sumAmountCents,
} from "../../utils/money";
import {
  PAYMENT_METHOD_LABELS,
  createPayment,
} from "./useBilling";

const METHOD_OPTIONS = Object.entries(PAYMENT_METHOD_LABELS);

/**
 * PostPaymentDialog — capture a cash/check/card-reference payment and
 * allocate it across one or more open invoices.
 *
 * Props:
 *   open            boolean
 *   onOpenChange    (boolean) -> void
 *   patient         { id, name }
 *   invoices        list of invoices (caller pre-filters to open ones)
 *   onPosted        optional callback invoked after a successful post
 */
export default function PostPaymentDialog({
  open,
  onOpenChange,
  patient,
  invoices,
  onPosted,
}) {
  const [method, setMethod] = useState("cash");
  const [amountStr, setAmountStr] = useState("");
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const [allocations, setAllocations] = useState({});   // invoice_id -> cents
  const [saving, setSaving] = useState(false);

  const openInvoices = useMemo(
    () => (invoices || []).filter(
      (inv) => inv.balance_cents > 0 &&
        !["void", "refunded"].includes(inv.status),
    ),
    [invoices],
  );

  useEffect(() => {
    if (!open) {
      // reset on close
      setMethod("cash");
      setAmountStr("");
      setReference("");
      setNotes("");
      setAllocations({});
    }
  }, [open]);

  const amountCents = parseDollarsToCents(amountStr);
  const totalAllocated = sumAmountCents(
    Object.entries(allocations).map(([, v]) => ({ amount_cents: v })),
  );
  const remaining = amountCents != null
    ? Math.max(amountCents - totalAllocated, 0)
    : 0;
  const overAllocated = amountCents != null && totalAllocated > amountCents;

  function setAllocation(invoiceId, rawCents, invoiceBalance) {
    const next = {
      ...allocations,
      [invoiceId]: clampCents(rawCents, { min: 0, max: invoiceBalance }),
    };
    setAllocations(next);
  }

  function autoAllocate() {
    // Fill invoices oldest-first up to amount.
    if (amountCents == null || amountCents <= 0) return;
    let left = amountCents;
    const next = {};
    for (const inv of [...openInvoices].sort(
      (a, b) => (a.created_at || "").localeCompare(b.created_at || ""),
    )) {
      const put = Math.min(inv.balance_cents, left);
      if (put > 0) next[inv.id] = put;
      left -= put;
      if (left <= 0) break;
    }
    setAllocations(next);
  }

  async function onSubmit() {
    if (amountCents == null || amountCents <= 0) {
      toast.error("Enter an amount greater than zero");
      return;
    }
    if (overAllocated) {
      toast.error("Allocated total exceeds payment amount");
      return;
    }
    setSaving(true);
    try {
      const allocsList = Object.entries(allocations)
        .filter(([, v]) => v > 0)
        .map(([invoice_id, amount_cents]) => ({ invoice_id, amount_cents }));
      await createPayment({
        patient_id: patient.id,
        method,
        amount_cents: amountCents,
        currency: "USD",
        reference: reference.trim() || undefined,
        allocations: allocsList,
      });
      toast.success(
        `Posted ${formatCents(amountCents)} payment`
          + (remaining > 0 ? ` (${formatCents(remaining)} unapplied credit)` : ""),
      );
      if (onPosted) onPosted();
      onOpenChange(false);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not post payment");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="post-payment-dialog"
        className="max-h-[85vh] overflow-y-auto sm:max-w-xl rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            Post payment
          </DialogTitle>
          <DialogDescription>
            {patient?.name ? `Posting for ${patient.name}. ` : ""}
            Cash &amp; checks post as captured. Card / ACH stays pending
            until the gateway confirms.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label htmlFor="pp-method">Method</Label>
              <Select value={method} onValueChange={setMethod}>
                <SelectTrigger id="pp-method" data-testid="pp-method">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {METHOD_OPTIONS.map(([key, label]) => (
                    <SelectItem key={key} value={key}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label htmlFor="pp-amount">Amount</Label>
              <div className="relative">
                <DollarSign
                  aria-hidden="true"
                  className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                />
                <Input
                  id="pp-amount"
                  data-testid="pp-amount"
                  value={amountStr}
                  onChange={(e) => setAmountStr(e.target.value)}
                  placeholder="0.00"
                  className="pl-7"
                  autoFocus
                />
              </div>
            </div>
          </div>

          <div>
            <Label htmlFor="pp-reference">Reference</Label>
            <Input
              id="pp-reference"
              data-testid="pp-reference"
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              placeholder="Check #, card last-4, gateway txn id…"
            />
          </div>

          <div className="space-y-2 rounded-sm border border-border p-3">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">Allocate to invoices</Label>
              <Button
                variant="ghost" size="sm"
                onClick={autoAllocate}
                disabled={amountCents == null}
                data-testid="pp-auto-allocate"
              >
                Auto-allocate
              </Button>
            </div>
            {openInvoices.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No open invoices — payment will be recorded as an
                unapplied credit.
              </p>
            ) : (
              <div className="divide-y divide-border">
                {openInvoices.map((inv) => {
                  const allocCents = allocations[inv.id] || 0;
                  return (
                    <div
                      key={inv.id}
                      data-testid={`pp-invoice-row-${inv.id}`}
                      className="flex items-center gap-3 py-2"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">
                          Invoice {inv.id.slice(0, 8)}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          Balance {formatCents(inv.balance_cents)} · {inv.status}
                        </div>
                      </div>
                      <div className="w-32">
                        <Input
                          data-testid={`pp-alloc-${inv.id}`}
                          value={allocCents ? (allocCents / 100).toFixed(2) : ""}
                          onChange={(e) => {
                            const c = parseDollarsToCents(e.target.value);
                            setAllocation(
                              inv.id, c || 0, inv.balance_cents,
                            );
                          }}
                          placeholder="0.00"
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">
                Allocated {formatCents(totalAllocated)}
                {amountCents != null
                  ? ` / ${formatCents(amountCents)}`
                  : ""}
              </span>
              <span
                data-testid="pp-remaining"
                className={overAllocated ? "text-destructive font-semibold" : "text-muted-foreground"}
              >
                {overAllocated
                  ? "Over-allocated"
                  : amountCents != null
                    ? `Remaining ${formatCents(remaining)}`
                    : "—"}
              </span>
            </div>
          </div>

          <div>
            <Label htmlFor="pp-notes">Notes</Label>
            <Textarea
              id="pp-notes"
              data-testid="pp-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional internal notes"
              rows={2}
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="ghost" onClick={() => onOpenChange(false)}
            data-testid="pp-cancel"
          >Cancel</Button>
          <Button
            onClick={onSubmit}
            disabled={saving || amountCents == null || amountCents <= 0 || overAllocated}
            data-testid="pp-submit"
            className="rounded-sm"
          >
            <CreditCard className="mr-2 h-4 w-4" />
            {saving ? "Posting…" : `Post ${amountCents ? formatCents(amountCents) : ""}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
