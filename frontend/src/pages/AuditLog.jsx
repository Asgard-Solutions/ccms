import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Shield, Search } from "lucide-react";
import { api } from "../api/client";
import { formatDateTime, relativeFromNow } from "../utils/time";
import { Input } from "../components/ui/input";
import { Skeleton } from "../components/ui/skeleton";

const FILTERS = [
  { v: "all", l: "All activity" },
  { v: "phi", l: "PHI access" },
  { v: "auth", l: "Authentication" },
  { v: "patient", l: "Patient changes" },
  { v: "emergency", l: "Break-glass" },
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

export default function AuditLog() {
  const [rows, setRows] = useState(null);
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");

  useEffect(() => {
    (async () => {
      setRows(null);
      try {
        const params = { limit: 200 };
        if (filter === "phi") params.phi_accessed = true;
        if (filter === "auth") params.action = "auth";
        if (filter === "patient") params.entity_type = "patient";
        if (filter === "emergency") params.action = "patient.unmasked";
        const { data } = await api.get("/audit-logs", { params });
        setRows(data);
      } catch {
        setRows([]);
      }
    })();
  }, [filter]);

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

  return (
    <div data-testid="audit-page" className="space-y-8 animate-in fade-in duration-300">
      <header>
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
      </header>

      <div className="flex flex-wrap items-center gap-3">
        {FILTERS.map((f) => (
          <button
            key={f.v}
            data-testid={`audit-filter-${f.v}`}
            onClick={() => setFilter(f.v)}
            className={`rounded-sm border px-4 py-1.5 text-sm font-medium transition-colors ${
              filter === f.v
                ? "border-[#7B9A82] bg-[#EDF2EE] text-[#526B58]"
                : "border-stone-200 bg-white text-[#5C6A61] hover:bg-[#F5F5F0]"
            }`}
          >
            {f.l}
          </button>
        ))}
        <div className="relative ml-auto max-w-xs flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#A3AFA7]" />
          <Input
            data-testid="audit-search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="actor, action, entity id…"
            className="h-9 rounded-sm border-stone-200 pl-9"
          />
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
