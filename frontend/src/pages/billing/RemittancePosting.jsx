import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { CheckCircle2, Plus, Trash2 } from "lucide-react";
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
import { formatCents, parseDollarsToCents } from "../../utils/money";
import { usePayers } from "./useBillingAdmin";
import { postRemittance } from "./useRemittance";
import { useClaims } from "./useClaims";

/** Phase 5 — manual remittance posting screen.
 *  Workflow: pick payer -> enter check/EFT header -> add claim rows
 *  -> server validates totals & writes payment/allocations/adjustments
 *  /denials -> redirect to remittance detail.
 */
export default function RemittancePosting() {
  const navigate = useNavigate();
  const { rows: payers } = usePayers();
  const { rows: allClaims } = useClaims({});

  const [payerId, setPayerId] = useState("");
  const [receivedAt, setReceivedAt] = useState(
    () => new Date().toISOString().slice(0, 10),
  );
  const [checkEft, setCheckEft] = useState("");
  const [notes, setNotes] = useState("");
  const [claimRows, setClaimRows] = useState([]);
  const [saving, setSaving] = useState(false);

  const eligibleClaims = useMemo(
    () => allClaims.filter((c) =>
      c.payer_id === payerId &&
      ["submitted", "accepted", "pending"].includes(c.status),
    ),
    [allClaims, payerId],
  );

  const totalPaid = useMemo(
    () => claimRows.reduce((a, r) => a + (parseDollarsToCents(r.paid) || 0), 0),
    [claimRows],
  );

  function addRow(claim) {
    if (claimRows.some((r) => r.claim_id === claim.id)) return;
    setClaimRows((rs) => [...rs, {
      claim_id: claim.id,
      billed_cents: claim.billed_cents,
      billed_display: formatCents(claim.billed_cents),
      paid: (claim.billed_cents / 100).toFixed(2),
      contractual: "0.00",
      patient_resp: "0.00",
      denied: "0.00",
      denial_code: "",
    }]);
  }

  function removeRow(idx) {
    setClaimRows((rs) => rs.filter((_, i) => i !== idx));
  }

  function updateRow(idx, patch) {
    setClaimRows((rs) => rs.map((r, i) => i === idx ? { ...r, ...patch } : r));
  }

  async function onSubmit() {
    if (!payerId) { toast.error("Pick a payer first"); return; }
    if (claimRows.length === 0) { toast.error("Add at least one claim"); return; }
    setSaving(true);
    try {
      const body = {
        payer_id: payerId,
        received_at: receivedAt,
        check_or_eft_number: checkEft || null,
        notes: notes || null,
        total_paid_cents: totalPaid,
        claims: claimRows.map((r) => ({
          claim_id: r.claim_id,
          billed_cents: r.billed_cents,
          paid_cents: parseDollarsToCents(r.paid) || 0,
          contractual_cents: parseDollarsToCents(r.contractual) || 0,
          patient_resp_cents: parseDollarsToCents(r.patient_resp) || 0,
          denied_cents: parseDollarsToCents(r.denied) || 0,
          denial_code: r.denial_code || null,
        })),
      };
      const remit = await postRemittance(body);
      toast.success("Remittance posted");
      navigate(`/billing/remittances/${remit.id}`);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not post remittance");
    } finally { setSaving(false); }
  }

  return (
    <div data-testid="remittance-posting" className="space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
          Billing
        </div>
        <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
          Post a remittance
        </h1>
        <p className="mt-2 max-w-xl text-sm text-muted-foreground">
          Manual remittance entry. The payment, adjustments and denial
          work items are all created atomically. Patient balance rolls
          forward automatically via the standard invoice recompute.
        </p>
      </header>

      <section className="grid gap-4 rounded-sm border border-border bg-card p-5 md:grid-cols-4">
        <div>
          <Label>Payer</Label>
          <Select value={payerId || ""} onValueChange={(v) => { setPayerId(v); setClaimRows([]); }}>
            <SelectTrigger data-testid="remit-payer">
              <SelectValue placeholder="Select…" />
            </SelectTrigger>
            <SelectContent>
              {(payers || []).map((p) => (
                <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label>Received</Label>
          <Input
            type="date" value={receivedAt}
            onChange={(e) => setReceivedAt(e.target.value)}
            data-testid="remit-received"
          />
        </div>
        <div>
          <Label>Check / EFT #</Label>
          <Input
            value={checkEft}
            onChange={(e) => setCheckEft(e.target.value)}
            placeholder="CHK-12345"
            data-testid="remit-check"
          />
        </div>
        <div>
          <Label>Total paid</Label>
          <Input
            disabled value={formatCents(totalPaid)}
            data-testid="remit-total"
            className="font-semibold"
          />
        </div>
      </section>

      <section className="rounded-sm border border-border bg-card p-5">
        <header className="mb-3 flex items-center justify-between">
          <h2 className="font-display text-lg font-medium tracking-tight">
            Claims on this remittance
          </h2>
          {payerId && (
            <EligibleClaimsPicker
              claims={eligibleClaims}
              onPick={addRow}
            />
          )}
        </header>
        {claimRows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {payerId
              ? "No claims added yet — use the picker to add one."
              : "Pick a payer above to see eligible claims."}
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="py-1 pr-2">Claim</th>
                <th className="py-1 pr-2 text-right">Billed</th>
                <th className="py-1 pr-2 text-right">Paid</th>
                <th className="py-1 pr-2 text-right">Contractual</th>
                <th className="py-1 pr-2 text-right">Patient</th>
                <th className="py-1 pr-2 text-right">Denied</th>
                <th className="py-1 pr-2">Denial code</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {claimRows.map((r, i) => (
                <tr key={r.claim_id} data-testid={`remit-row-${r.claim_id}`} className="border-t border-border">
                  <td className="py-2 pr-2 font-medium">
                    {r.claim_id.slice(0, 8)}
                  </td>
                  <td className="py-2 pr-2 text-right">{r.billed_display}</td>
                  <td className="py-2 pr-2">
                    <Input
                      className="text-right tabular-nums"
                      value={r.paid}
                      onChange={(e) => updateRow(i, { paid: e.target.value })}
                      data-testid={`remit-paid-${r.claim_id}`}
                    />
                  </td>
                  <td className="py-2 pr-2">
                    <Input
                      className="text-right tabular-nums"
                      value={r.contractual}
                      onChange={(e) => updateRow(i, { contractual: e.target.value })}
                      data-testid={`remit-contractual-${r.claim_id}`}
                    />
                  </td>
                  <td className="py-2 pr-2">
                    <Input
                      className="text-right tabular-nums"
                      value={r.patient_resp}
                      onChange={(e) => updateRow(i, { patient_resp: e.target.value })}
                    />
                  </td>
                  <td className="py-2 pr-2">
                    <Input
                      className="text-right tabular-nums"
                      value={r.denied}
                      onChange={(e) => updateRow(i, { denied: e.target.value })}
                      data-testid={`remit-denied-${r.claim_id}`}
                    />
                  </td>
                  <td className="py-2 pr-2">
                    <Input
                      value={r.denial_code}
                      onChange={(e) => updateRow(i, { denial_code: e.target.value })}
                      placeholder="e.g. CO-97"
                      data-testid={`remit-denial-${r.claim_id}`}
                    />
                  </td>
                  <td className="py-2 text-right">
                    <Button
                      size="sm" variant="ghost"
                      onClick={() => removeRow(i)}
                      data-testid={`remit-remove-${r.claim_id}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="rounded-sm border border-border bg-card p-5">
        <Label>Notes</Label>
        <Textarea
          rows={2} value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Optional posting notes"
        />
      </section>

      <div className="flex justify-end gap-3">
        <Button variant="ghost" onClick={() => navigate("/billing")}>
          Cancel
        </Button>
        <Button
          onClick={onSubmit}
          disabled={saving || claimRows.length === 0}
          data-testid="remit-post-btn"
          className="rounded-sm"
        >
          <CheckCircle2 className="mr-1 h-4 w-4" />
          {saving ? "Posting…" : "Post remittance"}
        </Button>
      </div>
    </div>
  );
}

function EligibleClaimsPicker({ claims, onPick }) {
  return (
    <Select onValueChange={(id) => {
      const c = claims.find((x) => x.id === id);
      if (c) onPick(c);
    }}>
      <SelectTrigger className="w-64" data-testid="remit-add-claim">
        <SelectValue placeholder={
          claims.length ? `Add a claim (${claims.length} eligible)` : "No eligible claims"
        } />
      </SelectTrigger>
      <SelectContent>
        {claims.map((c) => (
          <SelectItem key={c.id} value={c.id}>
            {c.id.slice(0, 8)} · {formatCents(c.billed_cents)} · {c.status}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
