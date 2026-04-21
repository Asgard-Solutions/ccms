import { useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import { toast } from "sonner";
import { api } from "../api/client";
import { ShieldCheck, Lock, AlertTriangle, Info } from "lucide-react";

const SCOPE_LABEL = {
  self: "self",
  assigned_patients: "assigned",
  assigned_location: "location",
  all_location_patients: "location+",
  all_org: "org",
  no_phi: "no-phi",
  phi_limited: "phi-ltd",
  phi_full: "phi-full",
};

function Cell({ grant }) {
  if (!grant) {
    return (
      <span
        data-testid="matrix-cell-denied"
        className="inline-flex h-5 min-w-[24px] items-center justify-center rounded-sm border border-border bg-card px-1 text-[10px] text-muted-foreground/70"
      >
        —
      </span>
    );
  }
  const badges = [];
  if (grant.requires_mfa) badges.push("MFA");
  if (grant.requires_approval) badges.push("APR");
  if (grant.break_glass_allowed) badges.push("BG");
  return (
    <div
      data-testid="matrix-cell-grant"
      className="flex flex-col items-start gap-1"
    >
      <span className="inline-flex items-center rounded-sm bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-primary">
        {SCOPE_LABEL[grant.scope] || grant.scope}
      </span>
      {badges.length > 0 && (
        <span className="text-[9px] text-destructive">{badges.join(" · ")}</span>
      )}
    </div>
  );
}

export default function PermissionMatrix() {
  const [matrix, setMatrix] = useState(null);
  const [filter, setFilter] = useState("");
  const [onlyPrivileged, setOnlyPrivileged] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/authz/matrix");
        setMatrix(data);
      } catch (err) {
        toast.error("Unable to load permission matrix");
      }
    })();
  }, []);

  const filtered = useMemo(() => {
    if (!matrix) return [];
    const q = filter.trim().toLowerCase();
    return matrix.permissions.filter((p) => {
      if (onlyPrivileged && !p.privileged) return false;
      if (!q) return true;
      return (
        p.key.toLowerCase().includes(q) ||
        p.resource.toLowerCase().includes(q) ||
        p.action.toLowerCase().includes(q)
      );
    });
  }, [matrix, filter, onlyPrivileged]);

  if (!matrix) {
    return <div className="p-6 text-muted-foreground">Loading permission matrix…</div>;
  }

  return (
    <div data-testid="permission-matrix-page" className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-medium text-foreground">
            Permission matrix
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Role × permission grid. Each cell shows the scope granted to that
            role; flags indicate MFA, approval, or break-glass overlays. This
            view is read-only evidence — the backend is the authoritative
            source.
          </p>
        </div>
        <Badge className="bg-primary/10 text-primary">
          <ShieldCheck className="mr-1 h-3 w-3" />
          {matrix.permissions.length} permissions × {matrix.roles.length} roles
        </Badge>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <Input
          data-testid="matrix-search"
          placeholder="Filter by resource, action, or key…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="max-w-sm"
        />
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input
            data-testid="matrix-privileged-toggle"
            type="checkbox"
            checked={onlyPrivileged}
            onChange={(e) => setOnlyPrivileged(e.target.checked)}
          />
          Privileged only
        </label>
      </div>

      <Card className="rounded-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-normal">
            <Info className="h-4 w-4 text-muted-foreground" />
            Scopes: self · assigned · location · location+ · phi-ltd · phi-full · org · no-phi
          </CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="min-w-full border-separate border-spacing-0 text-xs">
            <thead>
              <tr>
                <th className="sticky left-0 z-10 bg-card px-2 py-2 text-left text-[11px] uppercase tracking-wider text-muted-foreground">
                  Permission
                </th>
                {matrix.roles.map((r) => (
                  <th
                    key={r.key}
                    className="px-2 py-2 text-center text-[10px] uppercase tracking-wider text-muted-foreground"
                    title={r.name}
                  >
                    {r.abbr}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((p) => (
                <tr key={p.key} className="border-t border-border">
                  <td className="sticky left-0 z-10 bg-card px-2 py-2 font-mono text-[11px] text-foreground">
                    <div className="flex items-center gap-2">
                      {p.privileged && (
                        <Lock className="h-3 w-3 text-destructive" />
                      )}
                      {p.destructive && (
                        <AlertTriangle className="h-3 w-3 text-destructive" />
                      )}
                      {p.key}
                    </div>
                    <div className="text-[9px] uppercase tracking-wider text-muted-foreground/70">
                      {p.sensitivity}
                      {p.phi && " · phi"}
                      {p.export && " · export"}
                    </div>
                  </td>
                  {matrix.roles.map((r) => {
                    const grant = matrix.grants_by_role[r.key]?.[p.key];
                    return (
                      <td key={r.key} className="px-2 py-2 align-top">
                        <Cell grant={grant} />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
