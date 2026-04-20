import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FileStack, Filter } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { formatCents } from "../../utils/money";
import { formatDateTime } from "../../utils/time";
import {
  CLAIM_STATUS_LABELS,
  claimStatusTone,
  useClaims,
} from "./useClaims";

const STATUS_OPTIONS = [
  { v: "all", l: "All statuses" },
  ...Object.entries(CLAIM_STATUS_LABELS).map(([v, l]) => ({ v, l })),
];

export default function ClaimsQueue() {
  const [status, setStatus] = useState("all");
  const { rows, loading } = useClaims({
    status: status === "all" ? null : status,
  });

  const summary = useMemo(() => ({
    total: rows.length,
    billed: rows.reduce((a, c) => a + (c.billed_cents || 0), 0),
    ready: rows.filter((c) => c.status === "ready").length,
    validationFailed: rows.filter((c) => c.status === "validation_failed").length,
  }), [rows]);

  return (
    <div data-testid="claims-queue" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Billing
          </div>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
            Claims queue
          </h1>
        </div>
        <Button asChild variant="outline" className="rounded-sm">
          <Link to="/billing" data-testid="claims-back-btn">
            Back to dashboard
          </Link>
        </Button>
      </header>

      <div className="grid gap-4 sm:grid-cols-4">
        <Stat label="Total" value={summary.total} />
        <Stat label="Ready" value={summary.ready} tone="primary" />
        <Stat label="Needs fixes" value={summary.validationFailed} tone="destructive" />
        <Stat label="Billed total" value={formatCents(summary.billed)} />
      </div>

      <div className="flex items-center gap-3">
        <Filter className="h-4 w-4 text-muted-foreground" />
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger data-testid="claims-status-filter" className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((o) => (
              <SelectItem key={o.v} value={o.v}>{o.l}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <section className="overflow-hidden rounded-sm border border-border bg-card">
        {loading ? (
          <div className="p-4 space-y-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-10 w-full rounded-sm" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-muted-foreground">
            <FileStack className="h-6 w-6" />
            <p className="text-sm">No claims match this filter.</p>
          </div>
        ) : (
          <table className="w-full table-auto text-sm">
            <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-4 py-2">Claim</th>
                <th className="px-4 py-2">Patient</th>
                <th className="px-4 py-2">Service dates</th>
                <th className="px-4 py-2 text-right">Billed</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Last validated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr
                  key={c.id}
                  data-testid={`claim-row-${c.id}`}
                  className="border-t border-border hover:bg-muted/30"
                >
                  <td className="px-4 py-3 font-medium">
                    <Link
                      to={`/billing/claims/${c.id}`}
                      className="hover:underline"
                    >
                      {c.id.slice(0, 8)}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    <Link to={`/patients/${c.patient_id}`} className="hover:underline">
                      {c.patient_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {c.service_date_from} → {c.service_date_to}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatCents(c.billed_cents)}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${claimStatusTone(c.status)}`}
                    >
                      {CLAIM_STATUS_LABELS[c.status] || c.status}
                    </span>
                    {c.validation_error_count > 0 && (
                      <span className="ml-2 text-[11px] font-semibold text-destructive">
                        {c.validation_error_count} error{c.validation_error_count === 1 ? "" : "s"}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {c.validation_last_run_at
                      ? formatDateTime(c.validation_last_run_at)
                      : "—"}
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

function Stat({ label, value, tone }) {
  const toneClass = tone === "primary"
    ? "text-primary"
    : tone === "destructive"
      ? "text-destructive"
      : "text-foreground";
  return (
    <div className="rounded-sm border border-border bg-card p-5">
      <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">{label}</div>
      <div className={`mt-1 font-display text-3xl font-medium tabular-nums ${toneClass}`}>
        {value}
      </div>
    </div>
  );
}
