import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  FileStack,
  Filter,
  Search,
  Users,
} from "lucide-react";
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
import {
  CLAIM_STATUS_LABELS,
  CANONICAL_STATUS_LABELS,
  CANONICAL_STATUS_ORDER,
  QUEUE_KEYS,
  canonicalStatusLabel,
  canonicalStatusTone,
  claimEventLabel,
  claimStatusTone,
  useClaimsQueueV2,
} from "./useClaims";

const ALL_KEY = "all";

const STATUS_OPTIONS = [
  { v: "all", l: "All statuses" },
  ...Object.entries(CLAIM_STATUS_LABELS).map(([v, l]) => ({ v, l })),
];

const PAGE_SIZES = [10, 25, 50, 100];

// Human-friendly column definitions. `sortKey` is null for columns
// that don't map cleanly to a single server-side field.
const COLUMNS = [
  { id: "claim",         label: "Claim",         sortKey: null },
  { id: "patient",       label: "Patient",       sortKey: null },
  { id: "service_dates", label: "Service dates", sortKey: "service_date_from" },
  { id: "billed",        label: "Billed",        sortKey: "billed_cents",
    align: "right" },
  { id: "status",        label: "Status",        sortKey: "status" },
  { id: "assignee",      label: "Assignee",      sortKey: null },
  { id: "last_activity", label: "Last activity", sortKey: "updated_at" },
];

export default function ClaimsQueue() {
  const [tab, setTab] = useState(ALL_KEY);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [sort, setSort] = useState("updated_at:desc");

  // Filters
  const [status, setStatus] = useState("all");
  const [canonicalStatus, setCanonicalStatus] = useState("all");
  const [payerId, setPayerId] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [ageDays, setAgeDays] = useState("");

  const filters = useMemo(() => ({
    status_in: status !== "all" ? [status] : null,
    canonical_status_in: canonicalStatus !== "all" ? [canonicalStatus] : null,
    payer_id: payerId || null,
    assigned_to: assignedTo || null,
    age_days: ageDays ? Number(ageDays) : null,
  }), [status, canonicalStatus, payerId, assignedTo, ageDays]);

  const { data, loading } = useClaimsQueueV2({
    tab, page, pageSize, sort, filters,
  });

  const rows = data?.rows || [];
  const total = data?.total || 0;
  const summary = data?.summary || {
    shown: 0, ready: 0, needs_fixes: 0, billed_total_cents: 0,
  };
  const tabCounts = data?.tab_counts || {};
  const filterOptions = data?.filter_options || { payers: [], assignees: [] };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const hasActiveFilters =
    status !== "all" || canonicalStatus !== "all" || payerId || assignedTo || ageDays;
  const isEmptyView = !loading && rows.length === 0;

  function onSortClick(sortKey) {
    if (!sortKey) return;
    const [curField, curDir] = sort.split(":");
    if (curField === sortKey) {
      setSort(`${sortKey}:${curDir === "asc" ? "desc" : "asc"}`);
    } else {
      setSort(`${sortKey}:desc`);
    }
    setPage(1);
  }

  function setTabAndReset(newTab) {
    setTab(newTab);
    setPage(1);
  }

  function resetFilters() {
    setStatus("all");
    setCanonicalStatus("all");
    setPayerId("");
    setAssignedTo("");
    setAgeDays("");
    setPage(1);
  }

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
          <p className="mt-2 max-w-xl text-sm text-muted-foreground">
            Operational workspace for claim submission, follow-up, and fixes.
            Rows show the latest activity from the canonical event stream.
          </p>
        </div>
        <Button asChild variant="outline" className="rounded-sm">
          <Link to="/billing" data-testid="claims-back-btn">
            ← Back to dashboard
          </Link>
        </Button>
      </header>

      <Tabs value={tab} onValueChange={setTabAndReset}>
        <TabsList data-testid="claims-queue-tabs" className="rounded-sm">
          <TabsTrigger value={ALL_KEY} data-testid="tab-all">
            All
            <CountChip n={tabCounts.all} />
          </TabsTrigger>
          {QUEUE_KEYS.map((q) => (
            <TabsTrigger
              key={q.key}
              value={q.key}
              data-testid={`tab-${q.key}`}
            >
              {q.label}
              <CountChip n={tabCounts[q.key]} />
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div className="grid gap-4 sm:grid-cols-4">
        <Stat
          testid="stat-shown"
          label="Shown"
          value={loading ? "—" : summary.shown}
          hint={total ? `of ${total.toLocaleString()} total` : null}
        />
        <Stat
          testid="stat-ready"
          label="Ready"
          value={loading ? "—" : summary.ready}
          tone="primary"
        />
        <Stat
          testid="stat-needs-fixes"
          label="Needs fixes"
          value={loading ? "—" : summary.needs_fixes}
          tone="destructive"
        />
        <Stat
          testid="stat-billed-total"
          label="Billed total"
          value={loading ? "—" : formatCents(summary.billed_total_cents)}
        />
      </div>

      <FilterBar
        status={status} setStatus={setStatus}
        canonicalStatus={canonicalStatus} setCanonicalStatus={setCanonicalStatus}
        payerId={payerId} setPayerId={setPayerId}
        assignedTo={assignedTo} setAssignedTo={setAssignedTo}
        ageDays={ageDays} setAgeDays={setAgeDays}
        payers={filterOptions.payers}
        assignees={filterOptions.assignees}
        canonicalStatuses={filterOptions.canonical_statuses}
        hasActiveFilters={!!hasActiveFilters}
        onReset={resetFilters}
      />

      <section className="overflow-hidden rounded-sm border border-border bg-card">
        {loading ? (
          <TableSkeleton />
        ) : isEmptyView && hasActiveFilters ? (
          <NoResultsState onReset={resetFilters} />
        ) : isEmptyView ? (
          <EmptyState tab={tab} />
        ) : (
          <>
            <table className="w-full table-auto text-sm">
              <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
                <tr>
                  {COLUMNS.map((c) => (
                    <SortableHeader
                      key={c.id}
                      column={c}
                      currentSort={sort}
                      onClick={onSortClick}
                    />
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <ClaimRow key={c.id} c={c} />
                ))}
              </tbody>
            </table>
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              totalPages={totalPages}
              onPageChange={setPage}
              onPageSizeChange={(n) => { setPageSize(n); setPage(1); }}
            />
          </>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------
function CountChip({ n }) {
  if (n === undefined || n === null) return null;
  return (
    <span className="ml-2 rounded-sm bg-muted px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-muted-foreground">
      {n}
    </span>
  );
}

function Stat({ label, value, hint, tone, testid }) {
  const toneClass =
    tone === "primary" ? "text-primary" :
    tone === "destructive" ? "text-destructive" : "";
  return (
    <div
      data-testid={testid}
      className="rounded-sm border border-border bg-card p-4"
    >
      <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </div>
      <div className={`mt-1 font-display text-2xl tabular-nums ${toneClass}`}>
        {value}
      </div>
      {hint ? (
        <div className="mt-0.5 text-[11px] text-muted-foreground">{hint}</div>
      ) : null}
    </div>
  );
}

function SortableHeader({ column, currentSort, onClick }) {
  const [field, dir] = (currentSort || "").split(":");
  const active = column.sortKey && field === column.sortKey;
  const sortable = !!column.sortKey;
  const Icon = active
    ? (dir === "asc" ? ArrowUp : ArrowDown)
    : ArrowUpDown;
  return (
    <th
      className={`px-4 py-2 ${column.align === "right" ? "text-right" : ""} ${sortable ? "cursor-pointer select-none hover:text-foreground" : ""}`}
      onClick={() => sortable && onClick(column.sortKey)}
      data-testid={`claims-col-${column.id}`}
    >
      <span className="inline-flex items-center gap-1">
        {column.label}
        {sortable && (
          <Icon className={`h-3 w-3 ${active ? "text-foreground" : "opacity-40"}`} />
        )}
      </span>
    </th>
  );
}

function ClaimRow({ c }) {
  const lastEventLabel = claimEventLabel(c.last_event);
  const lastAt = c.last_event_at || c.updated_at;
  const patientDisplay = c.patient_name
    || (c.patient_mrn ? `MRN ${c.patient_mrn}` : c.patient_id?.slice(0, 8))
    || "—";
  const assigneeDisplay = c.assignee_name
    || (c.assigned_to ? c.assigned_to.slice(0, 8) : null);
  return (
    <tr
      data-testid={`claim-row-${c.id}`}
      className="border-t border-border transition-colors hover:bg-muted/30"
    >
      <td className="px-4 py-3 font-medium">
        <Link
          to={`/billing/claims/${c.id}`}
          className="hover:underline"
          data-testid={`claim-row-link-${c.id}`}
        >
          {c.id.slice(0, 8)}
        </Link>
        {c.payer_name ? (
          <div className="text-[11px] text-muted-foreground">{c.payer_name}</div>
        ) : null}
      </td>
      <td className="px-4 py-3">
        <Link to={`/patients/${c.patient_id}`} className="hover:underline">
          {patientDisplay}
        </Link>
        {c.patient_mrn && c.patient_name ? (
          <div className="text-[11px] text-muted-foreground">MRN {c.patient_mrn}</div>
        ) : null}
      </td>
      <td className="px-4 py-3 text-muted-foreground">
        {c.service_date_from} → {c.service_date_to}
      </td>
      <td className="px-4 py-3 text-right tabular-nums">
        {formatCents(c.billed_cents)}
      </td>
      <td className="px-4 py-3">
        <span
          className={`inline-flex items-center rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${canonicalStatusTone(c.canonical_status)}`}
          data-testid={`claim-row-canonical-${c.id}`}
          title={c.status ? `Raw: ${CLAIM_STATUS_LABELS[c.status] || c.status}` : undefined}
        >
          {canonicalStatusLabel(c.canonical_status)}
        </span>
        {/* Raw status is kept as a subtle secondary chip for operators
            who need the fine-grained state. */}
        {c.status && c.canonical_status !== c.status && (
          <span
            className={`ml-1.5 inline-flex items-center rounded-sm px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${claimStatusTone(c.status)}`}
            data-testid={`claim-row-status-${c.id}`}
          >
            {CLAIM_STATUS_LABELS[c.status] || c.status}
          </span>
        )}
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
        {assigneeDisplay || "—"}
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
}

function FilterBar({
  status, setStatus,
  canonicalStatus, setCanonicalStatus,
  payerId, setPayerId, assignedTo, setAssignedTo,
  ageDays, setAgeDays, payers, assignees,
  canonicalStatuses, hasActiveFilters, onReset,
}) {
  // Fall back to CANONICAL_STATUS_ORDER when the server hasn't
  // populated filter_options yet (first render).
  const canonicalOptions = canonicalStatuses?.length
    ? canonicalStatuses
    : CANONICAL_STATUS_ORDER.map((v) => ({
        value: v, label: CANONICAL_STATUS_LABELS[v],
      }));
  return (
    <section
      data-testid="claims-filter-bar"
      className="flex flex-wrap items-end gap-3 rounded-sm border border-border bg-card p-4"
    >
      <div className="flex min-w-[10rem] flex-col gap-1">
        <Label className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
          <Filter className="mr-1 inline h-3 w-3" /> Lifecycle
        </Label>
        <Select value={canonicalStatus} onValueChange={setCanonicalStatus}>
          <SelectTrigger data-testid="claims-canonical-filter" className="w-52">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All lifecycle states</SelectItem>
            {canonicalOptions.map((o) => (
              <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex min-w-[10rem] flex-col gap-1">
        <Label className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
          Raw status
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
        {(assignees || []).length > 0 ? (
          <Select
            value={assignedTo || "any"}
            onValueChange={(v) => setAssignedTo(v === "any" ? "" : v)}
          >
            <SelectTrigger data-testid="claims-assignee-filter" className="w-56">
              <SelectValue placeholder="Any" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="any">Any</SelectItem>
              {assignees.map((u) => (
                <SelectItem key={u.id} value={u.id}>{u.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <Input
            data-testid="claims-assignee-filter"
            placeholder="user id"
            value={assignedTo}
            onChange={(e) => setAssignedTo(e.target.value)}
            className="w-48"
          />
        )}
      </div>

      {hasActiveFilters && (
        <Button
          data-testid="claims-filters-reset"
          variant="ghost"
          size="sm"
          onClick={onReset}
          className="h-8 self-end text-xs text-muted-foreground"
        >
          Clear filters
        </Button>
      )}
    </section>
  );
}

function Pagination({ page, pageSize, total, totalPages, onPageChange, onPageSizeChange }) {
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);
  return (
    <div
      data-testid="claims-pagination"
      className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3 text-xs text-muted-foreground"
    >
      <span>
        {start.toLocaleString()}–{end.toLocaleString()} of {total.toLocaleString()}
      </span>
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-2">
          Rows:
          <Select
            value={String(pageSize)}
            onValueChange={(v) => onPageSizeChange(Number(v))}
          >
            <SelectTrigger
              data-testid="claims-page-size"
              className="h-7 w-20"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PAGE_SIZES.map((n) => (
                <SelectItem key={n} value={String(n)}>{n}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </span>
        <div className="flex items-center gap-1">
          <Button
            data-testid="claims-prev-page"
            variant="outline"
            size="sm"
            className="h-7 w-7 rounded-sm p-0"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            <ChevronLeft className="h-3 w-3" />
          </Button>
          <span className="tabular-nums" data-testid="claims-page-indicator">
            {page} / {totalPages}
          </span>
          <Button
            data-testid="claims-next-page"
            variant="outline"
            size="sm"
            className="h-7 w-7 rounded-sm p-0"
            disabled={page >= totalPages}
            onClick={() => onPageChange(page + 1)}
          >
            <ChevronRight className="h-3 w-3" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="space-y-2 p-4">
      {[0, 1, 2, 3, 4].map((i) => (
        <Skeleton key={i} className="h-10 w-full rounded-sm" />
      ))}
    </div>
  );
}

function EmptyState({ tab }) {
  return (
    <div
      data-testid="claims-empty-state"
      className="flex flex-col items-center gap-2 py-16 text-muted-foreground"
    >
      <FileStack className="h-8 w-8" />
      <p className="text-sm">
        {tab === "all"
          ? "No claims yet. Create one from a patient's encounter to get started."
          : "Nothing here right now — this queue is clear."}
      </p>
    </div>
  );
}

function NoResultsState({ onReset }) {
  return (
    <div
      data-testid="claims-no-results"
      className="flex flex-col items-center gap-3 py-16 text-muted-foreground"
    >
      <Search className="h-8 w-8" />
      <p className="text-sm">No claims match the current filters.</p>
      <Button
        variant="outline"
        size="sm"
        onClick={onReset}
        data-testid="claims-no-results-reset"
      >
        Clear filters
      </Button>
    </div>
  );
}
