import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "../api/client";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { AlertCircle, Download, FileBarChart, ShieldCheck, Users, History, Lock } from "lucide-react";

function StatCard({ icon: Icon, label, value, sub, testId }) {
  return (
    <Card data-testid={testId} className="rounded-sm">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm font-normal text-muted-foreground">
          <Icon className="h-4 w-4" /> {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="font-display text-3xl font-medium text-foreground">{value}</div>
        {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function EventList({ title, rows, emptyText, testId }) {
  return (
    <Card data-testid={testId} className="rounded-sm">
      <CardHeader>
        <CardTitle className="text-base font-normal">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {!rows?.length ? (
          <div className="text-sm text-muted-foreground">{emptyText}</div>
        ) : (
          <ul className="divide-y divide-border text-sm">
            {rows.slice(0, 15).map((r, i) => (
              <li key={i} className="py-2">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-mono text-[11px] text-foreground">{r.action}</div>
                    <div className="text-xs text-muted-foreground">
                      {r.actor_email || "—"} {r.entity_type ? `· ${r.entity_type}:${r.entity_id}` : ""}
                    </div>
                    {r.reason && (
                      <div className="text-[11px] text-destructive">{r.reason}</div>
                    )}
                  </div>
                  <time className="shrink-0 text-[11px] text-muted-foreground/70">
                    {new Date(r.created_at).toLocaleString()}
                  </time>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export default function AccessReview() {
  const [summary, setSummary] = useState(null);
  const [privileged, setPrivileged] = useState([]);
  const [roleChanges, setRoleChanges] = useState([]);
  const [phiHistory, setPhiHistory] = useState([]);
  const [exports, setExports] = useState([]);
  const [breakGlass, setBreakGlass] = useState([]);
  const [failed, setFailed] = useState([]);
  const [reauthOpen, setReauthOpen] = useState(false);
  const [reauthPassword, setReauthPassword] = useState("");
  const [reauthBusy, setReauthBusy] = useState(false);

  const loadAll = async () => {
    try {
      const [s, p, r, ph, ex, bg, f] = await Promise.all([
        api.get("/access/reports/access-review"),
        api.get("/access/reports/privileged-users"),
        api.get("/access/reports/recent-role-changes?days=30"),
        api.get("/access/reports/phi-access-history?days=7"),
        api.get("/access/reports/export-history?days=30"),
        api.get("/access/reports/break-glass-history?days=90"),
        api.get("/access/reports/failed-authz?days=7"),
      ]);
      setSummary(s.data);
      setPrivileged(p.data.users);
      setRoleChanges(r.data.events);
      setPhiHistory(ph.data.events);
      setExports(ex.data.events);
      setBreakGlass(bg.data.events);
      setFailed(f.data.events);
      return true;
    } catch (err) {
      if (err?.response?.status === 401) {
        setReauthOpen(true);
      } else {
        toast.error("Unable to load access review data");
      }
      return false;
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const submitReauth = async () => {
    if (!reauthPassword) return;
    setReauthBusy(true);
    try {
      await api.post("/auth/reauth", { password: reauthPassword });
      setReauthOpen(false);
      setReauthPassword("");
      toast.success("Re-authenticated — loading reports…");
      await loadAll();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Re-authentication failed");
    } finally {
      setReauthBusy(false);
    }
  };

  return (
    <div data-testid="access-review-page" className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-medium text-foreground">
            Access review
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Compliance evidence snapshot. Highlights privileged users, recent
            role changes, PHI access, exports, break-glass events and failed
            authorizations. Back-end reports require MFA re-auth.
          </p>
        </div>
        <Button
          data-testid="access-review-reauth-btn"
          variant="outline"
          onClick={() => setReauthOpen(true)}
          className="rounded-sm"
        >
          <Lock className="mr-2 h-4 w-4" />
          Re-authenticate
        </Button>
      </header>

      {summary && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            testId="stat-users"
            icon={Users}
            label="Active users"
            value={summary.users.active}
            sub={`${summary.users.role_assignments_active} role assignments`}
          />
          <StatCard
            testId="stat-phi"
            icon={ShieldCheck}
            label="PHI reads (7d)"
            value={summary.phi_reads_7d}
          />
          <StatCard
            testId="stat-denials"
            icon={AlertCircle}
            label="Authz denials (7d)"
            value={summary.authz_denials_7d}
          />
          <StatCard
            testId="stat-bg"
            icon={History}
            label="Break-glass (30d)"
            value={summary.break_glass_30d}
            sub={`${summary.elevations.pending} pending elevations`}
          />
        </div>
      )}

      <Card data-testid="privileged-users-card" className="rounded-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-normal">
            <ShieldCheck className="h-4 w-4 text-muted-foreground" /> Privileged users
            <Badge className="bg-accent text-accent-foreground">{privileged.length}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-muted-foreground">
                <th className="px-2 py-1">Email</th>
                <th className="px-2 py-1">Name</th>
                <th className="px-2 py-1">Role</th>
                <th className="px-2 py-1">Status</th>
                <th className="px-2 py-1">Last login</th>
              </tr>
            </thead>
            <tbody>
              {privileged.map((u, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="px-2 py-1 font-mono text-[11px]">{u.email}</td>
                  <td className="px-2 py-1">{u.name}</td>
                  <td className="px-2 py-1 text-xs">{u.role_key}</td>
                  <td className="px-2 py-1">
                    <Badge className="bg-primary/10 text-primary">{u.status || "active"}</Badge>
                  </td>
                  <td className="px-2 py-1 text-xs text-muted-foreground">
                    {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <EventList
          testId="recent-role-changes"
          title="Recent role changes (30d)"
          rows={roleChanges}
          emptyText="No recent role changes."
        />
        <EventList
          testId="phi-history"
          title="PHI access history (7d)"
          rows={phiHistory}
          emptyText="No PHI access in the last 7 days."
        />
        <EventList
          testId="export-history"
          title="Export history (30d)"
          rows={exports}
          emptyText="No exports."
        />
        <EventList
          testId="break-glass-history"
          title="Break-glass / elevation history (90d)"
          rows={breakGlass}
          emptyText="No break-glass events."
        />
        <EventList
          testId="failed-authz"
          title="Failed authorisation attempts (7d)"
          rows={failed}
          emptyText="No authz failures — 🙌"
        />
      </div>

      <Dialog open={reauthOpen} onOpenChange={setReauthOpen}>
        <DialogContent data-testid="access-review-reauth-dialog" className="rounded-sm">
          <DialogHeader>
            <DialogTitle>Re-authenticate</DialogTitle>
            <DialogDescription>
              The compliance reports on this page require you to confirm your
              password before they load.
            </DialogDescription>
          </DialogHeader>
          <Input
            data-testid="access-review-reauth-password"
            type="password"
            value={reauthPassword}
            autoFocus
            onChange={(e) => setReauthPassword(e.target.value)}
            placeholder="Your password"
            onKeyDown={(e) => {
              if (e.key === "Enter") submitReauth();
            }}
          />
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setReauthOpen(false)}
              disabled={reauthBusy}
            >
              Cancel
            </Button>
            <Button
              data-testid="access-review-reauth-submit"
              onClick={submitReauth}
              disabled={!reauthPassword || reauthBusy}
              className="bg-primary hover:bg-[var(--primary-hover)]"
            >
              <Lock className="mr-2 h-4 w-4" />
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
