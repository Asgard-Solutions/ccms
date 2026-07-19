/**
 * /portal/statements — patient-facing list of their own statements.
 *
 * Consumes:
 *   GET /api/billing/me/statements
 *   GET /api/billing/me/statements/{id}.pdf
 *
 * Shows the insurance-paid / patient-paid / balance breakdown inline
 * so the patient doesn't need to download the PDF just to see what's
 * owed.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Download, FileText, Wallet } from "lucide-react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import { formatCents } from "../utils/money";
import { formatDateTime } from "../utils/time";
import { listMyStatements, myStatementPdfUrl } from "../pages/billing/useRemittance";

function InvoiceBreakdownRow({ row }) {
  return (
    <tr data-testid={`portal-breakdown-${row.invoice_id}`} className="border-t border-border">
      <td className="py-2 pr-2 text-xs font-mono text-muted-foreground">
        {row.invoice_id.slice(0, 8)}
      </td>
      <td className="py-2 pr-2 text-xs">{(row.issued_at || "").slice(0, 10)}</td>
      <td className="py-2 pr-2 text-right tabular-nums">{formatCents(row.billed_cents)}</td>
      <td className="py-2 pr-2 text-right tabular-nums text-muted-foreground">
        {formatCents(row.insurance_paid_cents)}
      </td>
      <td className="py-2 pr-2 text-right tabular-nums text-muted-foreground">
        {formatCents(row.adjustments_cents)}
      </td>
      <td className="py-2 pr-2 text-right tabular-nums text-muted-foreground">
        {formatCents(row.patient_paid_cents)}
      </td>
      <td className="py-2 pr-2 text-right font-semibold tabular-nums">
        {formatCents(row.balance_cents)}
      </td>
    </tr>
  );
}

function StatementCard({ stmt }) {
  const [open, setOpen] = useState(false);
  const hasBreakdown = (stmt.invoice_breakdown || []).length > 0;

  return (
    <article
      data-testid={`portal-statement-card-${stmt.id}`}
      className="rounded-sm border border-border bg-card p-5"
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Statement · {stmt.as_of_date}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Generated {formatDateTime(stmt.generated_at)}
            {stmt.sent_at ? ` · sent via ${stmt.sent_via}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge
            variant="outline"
            data-testid={`portal-statement-balance-${stmt.id}`}
            className="rounded-sm"
          >
            <Wallet className="mr-1 h-3 w-3" />
            {formatCents(stmt.total_balance_cents)} due
          </Badge>
          <a
            href={myStatementPdfUrl(stmt.id)}
            target="_blank"
            rel="noreferrer"
            data-testid={`portal-statement-pdf-${stmt.id}`}
          >
            <Button size="sm" variant="outline" className="h-8 rounded-sm">
              <Download className="mr-1 h-3.5 w-3.5" />
              Download PDF
            </Button>
          </a>
        </div>
      </header>

      {hasBreakdown && (
        <div className="mt-3">
          <button
            type="button"
            data-testid={`portal-statement-toggle-${stmt.id}`}
            onClick={() => setOpen((v) => !v)}
            className="text-xs font-medium text-primary hover:underline"
          >
            {open ? "Hide" : "Show"} invoice breakdown ({stmt.invoice_count})
          </button>
          {open && (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-2">Invoice</th>
                    <th className="py-1 pr-2">Issued</th>
                    <th className="py-1 pr-2 text-right">Billed</th>
                    <th className="py-1 pr-2 text-right">Insurance paid</th>
                    <th className="py-1 pr-2 text-right">Adjust.</th>
                    <th className="py-1 pr-2 text-right">You paid</th>
                    <th className="py-1 pr-2 text-right">Balance</th>
                  </tr>
                </thead>
                <tbody>
                  {stmt.invoice_breakdown.map((row) => (
                    <InvoiceBreakdownRow key={row.invoice_id} row={row} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

export default function PortalStatements() {
  const [rows, setRows] = useState(null);

  useEffect(() => {
    listMyStatements()
      .then(setRows)
      .catch((e) => {
        toast.error(e?.response?.data?.detail || "Could not load statements");
        setRows([]);
      });
  }, []);

  return (
    <div data-testid="portal-statements-page" className="space-y-6">
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Billing
        </span>
        <h1 className="mt-1 font-display text-3xl font-medium tracking-tight">Statements</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          See what was billed, what insurance covered, and what's still owed.
        </p>
      </header>

      {rows === null ? (
        <div className="space-y-3">
          <Skeleton className="h-24 rounded-sm" />
          <Skeleton className="h-24 rounded-sm" />
        </div>
      ) : rows.length === 0 ? (
        <div
          data-testid="portal-statements-empty"
          className="flex flex-col items-center gap-3 rounded-sm border border-dashed border-border px-5 py-14 text-center"
        >
          <FileText className="h-8 w-8 text-muted-foreground" />
          <p className="text-sm font-medium">No statements yet</p>
          <p className="max-w-sm text-xs text-muted-foreground">
            When your clinic generates a statement it will appear here.
            You'll also receive an email if we have one on file.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {rows.map((s) => <StatementCard key={s.id} stmt={s} />)}
        </div>
      )}
    </div>
  );
}
