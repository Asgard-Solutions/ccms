import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { CheckCircle2, Upload } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { formatCents } from "../../utils/money";
import {
  commitRemittanceImport,
  uploadRemittanceImport,
} from "./useRemittance";

export default function RemittanceImport() {
  const navigate = useNavigate();
  const fileInput = useRef(null);
  const [staged, setStaged] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [committing, setCommitting] = useState(false);

  async function onPick(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    setUploading(true);
    try {
      const s = await uploadRemittanceImport(f);
      setStaged(s);
      toast.success(`Parsed ${s.claim_count} claims (${s.matched_count} matched)`);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function onCommit() {
    if (!staged) return;
    setCommitting(true);
    try {
      const r = await commitRemittanceImport(staged.id);
      toast.success("Remittance posted");
      navigate(`/billing/remittances/${r.remittance_id}`);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Commit failed");
    } finally { setCommitting(false); }
  }

  const unmatched = staged?.unmatched_count || 0;
  const canCommit = !!staged && unmatched === 0 && !!staged.resolved_payer_id;

  return (
    <div data-testid="remit-import" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Billing
          </div>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
            Import remittance
          </h1>
          <p className="mt-2 max-w-xl text-sm text-muted-foreground">
            Drop an 835 EDI or JSON remittance file. We parse it, match
            claims, and give you a preview before committing. No ledger
            changes happen until you click "Commit".
          </p>
        </div>
        <Button asChild variant="outline" className="rounded-sm">
          <Link to="/billing" data-testid="remit-import-back">Back</Link>
        </Button>
      </header>

      <section className="rounded-sm border border-dashed border-border bg-card p-10 text-center">
        <input
          ref={fileInput}
          type="file"
          accept=".835,.json,.txt,.edi,application/json,text/plain"
          className="hidden"
          onChange={onPick}
          data-testid="remit-import-file-input"
        />
        <Upload className="mx-auto mb-3 h-8 w-8 text-muted-foreground" />
        <Button
          onClick={() => fileInput.current?.click()}
          disabled={uploading}
          className="rounded-sm"
          data-testid="remit-import-choose-file"
        >
          {uploading ? "Uploading…" : "Choose file"}
        </Button>
        <p className="mt-3 text-xs text-muted-foreground">
          Max 2 MB · X12 835 or JSON (schema <code>ccms.remit.import.v1</code>)
        </p>
      </section>

      {uploading && (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}

      {staged && (
        <section
          data-testid="remit-import-preview"
          className="rounded-sm border border-border bg-card p-6"
        >
          <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
                Preview · {staged.source}
              </div>
              <h2 className="font-display text-xl font-medium tracking-tight">
                {staged.filename}
              </h2>
              <div className="mt-1 flex flex-wrap gap-3 text-sm text-muted-foreground">
                <span>Total paid: <strong>{formatCents(staged.header.total_paid_cents)}</strong></span>
                <span>Check/EFT: {staged.header.check_or_eft_number || "—"}</span>
                <span>
                  Payer:{" "}
                  {staged.resolved_payer_id
                    ? <span data-testid="remit-import-payer-ok" className="text-success">resolved</span>
                    : <span className="text-destructive">unresolved</span>}
                </span>
              </div>
            </div>
            <div className="text-right">
              <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
                Claims
              </div>
              <div className="font-display text-2xl tabular-nums">
                {staged.matched_count}/{staged.claim_count}
              </div>
              <div className="text-xs text-muted-foreground">
                {unmatched > 0 ? `${unmatched} unmatched` : "all matched"}
              </div>
            </div>
          </header>

          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="py-1 pr-2">Payer ctl</th>
                <th className="py-1 pr-2 text-right">Billed</th>
                <th className="py-1 pr-2 text-right">Paid</th>
                <th className="py-1 pr-2 text-right">Denied</th>
                <th className="py-1 pr-2">Match</th>
                <th className="py-1 pr-2">Claim</th>
              </tr>
            </thead>
            <tbody>
              {staged.claims.map((c, i) => {
                const m = c.match || {};
                return (
                  <tr
                    key={i}
                    data-testid={`remit-import-row-${i}`}
                    className="border-t border-border"
                  >
                    <td className="py-1 pr-2 text-xs">
                      {c.payer_control_number || c.patient_control_number || "—"}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums">
                      {formatCents(c.billed_cents)}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums">
                      {formatCents(c.paid_cents)}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums">
                      {formatCents(c.denied_cents)}
                    </td>
                    <td className="py-1 pr-2">
                      <span className={`rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase ${
                        m.matched ? "bg-success/15 text-success" : "bg-destructive/15 text-destructive"
                      }`}>
                        {m.matched ? m.match_method : "none"}
                      </span>
                    </td>
                    <td className="py-1 pr-2 text-xs">
                      {m.claim_id ? (
                        <Link to={`/billing/claims/${m.claim_id}`} className="hover:underline">
                          {m.claim_id.slice(0, 8)}
                        </Link>
                      ) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div className="mt-4 flex justify-end">
            <Button
              onClick={onCommit}
              disabled={!canCommit || committing}
              className="rounded-sm"
              data-testid="remit-import-commit"
            >
              <CheckCircle2 className="mr-1 h-4 w-4" />
              {committing ? "Committing…" : "Commit posting"}
            </Button>
          </div>
        </section>
      )}
    </div>
  );
}
