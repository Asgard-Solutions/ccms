import { Link } from "react-router-dom";
import { TrendingDown } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { formatCents } from "../../utils/money";
import { useArAging, useArAgingByPayer } from "./useRemittance";

const BUCKET_ORDER = ["0-30", "31-60", "61-90", "91-120", "120+"];

export default function ArAgingReport() {
  const { data, loading } = useArAging();
  const { rows: byPayer, loading: loadingByPayer } = useArAgingByPayer();

  const buckets = data?.buckets || [];
  const maxBucket = Math.max(1, ...buckets.map((b) => b.balance_cents || 0));

  return (
    <div data-testid="ar-aging-report" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Billing
          </div>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
            Accounts receivable — aging
          </h1>
        </div>
        <Button asChild variant="outline" className="rounded-sm">
          <Link to="/billing" data-testid="ar-back-btn">Back to dashboard</Link>
        </Button>
      </header>

      <section className="rounded-sm border border-border bg-card p-6">
        {loading ? (
          <Skeleton className="h-48 w-full" />
        ) : (
          <>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="font-display text-lg font-medium tracking-tight">
                Overall aging
              </h2>
              <div className="text-right">
                <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
                  Total outstanding
                </div>
                <div
                  data-testid="ar-total"
                  className="font-display text-3xl font-medium tabular-nums"
                >
                  {formatCents(data?.total_balance_cents || 0)}
                </div>
                <div className="text-xs text-muted-foreground">
                  {data?.total_invoice_count || 0} open invoices
                </div>
              </div>
            </div>
            <div className="grid gap-3">
              {BUCKET_ORDER.map((label) => {
                const b = buckets.find((x) => x.bucket === label) || {
                  balance_cents: 0, invoice_count: 0,
                };
                const pct = Math.round(((b.balance_cents || 0) / maxBucket) * 100);
                return (
                  <div key={label} data-testid={`ar-bucket-${label}`} className="flex items-center gap-3">
                    <div className="w-16 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      {label}d
                    </div>
                    <div className="flex-1 overflow-hidden rounded-sm bg-muted">
                      <div
                        className="h-7 bg-primary/80 transition-all"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <div className="w-28 text-right tabular-nums">
                      {formatCents(b.balance_cents)}
                    </div>
                    <div className="w-16 text-right text-xs text-muted-foreground tabular-nums">
                      {b.invoice_count} inv
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </section>

      <section className="rounded-sm border border-border bg-card p-6">
        <h2 className="mb-4 font-display text-lg font-medium tracking-tight">
          By payer
        </h2>
        {loadingByPayer ? (
          <Skeleton className="h-48 w-full" />
        ) : byPayer.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <TrendingDown className="h-4 w-4" />
            No outstanding balances.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="py-2 pr-2">Payer</th>
                {BUCKET_ORDER.map((b) => (
                  <th key={b} className="py-2 pr-2 text-right">{b}</th>
                ))}
                <th className="py-2 text-right">Total</th>
              </tr>
            </thead>
            <tbody>
              {byPayer.map((r) => {
                const b = Object.fromEntries(
                  (r.buckets || []).map((x) => [x.bucket, x]),
                );
                return (
                  <tr
                    key={r.payer_id || "self-pay"}
                    data-testid={`ar-payer-${r.payer_id || "self-pay"}`}
                    className="border-t border-border"
                  >
                    <td className="py-2 pr-2 font-medium">{r.payer_name}</td>
                    {BUCKET_ORDER.map((lbl) => (
                      <td key={lbl} className="py-2 pr-2 text-right tabular-nums">
                        {formatCents((b[lbl] && b[lbl].balance_cents) || 0)}
                      </td>
                    ))}
                    <td className="py-2 text-right font-semibold tabular-nums">
                      {formatCents(r.total_balance_cents)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
