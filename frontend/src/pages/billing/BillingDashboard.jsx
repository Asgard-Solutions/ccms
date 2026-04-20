import { useMemo } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, CreditCard, FileStack, FileText, Wallet } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { formatCents } from "../../utils/money";
import { formatDateTime } from "../../utils/time";
import {
  INVOICE_STATUS_LABELS,
  invoiceStatusTone,
  useInvoices,
  usePayments,
  useOutstandingSummary,
} from "./useBilling";

function StatCard({ label, value, sub, testId }) {
  return (
    <div
      data-testid={testId}
      className="rounded-sm border border-border bg-card p-5"
    >
      <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">{label}</div>
      <div className="mt-1 font-display text-3xl font-medium tabular-nums">{value}</div>
      {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

export default function BillingDashboard() {
  const { rows: invoices, loading: invoicesLoading } = useInvoices();
  const { rows: payments, loading: paymentsLoading } = usePayments();
  const { outstanding, openCount, totalBilled } = useOutstandingSummary(invoices);

  const recentInvoices = useMemo(
    () => [...invoices]
      .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""))
      .slice(0, 8),
    [invoices],
  );
  const recentPayments = useMemo(
    () => [...payments]
      .sort((a, b) => (b.received_at || "").localeCompare(a.received_at || ""))
      .slice(0, 8),
    [payments],
  );

  return (
    <div data-testid="billing-dashboard" className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Patient financials
          </div>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
            Billing
          </h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" className="rounded-sm" data-testid="billing-view-claims">
            <Link to="/billing/claims">
              <FileStack className="mr-1 h-4 w-4" /> Claims queue
            </Link>
          </Button>
          <Button asChild className="rounded-sm" data-testid="billing-view-invoices">
            <Link to="/billing/invoices">
              View invoices <ArrowRight className="ml-1 h-4 w-4" />
            </Link>
          </Button>
        </div>
      </header>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          testId="stat-outstanding"
          label="Outstanding balance"
          value={invoicesLoading ? "—" : formatCents(outstanding)}
          sub={`${openCount} open invoice${openCount === 1 ? "" : "s"}`}
        />
        <StatCard
          testId="stat-total-billed"
          label="Lifetime billed"
          value={invoicesLoading ? "—" : formatCents(totalBilled)}
          sub={`${invoices.length} invoice${invoices.length === 1 ? "" : "s"}`}
        />
        <StatCard
          testId="stat-payments-recorded"
          label="Payments recorded"
          value={paymentsLoading ? "—" : payments.length}
          sub="cash · check · card ref."
        />
      </div>

      <div className="grid gap-8 lg:grid-cols-2">
        <section className="rounded-sm border border-border bg-card p-6">
          <header className="mb-4 flex items-center justify-between">
            <h2 className="font-display text-xl font-medium tracking-tight">
              Recent invoices
            </h2>
            <FileText className="h-4 w-4 text-muted-foreground" />
          </header>
          {invoicesLoading ? (
            <Skeleton className="h-40 w-full rounded-sm" />
          ) : recentInvoices.length === 0 ? (
            <p className="text-sm text-muted-foreground">No invoices yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {recentInvoices.map((inv) => (
                <li key={inv.id}>
                  <Link
                    to={`/billing/invoices/${inv.id}`}
                    data-testid={`dashboard-invoice-${inv.id}`}
                    className="-mx-3 flex items-center gap-3 rounded-sm px-3 py-3 hover:bg-muted"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium">
                        Invoice {inv.id.slice(0, 8)}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {formatDateTime(inv.created_at)} · {formatCents(inv.total_cents)}
                      </div>
                    </div>
                    <span
                      className={`rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${invoiceStatusTone(inv.status)}`}
                    >
                      {INVOICE_STATUS_LABELS[inv.status] || inv.status}
                    </span>
                    <span className="ml-2 min-w-[5rem] text-right text-sm tabular-nums">
                      {formatCents(inv.balance_cents)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-sm border border-border bg-card p-6">
          <header className="mb-4 flex items-center justify-between">
            <h2 className="font-display text-xl font-medium tracking-tight">
              Recent payments
            </h2>
            <Wallet className="h-4 w-4 text-muted-foreground" />
          </header>
          {paymentsLoading ? (
            <Skeleton className="h-40 w-full rounded-sm" />
          ) : recentPayments.length === 0 ? (
            <p className="text-sm text-muted-foreground">No payments recorded yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {recentPayments.map((p) => (
                <li
                  key={p.id}
                  data-testid={`dashboard-payment-${p.id}`}
                  className="flex items-center gap-3 py-3"
                >
                  <div className="flex h-9 w-9 items-center justify-center rounded-sm bg-primary/10 text-primary">
                    <CreditCard className="h-4 w-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="truncate text-sm font-medium">
                      {formatCents(p.amount_cents)} · {p.method.replaceAll("_", " ")}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {formatDateTime(p.received_at || p.created_at)} · {p.status}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
