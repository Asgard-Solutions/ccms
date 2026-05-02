import { useEffect, useState } from "react";
import { ScrollText, ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "../../../components/ui/button";
import { api } from "../../../api/client";
import { EmptyState, SectionHeader } from "../common";
import { formatDateTime } from "../../../utils/time";

// Compliance-relevant audit actions only.
const COMPLIANCE_ACTION_PREFIXES = [
  "compliance.",
  "auth.mfa_",
  "auth.login",
  "security.",
  "phi_accessed",
];

function isComplianceRelevant(row) {
  if (row?.phi_accessed) return true;
  const a = row?.action || "";
  return COMPLIANCE_ACTION_PREFIXES.some((p) => a.startsWith(p));
}

export default function AuditTrail() {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/audit-logs", { params: { limit: 100 } });
        const list = Array.isArray(data) ? data : data?.rows || data?.results || [];
        setRows(list.filter(isComplianceRelevant).slice(0, 50));
      } catch (e) {
        setError(e?.response?.data?.detail || "Failed to load audit trail");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return (
    <section data-testid="audit-trail-register" className="space-y-4">
      <SectionHeader
        testid="audit-trail-header"
        title="Compliance audit trail"
        count={rows.length}
        action={
          <Button asChild variant="outline" size="sm" data-testid="audit-trail-full-link">
            <Link to="/audit-log" className="gap-1">
              Full audit log <ExternalLink className="h-3.5 w-3.5" />
            </Link>
          </Button>
        }
      />
      <div className="flex items-start gap-2 rounded-sm border border-border bg-secondary/40 p-3 text-xs text-muted-foreground">
        <ScrollText className="mt-0.5 h-4 w-4 flex-none text-primary" />
        <span>
          Last 50 compliance-relevant audit events (compliance.*, auth.*, security.*, PHI access).
          The full audit log carries every system action with actor, IP, and tenant context, and is
          retained for 7 years to satisfy SOC 2 CC4.1 and ISO 27001 A.12.4 evidence requirements.
        </span>
      </div>

      {error && (
        <div data-testid="audit-trail-error" className="rounded-sm border border-destructive-soft bg-destructive-soft p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {loading ? (
        <div data-testid="audit-trail-loading" className="text-sm text-muted-foreground">Loading…</div>
      ) : rows.length === 0 ? (
        <EmptyState testid="audit-trail-empty" label="No compliance-relevant events in the recent audit window." />
      ) : (
        <div className="overflow-x-auto rounded-sm border border-border bg-card">
          <table className="w-full min-w-[820px] text-left text-sm">
            <thead className="border-b border-border text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">When</th>
                <th className="px-4 py-3 font-medium">Action</th>
                <th className="px-4 py-3 font-medium">Actor</th>
                <th className="px-4 py-3 font-medium">Entity</th>
                <th className="px-4 py-3 font-medium">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.id || i} data-testid={`audit-row-${i}`} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 align-top text-xs text-muted-foreground">{formatDateTime(r.timestamp || r.at)}</td>
                  <td className="px-4 py-3 align-top text-xs">
                    <code className="font-mono text-[11px] text-foreground">{r.action}</code>
                  </td>
                  <td className="px-4 py-3 align-top text-xs text-muted-foreground">
                    {r.actor_email || r.actor_id || "system"}
                  </td>
                  <td className="px-4 py-3 align-top text-xs text-muted-foreground">
                    {r.entity_type ? `${r.entity_type}${r.entity_id ? ` · ${String(r.entity_id).slice(0, 8)}…` : ""}` : "—"}
                  </td>
                  <td className="px-4 py-3 align-top text-xs">
                    <span
                      data-testid={`audit-outcome-${i}`}
                      className={`inline-flex rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
                        r.outcome === "success" ? "bg-primary/10 text-primary" : "bg-destructive-soft text-destructive"
                      }`}
                    >
                      {r.outcome || "—"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
