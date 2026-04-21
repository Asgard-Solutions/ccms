import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FileText, Filter } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { formatDateTime } from "../../utils/time";
import { formatCents } from "../../utils/money";
import {
  INVOICE_STATUS_LABELS,
  invoiceStatusTone,
  useInvoices,
} from "./useBilling";

const FILTER_OPTIONS = [
  { value: "all", label: "All statuses" },
  ...Object.entries(INVOICE_STATUS_LABELS).map(([value, label]) => ({
    value, label,
  })),
];

export default function InvoicesList() {
  const [statusFilter, setStatusFilter] = useState("all");
  const [searchText, setSearchText] = useState("");
  const { rows, loading } = useInvoices({
    status: statusFilter === "all" ? null : statusFilter,
  });

  const filtered = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) => r.id.toLowerCase().includes(q) ||
        (r.patient_id || "").toLowerCase().includes(q),
    );
  }, [rows, searchText]);

  return (
    <div data-testid="invoices-page" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Billing
          </div>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
            Invoices
          </h1>
        </div>
        <Button asChild variant="outline" className="rounded-sm">
          <Link to="/billing" data-testid="invoices-back-dashboard">
            Back to dashboard
          </Link>
        </Button>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <Filter className="h-4 w-4 text-muted-foreground" />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger data-testid="invoices-status-filter" className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FILTER_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          data-testid="invoices-search"
          placeholder="Search by ID…"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          className="max-w-xs"
        />
      </div>

      <section className="overflow-hidden rounded-sm border border-border bg-card">
        {loading ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10 w-full rounded-sm" />)}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-muted-foreground">
            <FileText className="h-6 w-6" />
            <p className="text-sm">No invoices match this filter.</p>
          </div>
        ) : (
          <table className="w-full table-auto text-sm">
            <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-4 py-2">Invoice</th>
                <th className="px-4 py-2">Patient</th>
                <th className="px-4 py-2">Issued</th>
                <th className="px-4 py-2 text-right">Total</th>
                <th className="px-4 py-2 text-right">Balance</th>
                <th className="px-4 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((inv) => (
                <tr
                  key={inv.id}
                  data-testid={`invoice-row-${inv.id}`}
                  className="border-t border-border hover:bg-muted/30"
                >
                  <td className="px-4 py-3 font-medium">
                    <Link
                      to={`/billing/invoices/${inv.id}`}
                      className="hover:underline"
                    >
                      {inv.id.slice(0, 8)}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    <Link
                      to={`/patients/${inv.patient_id}`}
                      className="hover:underline"
                    >
                      {inv.patient_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {inv.issued_at ? formatDateTime(inv.issued_at) : "—"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatCents(inv.total_cents)}
                  </td>
                  <td className="px-4 py-3 text-right font-medium tabular-nums">
                    {formatCents(inv.balance_cents)}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${invoiceStatusTone(inv.status)}`}
                    >
                      {INVOICE_STATUS_LABELS[inv.status] || inv.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
