import { useEffect, useState } from "react";
import { ClipboardCheck, AlertTriangle, ShieldCheck, CircleDashed, XCircle, FileText, ExternalLink } from "lucide-react";
import { api } from "../api/client";
import { formatDateTime } from "../utils/time";
import { Skeleton } from "../components/ui/skeleton";

const STATUS_META = {
  implemented: {
    label: "Implemented",
    chip: "bg-[#EDF2EE] text-[#526B58]",
    dot: "bg-[#7B9A82]",
    icon: ShieldCheck,
  },
  partial: {
    label: "Partial",
    chip: "bg-[#FDF6ED] text-[#B5823E]",
    dot: "bg-[#D4A373]",
    icon: CircleDashed,
  },
  missing: {
    label: "Not implemented",
    chip: "bg-[#FBF1EE] text-[#C76D54]",
    dot: "bg-[#C76D54]",
    icon: XCircle,
  },
  out_of_app: {
    label: "Out of app scope",
    chip: "bg-stone-100 text-stone-600",
    dot: "bg-stone-400",
    icon: FileText,
  },
};

function StatChip({ value, label, testid }) {
  return (
    <div
      data-testid={testid}
      className="rounded-sm border border-stone-200 bg-white px-4 py-3"
    >
      <div className="font-['Outfit'] text-2xl font-medium text-[#1F2924]">{value}</div>
      <div className="mt-1 text-[11px] uppercase tracking-[0.15em] text-[#5C6A61]">{label}</div>
    </div>
  );
}

function Flag({ ok, label, testid }) {
  return (
    <div
      data-testid={testid}
      className="flex items-center justify-between gap-3 rounded-sm border border-stone-200 bg-white px-4 py-3"
    >
      <span className="text-sm text-[#1F2924]">{label}</span>
      <span
        className={`inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
          ok ? "bg-[#EDF2EE] text-[#526B58]" : "bg-[#FBF1EE] text-[#C76D54]"
        }`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-[#7B9A82]" : "bg-[#C76D54]"}`} />
        {ok ? "OK" : "Review"}
      </span>
    </div>
  );
}

function ControlRow({ c }) {
  const meta = STATUS_META[c.status] || STATUS_META.missing;
  const Icon = meta.icon;
  return (
    <tr data-testid={`control-row-${c.id}`} className="border-b border-stone-100 last:border-0">
      <td className="py-3 pr-4 align-top">
        <div className="flex items-start gap-2">
          <Icon className="mt-0.5 h-4 w-4 text-[#5C6A61]" />
          <div>
            <div className="font-mono text-[11px] uppercase tracking-wider text-[#5C6A61]">{c.id}</div>
            <div className="mt-0.5 text-sm text-[#1F2924]">{c.name}</div>
          </div>
        </div>
      </td>
      <td className="py-3 pr-4 align-top text-xs text-[#5C6A61]">{c.group}</td>
      <td className="py-3 pr-4 align-top">
        <div className="flex flex-wrap gap-1.5">
          {c.frameworks.map((f) => (
            <span
              key={f}
              className="rounded-sm bg-[#F5F5F0] px-1.5 py-0.5 text-[10px] font-medium text-[#5C6A61]"
            >
              {f}
            </span>
          ))}
        </div>
      </td>
      <td className="py-3 align-top">
        <span
          className={`inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${meta.chip}`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
          {meta.label}
        </span>
      </td>
    </tr>
  );
}

export default function Compliance() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [groupFilter, setGroupFilter] = useState("all");

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/compliance/overview");
        setData(data);
      } catch (e) {
        setError(e?.response?.data?.detail || "Failed to load compliance overview");
      }
    })();
  }, []);

  if (error) {
    return (
      <div data-testid="compliance-error" className="rounded-sm border border-[#E6D5CF] bg-[#FBF1EE] p-4 text-sm text-[#C76D54]">
        {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div data-testid="compliance-loading" className="space-y-4">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  const groups = ["all", ...Array.from(new Set(data.controls.map((c) => c.group)))];
  const filteredControls =
    groupFilter === "all"
      ? data.controls
      : data.controls.filter((c) => c.group === groupFilter);

  const env = data.environment;
  const envFlags = [
    { k: "cors_origin_locked", l: "CORS origin-locked" },
    { k: "frontend_url_configured", l: "FRONTEND_URL configured" },
    { k: "jwt_secret_strong", l: "JWT_SECRET ≥ 32 chars" },
    { k: "data_encryption_key_configured", l: "Data encryption key set" },
    { k: "mfa_issuer_configured", l: "MFA issuer configured" },
    { k: "redis_url_configured", l: "Redis URL configured" },
    { k: "mongo_read_url_distinct", l: "Read replica URL distinct" },
    { k: "admin_password_configured", l: "Admin seed password set" },
    { k: "redis_alive", l: "Redis reachable" },
  ];

  const readinessPct =
    data.readiness_score !== null && data.readiness_score !== undefined
      ? Math.round(data.readiness_score * 100)
      : null;

  return (
    <div data-testid="compliance-page" className="space-y-10 animate-in fade-in duration-300">
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
          Compliance
        </span>
        <h1 className="mt-2 font-['Outfit'] text-4xl font-medium tracking-tight">
          Internal readiness dashboard
        </h1>
        <p className="mt-3 max-w-3xl text-sm text-[#5C6A61]">
          {data.disclaimer}
        </p>
        <div
          data-testid="compliance-disclaimer"
          className="mt-4 flex items-start gap-2 rounded-sm border border-[#EDE0C7] bg-[#FDF6ED] p-3 text-xs text-[#8A6C33]"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
          <span>
            This page reflects application-layer signals only. Certification for SOC 2,
            CCPA and ISO 27001 requires independent audit, policy, HR and
            infrastructure evidence that does not live in this codebase.
          </span>
        </div>
      </header>

      {/* readiness snapshot */}
      <section data-testid="readiness-snapshot" className="space-y-4">
        <div className="flex items-baseline justify-between">
          <h2 className="font-['Outfit'] text-lg font-medium">Readiness snapshot</h2>
          <span className="text-xs text-[#5C6A61]">
            Generated {formatDateTime(data.generated_at)}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <StatChip
            testid="stat-score"
            value={readinessPct !== null ? `${readinessPct}%` : "—"}
            label="Control coverage"
          />
          <StatChip testid="stat-implemented" value={data.status_totals.implemented} label="Implemented" />
          <StatChip testid="stat-partial" value={data.status_totals.partial} label="Partial" />
          <StatChip testid="stat-missing" value={data.status_totals.missing} label="Missing" />
          <StatChip testid="stat-out-of-app" value={data.status_totals.out_of_app} label="Out of app scope" />
        </div>
      </section>

      {/* environment hardening */}
      <section data-testid="env-hardening" className="space-y-4">
        <h2 className="font-['Outfit'] text-lg font-medium">Environment hardening</h2>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {envFlags.map((f) => (
            <Flag key={f.k} ok={!!env[f.k]} label={f.l} testid={`env-flag-${f.k}`} />
          ))}
        </div>
      </section>

      {/* audit activity */}
      <section data-testid="audit-activity" className="space-y-4">
        <h2 className="font-['Outfit'] text-lg font-medium">Audit & access activity</h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatChip testid="audit-total" value={data.audit_activity.total_rows} label="Audit rows (total)" />
          <StatChip testid="audit-24h" value={data.audit_activity.last_24h} label="Events · last 24 h" />
          <StatChip testid="audit-phi-30d" value={data.audit_activity.phi_access_30d} label="PHI access · 30 d" />
          <StatChip testid="audit-breakglass-30d" value={data.audit_activity.breakglass_30d} label="Break-glass · 30 d" />
          <StatChip testid="audit-failed-logins" value={data.audit_activity.failed_logins_24h} label="Failed logins · 24 h" />
          <StatChip
            testid="audit-last-phi"
            value={data.audit_activity.last_phi_access_at ? formatDateTime(data.audit_activity.last_phi_access_at) : "—"}
            label="Last PHI access"
          />
          <StatChip
            testid="audit-last-breakglass"
            value={data.audit_activity.last_breakglass_at ? formatDateTime(data.audit_activity.last_breakglass_at) : "—"}
            label="Last break-glass"
          />
          <StatChip
            testid="mfa-adoption"
            value={
              data.mfa.adoption_ratio !== null && data.mfa.adoption_ratio !== undefined
                ? `${Math.round(data.mfa.adoption_ratio * 100)}%`
                : "—"
            }
            label={`MFA · ${data.mfa.mfa_enabled}/${data.mfa.privileged_users} privileged`}
          />
        </div>
      </section>

      {/* retention */}
      <section data-testid="retention-status" className="space-y-4">
        <h2 className="font-['Outfit'] text-lg font-medium">Retention & privacy workflow</h2>
        <div className="grid gap-3 md:grid-cols-3">
          <StatChip
            testid="retention-softdelete"
            value={data.retention.soft_deleted_patients}
            label="Soft-deleted patients"
          />
          <StatChip
            testid="retention-overdue"
            value={data.retention.overdue_purge_count}
            label="Overdue purge (worker pending)"
          />
          <Flag
            ok={data.retention.automated_purge_worker}
            label="Automated purge worker running"
            testid="retention-worker-flag"
          />
        </div>
      </section>

      {/* controls table */}
      <section data-testid="controls-table" className="space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="font-['Outfit'] text-lg font-medium">Controls ({filteredControls.length})</h2>
          <div className="flex flex-wrap gap-1.5">
            {groups.map((g) => (
              <button
                key={g}
                data-testid={`group-filter-${g.replace(/\s+/g, "-").toLowerCase()}`}
                onClick={() => setGroupFilter(g)}
                className={`rounded-sm px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider transition-colors ${
                  groupFilter === g
                    ? "bg-[#1F2924] text-white"
                    : "bg-stone-100 text-[#5C6A61] hover:bg-stone-200"
                }`}
              >
                {g === "all" ? "All" : g}
              </button>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto rounded-sm border border-stone-200 bg-white">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="border-b border-stone-200 text-[11px] uppercase tracking-[0.15em] text-[#5C6A61]">
              <tr>
                <th className="px-4 py-3 font-medium">Control</th>
                <th className="px-4 py-3 font-medium">Group</th>
                <th className="px-4 py-3 font-medium">Frameworks</th>
                <th className="px-4 py-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="px-4">
              {filteredControls.map((c) => (
                <ControlRow key={c.id} c={c} />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* docs */}
      <section data-testid="compliance-docs" className="space-y-3">
        <h2 className="font-['Outfit'] text-lg font-medium">Reference documents</h2>
        <ul className="space-y-2 text-sm">
          {data.documents.map((p) => (
            <li
              key={p}
              data-testid={`doc-${p.split("/").pop()}`}
              className="flex items-center gap-2 text-[#5C6A61]"
            >
              <ClipboardCheck className="h-4 w-4" />
              <code className="font-mono text-xs text-[#1F2924]">{p}</code>
            </li>
          ))}
        </ul>
        <p className="pt-2 text-xs text-[#5C6A61]">
          External auditors should be pointed at these files along with the
          `/api/audit-logs` export and `/api/metrics` evidence. Certification
          readiness also requires infrastructure, legal, HR and operational
          evidence held outside this repository.
        </p>
      </section>
    </div>
  );
}
