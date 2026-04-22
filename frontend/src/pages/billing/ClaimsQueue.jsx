import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FileStack, Filter, Users } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { formatCents } from "../../utils/money";
import { formatDateTime } from "../../utils/time";
import { usePayers } from "./useBillingAdmin";
import {
  CLAIM_STATUS_LABELS,
  QUEUE_KEYS,
  claimEventLabel,
  claimStatusTone,
  useClaimQueue,
  useClaims,
} from "./useClaims";

const ALL_KEY = "all";

const STATUS_OPTIONS = [
  { v: "all", l: "All statuses" },
  ...Object.entries(CLAIM_STATUS_LABELS).map(([v, l]) => ({ v, l })),
];

export default function ClaimsQueue() {
  const [tab, setTab] = useState(ALL_KEY);
  const [status, setStatus] = useState("all");
  const [payerId, setPayerId] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [ageDays, setAgeDays] = useState("");

  const { rows: payers } = usePayers();

  // "All" uses the classic listing endpoint; named queues use the
  // dedicated queue endpoint (server-side filtering).
  const isNamedQueue = tab !== ALL_KEY;
  const allRows = useClaims({
    status: !isNamedQueue && status !== "all" ? status : null,
  });
  const queue = useClaimQueue({
    queue: isNamedQueue ? tab : null,
    filters: {
      payer_id: payerId || null,
      assigned_to: assignedTo || null,
      age_days: ageDays ? Number(ageDays) : null,
      status_in: status !== "all" ? [status] : null,
    },
  });

  const rows = isNamedQueue ? queue.rows : allRows.rows;
  const loading = isNamedQueue ? queue.loading : allRows.loading;

  const summary = useMemo(() => ({
    total: rows.length,
    billed: rows.reduce((a, c) => a + (c.billed_cents || 0), 0),
    ready: rows.filter((c) => c.status === "ready").length,
    needsFixes: rows.filter((c) =>
      c.status === "validation_failed"
      || (c.validation_error_count || 0) > 0).length,
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

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList data-testid="claims-queue-tabs" className="rounded-sm">
          <TabsTrigger value={ALL_KEY} data-testid="tab-all">All</TabsTrigger>
          {QUEUE_KEYS.map((q) => (
            <TabsTrigger
              key={q.key}
              value={q.key}
              data-testid={`tab-${q.key}`}
            >
              {q.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div className="grid gap-4 sm:grid-cols-4">
        <Stat label="Shown" value={summary.total} />
        <Stat label="Ready" value={summary.ready} tone="primary" />
        <Stat label="Needs fixes" value={summary.needsFixes} tone="destructive" />
        <Stat label="Billed total" value={formatCents(summary.billed)} />
      </div>

      <section
        data-testid="claims-filter-bar"
        className="flex flex-wrap items-end gap-3 rounded-sm border border-border bg-card p-4"
      >
        <div className="flex min-w-[10rem] flex-col gap-1">
          <Label className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            <Filter className="mr-1 inline h-3 w-3" /> Status
          </Label>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger data-testid="claims-status-filter" className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((o) => (
                <SelectItem key={o.v} value={o.v}>{o.l}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex min-w-[10rem] flex-col gap-1">
          <Label className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Payer
          </Label>
          <Select value={payerId || "any"} onValueChange={(v) => setPayerId(v === "any" ? "" : v)}>
            <SelectTrigger data-testid="claims-payer-filter" className="w-56">
              <SelectValue placeholder="Any payer" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="any">Any payer</SelectItem>
              {(payers || []).map((p) => (
                <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex min-w-[9rem] flex-col gap-1">
          <Label className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Age &gt; days
          </Label>
          <Input
            data-testid="claims-age-filter"
            type="number"
            min="0"
            placeholder="any"
            value={ageDays}
            onChange={(e) => setAgeDays(e.target.value)}
            className="w-28"
          />
        </div>

        <div className="flex min-w-[11rem] flex-col gap-1">
          <Label className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            <Users className="mr-1 inline h-3 w-3" /> Assignee
          </Label>
          <Input
            data-testid="claims-assignee-filter"
            placeholder="user id"
            value={assignedTo}
            onChange={(e) => setAssignedTo(e.target.value)}
            className="w-48"
          />
        </div>
      </section>

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
            <p className="text-sm">No claims match this view.</p>
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
                <th className="px-4 py-2">Assignee</th>
                <th className="px-4 py-2">Last activity</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const lastEventLabel = claimEventLabel(c.last_event);
                const lastAt = c.last_event_at || c.updated_at;
                return (
                <tr
                  key={c.id}
                  data-testid={`claim-row-${c.id}`}
                  className="border-t border-border hover:bg-muted/30"
                >
                  <td className="px-4 py-3 font-medium">
                    <Link
                      to={`/billing/claims/${c.id}`}
                      className="hover:underline"
                      data-testid={`claim-row-link-${c.id}`}
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
                      data-testid={`claim-row-status-${c.id}`}
                    >
                      {CLAIM_STATUS_LABELS[c.status] || c.status}
                    </span>
                    {c.validation_error_count > 0 && (
                      <span
                        data-testid={`claim-row-errors-${c.id}`}
                        className="ml-2 text-[11px] font-semibold text-destructive"
                      >
                        {c.validation_error_count} error{c.validation_error_count === 1 ? "" : "s"}
                      </span>
                    )}
                    {c.validation_warning_count > 0 && (
                      <span
                        data-testid={`claim-row-warnings-${c.id}`}
                        className="ml-2 text-[11px] font-semibold text-warning"
                      >
                        {c.validation_warning_count} warn
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {c.assigned_to ? c.assigned_to.slice(0, 8) : "—"}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {lastEventLabel ? (
                      <span
                        data-testid={`claim-row-last-event-${c.id}`}
                        className="inline-flex flex-col"
                      >
                        <span className="font-medium text-foreground">{lastEventLabel}</span>
                        <span>{lastAt ? formatDateTime(lastAt) : "—"}</span>
                      </span>
                    ) : (
                      lastAt ? formatDateTime(lastAt) : "—"
                    )}
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
