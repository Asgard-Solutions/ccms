/**
 * /admin/access-history — Access Change History (Phase 5).
 *
 * Replaces the old /access-review. Surfaces every authz.* audit row
 * so admins can see who granted what to whom, when, and why.
 *
 * Backed by GET /api/authz/access-history.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Clock, Download, Filter, RefreshCw, ShieldAlert, UserCog,
} from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";

const PREFIX_OPTIONS = [
  { value: "all", label: "All access changes", prefix: "" },
  { value: "role", label: "Role changes", prefix: "authz.role" },
  { value: "assign", label: "Assignments", prefix: "authz.role_assigned" },
  { value: "override", label: "Permission overrides", prefix: "authz.override" },
  { value: "elevation", label: "Elevation requests", prefix: "authz.elevation" },
  { value: "migration", label: "Migration events", prefix: "authz.migration" },
];

function ts(x) {
  if (!x) return "—";
  try {
    return new Date(x).toLocaleString();
  } catch {
    return x;
  }
}

function humanAction(a) {
  const map = {
    "authz.role.created":  "Custom role created",
    "authz.role.updated":  "Role edited",
    "authz.role.deleted":  "Role archived",
    "authz.role_assigned": "Role assigned to user",
    "authz.role_revoked":  "Role revoked from user",
    "authz.override_granted": "Per-user override granted",
    "authz.override_revoked": "Override revoked",
    "authz.elevation_requested": "Elevation requested",
    "authz.elevation_approved": "Elevation approved",
    "authz.elevation_rejected": "Elevation rejected",
    "authz.migration.legacy_backfill_applied": "Legacy-role migration applied",
  };
  return map[a] || a;
}

export default function AccessHistoryPage() {
  const [rows, setRows] = useState(null);
  const [filter, setFilter] = useState("all");

  async function refresh() {
    setRows(null);
    try {
      const opt = PREFIX_OPTIONS.find((o) => o.value === filter);
      const params = { limit: 200 };
      if (opt?.prefix) params.action_prefix = opt.prefix;
      const { data } = await api.get("/authz/access-history", { params });
      setRows(data.rows || []);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to load access history");
      setRows([]);
    }
  }
  useEffect(() => { refresh(); }, [filter]); // eslint-disable-line

  const stats = useMemo(() => {
    const list = rows || [];
    const byAction = {};
    for (const r of list) {
      byAction[r.action] = (byAction[r.action] || 0) + 1;
    }
    return {
      total: list.length,
      top: Object.entries(byAction)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3),
    };
  }, [rows]);

  async function exportCsv() {
    try {
      const res = await api.get("/audit-logs/export.csv", {
        params: {
          action: PREFIX_OPTIONS.find((o) => o.value === filter)?.prefix
                  ? `${PREFIX_OPTIONS.find((o) => o.value === filter).prefix}`
                  : "authz",
        },
        responseType: "blob",
      });
      const url = URL.createObjectURL(new Blob([res.data], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `access-history-${Date.now()}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to export");
    }
  }

  return (
    <div data-testid="access-history-page" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            User Management
          </span>
          <h1 className="mt-1 font-display text-3xl font-medium tracking-tight">
            Access History
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Every change to users, roles, and permissions.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            data-testid="access-history-refresh"
            onClick={refresh}
            className="h-9 rounded-sm px-3 text-xs"
          >
            <RefreshCw className="mr-1 h-3.5 w-3.5" />
            Refresh
          </Button>
          <Button
            size="sm"
            variant="outline"
            data-testid="access-history-export"
            onClick={exportCsv}
            className="h-9 rounded-sm px-3 text-xs"
          >
            <Download className="mr-1 h-3.5 w-3.5" />
            Export CSV
          </Button>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <Filter className="h-4 w-4 text-muted-foreground" />
        <Select value={filter} onValueChange={setFilter}>
          <SelectTrigger data-testid="access-history-filter" className="w-[260px] rounded-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PREFIX_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {rows !== null && (
          <span className="text-xs text-muted-foreground">
            {stats.total} recent event{stats.total === 1 ? "" : "s"}
          </span>
        )}
      </div>

      <section data-testid="access-history-list" className="rounded-sm border border-border bg-card">
        {rows === null ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-14 rounded-sm" />)}
          </div>
        ) : rows.length === 0 ? (
          <div data-testid="access-history-empty" className="px-5 py-14 text-center text-sm text-muted-foreground">
            No access changes in recent history.
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {rows.map((r) => (
              <li
                key={r.id || `${r.action}-${r.created_at}`}
                data-testid={`access-history-row-${r.id || r.action}`}
                className="flex flex-wrap items-center gap-3 px-5 py-3"
              >
                <div className="flex-1 min-w-[280px]">
                  <div className="flex items-center gap-2">
                    {r.action.includes("override") || r.action.includes("elevation")
                      ? <ShieldAlert className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />
                      : <UserCog className="h-3.5 w-3.5 text-muted-foreground" />}
                    <p className="font-medium text-sm">
                      {humanAction(r.action)}
                    </p>
                    {r.outcome && r.outcome !== "success" && (
                      <Badge variant="outline" className="rounded-sm text-[10px] text-destructive">
                        {r.outcome}
                      </Badge>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    by {r.actor_email || r.actor_id || "system"}
                    {r.entity_id ? ` · target ${r.entity_id}` : ""}
                  </p>
                  {r.metadata && Object.keys(r.metadata).length > 0 && (
                    <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                      {Object.entries(r.metadata)
                        .filter(([k]) => !["tenant_id"].includes(k))
                        .slice(0, 4)
                        .map(([k, v]) => `${k}: ${typeof v === "object"
                          ? JSON.stringify(v).slice(0, 40)
                          : String(v)}`)
                        .join(" · ")}
                    </p>
                  )}
                </div>
                <Badge variant="outline" className="rounded-sm text-[10px] text-muted-foreground">
                  <Clock className="mr-1 h-2.5 w-2.5" />
                  {ts(r.created_at)}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
