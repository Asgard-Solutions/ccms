import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Shield, Search, Download, Filter } from "lucide-react";
import { api } from "../api/client";
import { formatDateTime, relativeFromNow } from "../utils/time";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Skeleton } from "../components/ui/skeleton";
import { toast } from "sonner";

const QUICK_FILTERS = [
  { v: "all", l: "All activity", params: {} },
  { v: "phi", l: "PHI access", params: { phi_accessed: true } },
  { v: "auth", l: "Authentication", params: { action: "auth" } },
  { v: "patient", l: "Patient changes", params: { entity_type: "patient" } },
  { v: "emergency", l: "Break-glass", params: { action: "patient.unmasked" } },
  { v: "user_admin", l: "User admin", params: { action: "user" } },
];

function outcomeChip(outcome) {
  const map = {
    success: "bg-[#EDF2EE] text-[#526B58]",
    failure: "bg-[#FBF1EE] text-[#C76D54]",
  };
  return (
    <span
      className={`rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
        map[outcome] || "bg-stone-100"
      }`}
    >
      {outcome}
    </span>
  );
}

function getApiBase() {
  return process.env.REACT_APP_BACKEND_URL;
}

export default function AuditLog() {
  const [rows, setRows] = useState(null);
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const [actorEmail, setActorEmail] = useState("");
  const [entityId, setEntityId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [limit, setLimit] = useState(200);
  const [exporting, setExporting] = useState(false);

  const activeParams = useMemo(() => {
    const p = { limit, ...QUICK_FILTERS.find((f) => f.v === filter).params };
    if (actorEmail.trim()) p.actor_email = actorEmail.trim();
    if (entityId.trim()) p.entity_id = entityId.trim();
    if (dateFrom) p.date_from = new Date(dateFrom).toISOString();
    if (dateTo) p.date_to = new Date(dateTo).toISOString();
    return p;
  }, [filter, actorEmail, entityId, dateFrom, dateTo, limit]);

  useEffect(() => {
    (async () => {
      setRows(null);
      try {
        const { data } = await api.get("/audit-logs", { params: activeParams });
        setRows(data);
      } catch {
        setRows([]);
      }
    })();
  }, [activeParams]);

  const filtered = useMemo(() => {
    if (!rows) return null;
    const needle = q.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((r) =>
      [r.action, r.actor_email, r.entity_id, r.reason]
        .filter(Boolean)
        .some((s) => s.toLowerCase().includes(needle))
    );
  }, [rows, q]);

  async function downloadCsv() {
    setExporting(true);
    try {
      const { limit: _l, ...exportParams } = activeParams;
      const qs = new URLSearchParams({
        ...Object.fromEntries(
          Object.entries(exportParams).filter(([, v]) => v !== undefined && v !== null && v !== "")
        ),
        limit: 10000,
      }).toString();
      // Use fetch directly so we can stream the blob with cookies.
      const url = `${getApiBase()}/api/audit-logs/export.csv?${qs}`;
      const res = await fetch(url, { credentials: "include" });
      if (!res.ok) {
        toast.error(`Export failed (HTTP ${res.status})`);
        return;
      }
      const blob = await res.blob();
      const dl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = dl;
      a.download = `ccms_audit_${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(dl);
      toast.success("Audit log exported");
    } catch (e) {
      toast.error(e?.message || "Export failed");
    } finally {
      setExporting(false);
    }
  }

  function resetFilters() {
    setFilter("all");
    setQ("");
    setActorEmail("");
    setEntityId("");
    setDateFrom("");
    setDateTo("");
    setLimit(200);
  }

  return (
    <div data-testid="audit-page" className="space-y-8 animate-in fade-in duration-300">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
            Compliance
          </span>
          <h1 className="mt-2 font-['Outfit'] text-4xl font-medium tracking-tight">
            Audit log
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-[#5C6A61]">
            Every login, view, mutation, unmask and break-glass event. Retained
            for 7 years in line with HIPAA technical safeguard recommendations.
          </p>
        </div>
        <Button
          data-testid="audit-export-csv"
          onClick={downloadCsv}
          disabled={exporting}
          className="rounded-sm bg-[#1F2924] text-white hover:bg-[#0F1A15]"
        >
          <Download className="mr-2 h-4 w-4" />
          {exporting ? "Exporting…" : "Export CSV"}
        </Button>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        {QUICK_FILTERS.map((f) => (
          <button
            key={f.v}
            data-testid={`audit-filter-${f.v}`}
            onClick={() => setFilter(f.v)}
            className={`rounded-sm border px-3 py-1.5 text-sm font-medium transition-colors ${
              filter === f.v
                ? "border-[#7B9A82] bg-[#EDF2EE] text-[#526B58]"
                : "border-stone-200 bg-white text-[#5C6A61] hover:bg-[#F5F5F0]"
            }`}
          >
            {f.l}
          </button>
        ))}
      </div>

      <div className="rounded-sm border border-stone-200 bg-white p-4">
        <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
          <Filter className="h-3.5 w-3.5" /> Advanced filters
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
          <div>
            <Label className="text-[11px] text-[#5C6A61]">Actor email</Label>
            <Input
              data-testid="audit-actor-email"
              value={actorEmail}
              onChange={(e) => setActorEmail(e.target.value)}
              placeholder="e.g. doctor@"
              className="h-9 rounded-sm"
            />
          </div>
          <div>
            <Label className="text-[11px] text-[#5C6A61]">Entity id</Label>
            <Input
              data-testid="audit-entity-id"
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
              placeholder="patient / user id"
              className="h-9 rounded-sm"
            />
          </div>
          <div>
            <Label className="text-[11px] text-[#5C6A61]">From (UTC)</Label>
            <Input
              data-testid="audit-date-from"
              type="datetime-local"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="h-9 rounded-sm"
            />
          </div>
          <div>
            <Label className="text-[11px] text-[#5C6A61]">To (UTC)</Label>
            <Input
              data-testid="audit-date-to"
              type="datetime-local"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="h-9 rounded-sm"
            />
          </div>
          <div>
            <Label className="text-[11px] text-[#5C6A61]">Limit</Label>
            <select
              data-testid="audit-limit"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="h-9 w-full rounded-sm border border-stone-200 bg-white px-2 text-sm"
            >
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={200}>200</option>
              <option value={500}>500</option>
            </select>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <div className="relative max-w-xs flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#A3AFA7]" />
            <Input
              data-testid="audit-search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="actor, action, entity id, reason…"
              className="h-9 rounded-sm border-stone-200 pl-9"
            />
          </div>
          <Button
            data-testid="audit-reset-filters"
            variant="outline"
            onClick={resetFilters}
            className="h-9 rounded-sm"
          >
            Reset
          </Button>
          <span data-testid="audit-result-count" className="ml-auto text-xs text-[#5C6A61]">
            {filtered ? `${filtered.length} of ${rows?.length ?? 0} rows (capped at ${limit})` : "loading…"}
          </span>
        </div>
      </div>

      {filtered === null ? (
        <Skeleton className="h-80 rounded-sm" />
      ) : filtered.length === 0 ? (
        <div className="rounded-sm border border-dashed border-stone-200 bg-white p-16 text-center">
          <Shield className="mx-auto h-10 w-10 text-[#A3AFA7]" />
          <p className="mt-4 font-['Outfit'] text-lg">No matching audit entries</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-sm border border-stone-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-stone-200 bg-[#FAF9F6] text-xs font-semibold uppercase tracking-wider text-[#5C6A61]">
              <tr>
                <th className="px-4 py-3">When</th>
                <th className="px-4 py-3">Action</th>
                <th className="px-4 py-3">Actor</th>
                <th className="px-4 py-3">Entity</th>
                <th className="px-4 py-3">Reason / meta</th>
                <th className="px-4 py-3">Outcome</th>
                <th className="px-4 py-3">PHI</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr
                  key={r.id}
                  data-testid={`audit-row-${r.id}`}
                  className="border-b border-stone-100 last:border-b-0 hover:bg-[#F5F5F0]/50"
                >
                  <td className="px-4 py-3 align-top text-[#1F2924]">
                    <div>{formatDateTime(r.created_at)}</div>
                    <div className="text-xs text-[#5C6A61]">{relativeFromNow(r.created_at)}</div>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <code className="font-mono text-xs">{r.action}</code>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div>{r.actor_email || "—"}</div>
                    <div className="text-[11px] uppercase tracking-wider text-[#5C6A61]">
                      {r.actor_role || ""}
                    </div>
                    {r.ip && (
                      <div className="mt-1 font-mono text-[10px] text-[#A3AFA7]">{r.ip}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 align-top">
                    {r.entity_type ? (
                      <>
                        <div className="text-xs text-[#5C6A61]">{r.entity_type}</div>
                        <div className="truncate font-mono text-[11px]">
                          {r.entity_id ? (
                            r.entity_type === "patient" ? (
                              <Link
                                to={`/patients/${r.entity_id}`}
                                className="hover:underline"
                              >
                                {r.entity_id}
                              </Link>
                            ) : (
                              r.entity_id
                            )
                          ) : (
                            "—"
                          )}
                        </div>
                      </>
                    ) : (
                      <span className="text-[#A3AFA7]">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 align-top text-xs text-[#5C6A61]">
                    {r.reason ? (
                      <span className="text-[#1F2924]">“{r.reason}”</span>
                    ) : null}
                    {r.metadata && Object.keys(r.metadata).length > 0 && (
                      <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[11px] text-[#5C6A61]">
                        {JSON.stringify(r.metadata, null, 0)}
                      </pre>
                    )}
                  </td>
                  <td className="px-4 py-3 align-top">{outcomeChip(r.outcome)}</td>
                  <td className="px-4 py-3 align-top">
                    {r.phi_accessed ? (
                      <span className="rounded-sm bg-[#FDF6ED] px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-[#D4A373]">
                        PHI
                      </span>
                    ) : (
                      <span className="text-[#A3AFA7]">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
