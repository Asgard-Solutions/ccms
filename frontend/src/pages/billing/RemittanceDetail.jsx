import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Skeleton } from "../../components/ui/skeleton";
import { Button } from "../../components/ui/button";
import { formatCents } from "../../utils/money";
import { formatDateTime } from "../../utils/time";
import { fetchRemittanceDetail } from "./useRemittance";

export default function RemittanceDetail() {
  const { id } = useParams();
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await fetchRemittanceDetail(id);
        if (!cancelled) setDetail(d);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [id]);

  if (loading) return <Skeleton className="h-64 w-full" />;
  if (!detail) return <p className="text-sm text-muted-foreground">Not found.</p>;

  const { remittance, claims, lines } = detail;

  return (
    <div data-testid="remittance-detail" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Remittance · {remittance.id.slice(0, 8)}
          </div>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
            {formatCents(remittance.total_paid_cents)}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Received {formatDateTime(remittance.received_at)}
            {remittance.check_or_eft_number && ` · ${remittance.check_or_eft_number}`}
          </p>
        </div>
        <Button asChild variant="outline" className="rounded-sm">
          <Link to="/billing" data-testid="remit-back-btn">Back</Link>
        </Button>
      </header>

      <section className="rounded-sm border border-border bg-card p-5">
        <h2 className="mb-3 font-display text-lg font-medium tracking-tight">
          Claims
        </h2>
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            <tr>
              <th className="py-1 pr-2">Claim</th>
              <th className="py-1 pr-2 text-right">Billed</th>
              <th className="py-1 pr-2 text-right">Paid</th>
              <th className="py-1 pr-2 text-right">Contractual</th>
              <th className="py-1 pr-2 text-right">Patient</th>
              <th className="py-1 pr-2 text-right">Denied</th>
              <th className="py-1 pr-2">Code</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((c) => (
              <tr key={c.id} className="border-t border-border">
                <td className="py-2 pr-2 font-medium">
                  <Link to={`/billing/claims/${c.claim_id}`} className="hover:underline">
                    {c.claim_id.slice(0, 8)}
                  </Link>
                </td>
                <td className="py-2 pr-2 text-right">{formatCents(c.billed_cents)}</td>
                <td className="py-2 pr-2 text-right">{formatCents(c.paid_cents)}</td>
                <td className="py-2 pr-2 text-right">{formatCents(c.contractual_cents)}</td>
                <td className="py-2 pr-2 text-right">{formatCents(c.patient_resp_cents)}</td>
                <td className="py-2 pr-2 text-right">{formatCents(c.denied_cents)}</td>
                <td className="py-2 pr-2 text-xs uppercase tracking-wider">
                  {c.denial_code || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {lines.length > 0 && (
        <section className="rounded-sm border border-border bg-card p-5">
          <h2 className="mb-3 font-display text-lg font-medium tracking-tight">
            Line detail
          </h2>
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="py-1 pr-2">CPT</th>
                <th className="py-1 pr-2 text-right">Billed</th>
                <th className="py-1 pr-2 text-right">Paid</th>
                <th className="py-1 pr-2 text-right">Denied</th>
                <th className="py-1 pr-2">Denial code</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((ln) => (
                <tr key={ln.id} className="border-t border-border">
                  <td className="py-2 pr-2 font-medium">{ln.cpt_code || "—"}</td>
                  <td className="py-2 pr-2 text-right">{formatCents(ln.billed_cents)}</td>
                  <td className="py-2 pr-2 text-right">{formatCents(ln.paid_cents)}</td>
                  <td className="py-2 pr-2 text-right">{formatCents(ln.denied_cents)}</td>
                  <td className="py-2 pr-2 text-xs uppercase tracking-wider">
                    {ln.denial_code || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
