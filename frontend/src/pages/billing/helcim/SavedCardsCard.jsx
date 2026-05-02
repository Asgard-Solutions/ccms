import { useCallback, useEffect, useState } from "react";
import { CreditCard, Plus, ShieldCheck, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../../../components/ui/button";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "../../../components/ui/dialog";
import {
  listSavedCards,
  deleteSavedCard,
  chargeSavedCardById,
} from "./api";
import { formatDateTime } from "../../../utils/time";
import { formatCents, parseDollarsToCents } from "../../../utils/money";

function ChargeSavedCardDialog({ open, onClose, card, onCharged }) {
  const [amountStr, setAmountStr] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const cents = parseDollarsToCents(amountStr);

  const onSubmit = async () => {
    if (cents == null || cents <= 0) {
      toast.error("Enter a charge amount.");
      return;
    }
    setBusy(true);
    try {
      const res = await chargeSavedCardById({
        token_id: card.id,
        amount_cents: cents,
        description: description || `Charge against saved card ****${card.last4}`,
      });
      toast.success(`Charged ${formatCents(cents)} via Helcim (txn ${res.transaction?.transactionId || "n/a"}).`);
      onCharged?.(res);
      onClose();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Charge failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="charge-saved-card-dialog" className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-display flex items-center gap-2">
            <Sparkles className="h-4 w-4" /> Charge saved card
          </DialogTitle>
          <DialogDescription>
            {card?.brand} ****{card?.last4}
            {card?.expiry ? ` · exp ${card.expiry}` : ""}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label htmlFor="charge-amount">Amount</Label>
            <Input
              id="charge-amount"
              data-testid="charge-amount-input"
              value={amountStr}
              onChange={(e) => setAmountStr(e.target.value)}
              placeholder="0.00"
              autoFocus
            />
          </div>
          <div>
            <Label htmlFor="charge-desc">Description (optional)</Label>
            <Input
              id="charge-desc"
              data-testid="charge-desc-input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="e.g. Treatment plan visit copay"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="charge-cancel">Cancel</Button>
          <Button data-testid="charge-confirm" disabled={busy} onClick={onSubmit}>
            {busy ? "Charging…" : `Charge ${cents ? formatCents(cents) : ""}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function SavedCardsCard({ patientId, onChanged }) {
  const [cards, setCards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [chargeOpen, setChargeOpen] = useState(false);
  const [activeCard, setActiveCard] = useState(null);

  const load = useCallback(async () => {
    if (!patientId) return;
    setLoading(true);
    try {
      setCards(await listSavedCards(patientId));
      setError(null);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to load cards.");
    } finally {
      setLoading(false);
    }
  }, [patientId]);

  useEffect(() => { load(); }, [load]);

  const onDelete = async (card) => {
    if (!confirm(`Remove saved card ****${card.last4}?`)) return;
    try {
      await deleteSavedCard(card.id);
      toast.success("Card removed.");
      load();
      onChanged?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to delete card.");
    }
  };

  return (
    <section
      data-testid="saved-cards-card"
      className="rounded-sm border border-border bg-card p-5 space-y-4"
    >
      <div className="flex items-center justify-between">
        <h2 className="font-display text-base font-medium flex items-center gap-2">
          <CreditCard className="h-4 w-4 text-primary" />
          Saved cards
          {cards.length > 0 && (
            <span className="ml-1 text-xs font-normal text-muted-foreground">({cards.length})</span>
          )}
        </h2>
      </div>

      {loading ? (
        <div className="text-sm text-muted-foreground">Loading…</div>
      ) : error ? (
        <div data-testid="saved-cards-error" className="rounded-sm border border-destructive-soft bg-destructive-soft p-3 text-sm text-destructive">
          {error}
        </div>
      ) : cards.length === 0 ? (
        <div data-testid="saved-cards-empty" className="rounded-sm border border-dashed border-border bg-card/50 px-4 py-6 text-center text-sm text-muted-foreground">
          No saved cards yet. Take a payment via Helcim and tick "Save card on file" to register one.
        </div>
      ) : (
        <ul className="space-y-2">
          {cards.map((c) => (
            <li
              key={c.id}
              data-testid={`saved-card-row-${c.id}`}
              className="flex items-center justify-between rounded-sm border border-border bg-muted/30 px-3 py-2.5"
            >
              <div>
                <div className="text-sm font-medium text-foreground flex items-center gap-2">
                  <span>{c.brand || "Card"} ****{c.last4 || "----"}</span>
                  {c.is_default && (
                    <span data-testid={`saved-card-default-${c.id}`} className="rounded-sm bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-primary">
                      Default
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-muted-foreground">
                  {c.cardholder_name && `${c.cardholder_name} · `}
                  {c.expiry && `exp ${c.expiry} · `}
                  Saved {c.created_at ? formatDateTime(c.created_at) : "—"}
                  {c.last_used_at && ` · last used ${formatDateTime(c.last_used_at)} (${c.last_use_outcome})`}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  data-testid={`saved-card-charge-${c.id}`}
                  size="sm"
                  variant="outline"
                  onClick={() => { setActiveCard(c); setChargeOpen(true); }}
                  className="gap-1"
                >
                  <Sparkles className="h-3.5 w-3.5" /> Charge
                </Button>
                <Button
                  data-testid={`saved-card-delete-${c.id}`}
                  size="sm"
                  variant="ghost"
                  onClick={() => onDelete(c)}
                  className="text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
        <ShieldCheck className="mt-0.5 h-3 w-3 flex-none text-primary" />
        Tokens are stored encrypted in the Helcim Customer Vault. We never see the PAN.
      </p>

      {activeCard && (
        <ChargeSavedCardDialog
          open={chargeOpen}
          onClose={() => { setChargeOpen(false); setActiveCard(null); }}
          card={activeCard}
          onCharged={() => { load(); onChanged?.(); }}
        />
      )}
    </section>
  );
}
