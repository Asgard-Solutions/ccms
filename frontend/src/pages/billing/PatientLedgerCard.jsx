import { useMemo } from "react";
import { ArrowDownRight, ArrowUpRight, ClipboardList, Wallet } from "lucide-react";
import { Skeleton } from "../../components/ui/skeleton";
import { formatDate, formatDateTime } from "../../utils/time";
import { formatCents } from "../../utils/money";
import { invoiceStatusTone, usePatientLedger } from "./useBilling";

const KIND_META = {
  charge: { label: "Charge", icon: ArrowUpRight, tone: "text-warning" },
  payment: { label: "Payment", icon: ArrowDownRight, tone: "text-success" },
  adjustment: { label: "Adjustment", icon: ArrowDownRight, tone: "text-success" },
  refund: { label: "Refund", icon: ArrowUpRight, tone: "text-destructive" },
  credit: { label: "Credit", icon: Wallet, tone: "text-muted-foreground" },
  invoice_void: { label: "Void", icon: ArrowDownRight, tone: "text-muted-foreground" },
};

function LedgerRow({ row }) {
  const meta = KIND_META[row.kind] || { label: row.kind, icon: ClipboardList, tone: "text-muted-foreground" };
  const Icon = meta.icon;
  return (
    <tr
      data-testid={`ledger-row-${row.id}`}
      className="border-b border-border last:border-0"
    >
      <td className="py-3 pr-4 align-top text-xs text-muted-foreground">
        {row.occurred_at ? formatDateTime(row.occurred_at) : "—"}
      </td>
      <td className="py-3 pr-4 align-top">
        <span className={`inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide ${meta.tone}`}>
          <Icon className="h-3.5 w-3.5" /> {meta.label}
        </span>
      </td>
      <td className="py-3 pr-4 align-top text-sm text-foreground">
        {row.description}
        {row.status && (
          <span
            className={`ml-2 inline-flex items-center rounded-sm px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide ${invoiceStatusTone(row.status)}`}
          >
            {row.status}
          </span>
        )}
      </td>
      <td className="py-3 pr-4 align-top text-right text-sm font-medium tabular-nums">
        {row.balance_delta > 0 ? "+" : row.balance_delta < 0 ? "−" : ""}
        {formatCents(Math.abs(row.amount_cents))}
      </td>
      <td className="py-3 align-top text-right text-sm tabular-nums text-muted-foreground">
        {formatCents(row.running_balance_cents)}
      </td>
    </tr>
  );
}

/**
 * PatientLedgerCard — shows a patient's chronological financial ledger.
 *
 * Props:
 *   patientId   UUID string
 *   title       optional override
 */
export default function PatientLedgerCard({ patientId, title = "Billing & ledger" }) {
  const { payload, loading, error } = usePatientLedger(patientId);

  const rows = useMemo(() => payload?.rows || [], [payload]);

  return (
    <section
      data-testid="patient-ledger-card"
      className="rounded-sm border border-border bg-card p-6"
    >
      <header className="mb-4 flex items-baseline justify-between">
        <h2 className="font-display text-2xl font-medium tracking-tight">
          {title}
        </h2>
        {payload && (
          <div className="text-right">
            <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              Current balance
            </div>
            <div
              data-testid="ledger-balance"
              className="font-display text-3xl font-medium tabular-nums"
            >
              {formatCents(payload.running_balance_cents)}
            </div>
          </div>
        )}
      </header>

      {loading && (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-10 w-full rounded-sm" />
          ))}
        </div>
      )}

      {error && !loading && (
        <div
          data-testid="ledger-error"
          className="rounded-sm border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
        >
          {error}
        </div>
      )}

      {!loading && !error && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No billing activity yet.
        </p>
      )}

      {!loading && rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full table-auto text-sm">
            <thead>
              <tr className="border-b border-border text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
                <th className="py-2 pr-4">When</th>
                <th className="py-2 pr-4">Type</th>
                <th className="py-2 pr-4">Description</th>
                <th className="py-2 pr-4 text-right">Amount</th>
                <th className="py-2 text-right">Running balance</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => <LedgerRow key={row.id} row={row} />)}
            </tbody>
          </table>
        </div>
      )}

      {payload?.totals && (
        <footer className="mt-4 grid grid-cols-2 gap-3 border-t border-border pt-4 text-xs sm:grid-cols-4">
          <Stat label="Charges" value={formatCents(payload.totals.charges_cents)} />
          <Stat label="Payments" value={formatCents(payload.totals.payments_cents)} />
          <Stat label="Adjustments" value={formatCents(payload.totals.adjustments_cents)} />
          <Stat label="Refunds" value={formatCents(payload.totals.refunds_cents)} />
        </footer>
      )}
      {/* Accessible note; safe-guard when usePatientLedger hasn't loaded yet. */}
      <span className="sr-only" data-testid="ledger-date-sort-note">
        rows sorted oldest to newest
      </span>
      {formatDate && null}
    </section>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground">{label}</div>
      <div className="font-medium tabular-nums">{value}</div>
    </div>
  );
}
