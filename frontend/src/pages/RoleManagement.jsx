import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "../api/client";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { UserCog, UserPlus, Trash2, Shield, KeyRound, Users } from "lucide-react";

function UserOverridesDialog({ user, open, onOpenChange, permissions }) {
  const [rows, setRows] = useState([]);
  const [form, setForm] = useState({
    permission_key: "",
    scope: "all_org",
    reason: "",
    expires_at: "",
  });

  const load = async () => {
    if (!user) return;
    try {
      const { data } = await api.get(
        `/authz/users/${user.id}/overrides?include_revoked=true`
      );
      setRows(data);
    } catch (err) {
      // If caller lacks permission.read, show empty list quietly.
      setRows([]);
    }
  };
  useEffect(() => {
    if (open) load();
  }, [open, user?.id]);

  const grant = async () => {
    if (!form.permission_key || form.reason.trim().length < 10) {
      toast.error("Pick a permission and provide a 10+ character reason.");
      return;
    }
    try {
      await api.post(`/authz/users/${user.id}/overrides`, {
        permission_key: form.permission_key,
        scope: form.scope || "all_org",
        reason: form.reason.trim(),
        expires_at: form.expires_at || null,
        requires_mfa: false,
        requires_approval: false,
        break_glass_allowed: false,
      });
      toast.success("Override granted — user sessions revoked");
      setForm({ permission_key: "", scope: "all_org", reason: "", expires_at: "" });
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Grant failed");
    }
  };

  const revoke = async (id) => {
    if (!window.confirm("Revoke this override?")) return;
    try {
      await api.delete(`/authz/users/${user.id}/overrides/${id}`);
      toast.success("Override revoked");
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Revoke failed");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="overrides-dialog"
        className="max-h-[80vh] max-w-2xl overflow-y-auto rounded-sm"
      >
        <DialogHeader>
          <DialogTitle>
            <KeyRound className="mr-2 inline h-4 w-4" />
            Per-user overrides
          </DialogTitle>
          <DialogDescription>
            {user?.email} — exception-grant specific permissions beyond the
            user's roles. Use sparingly; every override is audited.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 rounded-sm border border-subtle p-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <label className="text-xs uppercase tracking-wider text-muted-strong">
                Permission
              </label>
              <Input
                data-testid="override-permission-input"
                list="override-perm-list"
                value={form.permission_key}
                onChange={(e) =>
                  setForm((f) => ({ ...f, permission_key: e.target.value }))
                }
                placeholder="e.g. audit_log.read"
              />
              <datalist id="override-perm-list">
                {permissions.map((p) => (
                  <option key={p.key} value={p.key} />
                ))}
              </datalist>
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-muted-strong">
                Scope
              </label>
              <Select
                value={form.scope}
                onValueChange={(v) => setForm((f) => ({ ...f, scope: v }))}
              >
                <SelectTrigger data-testid="override-scope-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[
                    "all_org",
                    "assigned_location",
                    "all_location_patients",
                    "assigned_patients",
                    "phi_limited",
                    "phi_full",
                    "no_phi",
                    "self",
                  ].map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div>
            <label className="text-xs uppercase tracking-wider text-muted-strong">
              Reason (min 10 chars)
            </label>
            <Input
              data-testid="override-reason-input"
              value={form.reason}
              onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
              placeholder="Documented business justification"
            />
          </div>
          <div>
            <label className="text-xs uppercase tracking-wider text-muted-strong">
              Expires (ISO datetime — leave blank for permanent)
            </label>
            <Input
              data-testid="override-expires-input"
              value={form.expires_at}
              onChange={(e) => setForm((f) => ({ ...f, expires_at: e.target.value }))}
              placeholder="2026-12-31T23:59:59Z"
            />
          </div>
          <Button
            data-testid="override-grant-btn"
            onClick={grant}
            disabled={
              !form.permission_key || form.reason.trim().length < 10
            }
            className="bg-sage hover:bg-sage-hover"
          >
            Grant override
          </Button>
        </div>

        <div className="mt-4">
          <div className="mb-2 text-xs uppercase tracking-wider text-muted-strong">
            Existing overrides
          </div>
          {rows.length === 0 ? (
            <div className="text-sm text-muted-strong">No overrides.</div>
          ) : (
            <ul className="divide-y divide-border">
              {rows.map((o) => (
                <li
                  key={o.id}
                  data-testid={`override-row-${o.id}`}
                  className="py-2 flex items-start justify-between gap-3"
                >
                  <div>
                    <div className="font-mono text-[11px]">{o.permission_key}</div>
                    <div className="text-xs text-muted-strong">
                      scope: {o.scope} · {o.reason}
                    </div>
                    <div className="text-[10px] text-soft">
                      granted by {o.granted_by_email} · {new Date(o.created_at).toLocaleString()}
                      {o.expires_at && ` · expires ${o.expires_at}`}
                    </div>
                  </div>
                  <div className="shrink-0">
                    {o.status === "active" ? (
                      <Button
                        data-testid={`override-revoke-btn-${o.id}`}
                        size="sm"
                        variant="ghost"
                        className="rounded-sm text-danger-soft"
                        onClick={() => revoke(o.id)}
                      >
                        <Trash2 className="mr-1 h-3 w-3" /> Revoke
                      </Button>
                    ) : (
                      <Badge className="bg-muted text-muted-foreground">revoked</Badge>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function RoleManagement() {
  const [roles, setRoles] = useState([]);
  const [users, setUsers] = useState([]);
  const [locations, setLocations] = useState([]);
  const [permissions, setPermissions] = useState([]);
  const [assignDialogOpen, setAssignDialogOpen] = useState(false);
  const [overridesUser, setOverridesUser] = useState(null);
  const [selectedUser, setSelectedUser] = useState(null);
  const [pendingRole, setPendingRole] = useState("");

  const load = async () => {
    try {
      const [r, u, l, p] = await Promise.all([
        api.get("/authz/roles"),
        api.get("/auth/users?include_disabled=true"),
        api.get("/authz/locations"),
        api.get("/authz/permissions"),
      ]);
      setRoles(r.data);
      setUsers(u.data);
      setLocations(l.data);
      setPermissions(p.data);
    } catch (err) {
      toast.error("Unable to load role data");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const openAssign = (u) => {
    setSelectedUser(u);
    setPendingRole("");
    setAssignDialogOpen(true);
  };

  const assignRole = async () => {
    if (!selectedUser || !pendingRole) return;
    try {
      await api.post(`/authz/users/${selectedUser.id}/roles`, { role_key: pendingRole });
      toast.success(`Assigned ${pendingRole} — user sessions revoked`);
      setAssignDialogOpen(false);
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Assignment failed");
    }
  };

  const revokeRole = async (user, roleKey) => {
    if (!window.confirm(`Revoke role "${roleKey}" from ${user.email}?`)) return;
    try {
      await api.delete(`/authz/users/${user.id}/roles/${roleKey}`);
      toast.success("Role revoked");
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Revoke failed");
    }
  };

  return (
    <div data-testid="role-management-page" className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-['Outfit'] text-3xl font-medium text-strong">
            Role management
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-strong">
            Assign baseline roles to users. Each assignment bumps the user's
            session epoch so existing tokens are revoked immediately.
          </p>
        </div>
        <Badge className="surface-sage text-sage-deep">
          <Shield className="mr-1 h-3 w-3" />
          {roles.length} roles · {users.length} users
        </Badge>
      </header>

      <Card className="rounded-sm">
        <CardHeader>
          <CardTitle className="text-base font-normal flex items-center gap-2">
            <Users className="h-4 w-4 text-muted-strong" /> Users & role assignments
          </CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-muted-strong">
                <th className="px-3 py-2">User</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Legacy role</th>
                <th className="px-3 py-2">Assigned roles</th>
                <th className="px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <UserRow
                  key={u.id}
                  user={u}
                  onAssign={() => openAssign(u)}
                  onRevoke={(rk) => revokeRole(u, rk)}
                  onOverrides={() => setOverridesUser(u)}
                />
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card className="rounded-sm">
        <CardHeader>
          <CardTitle className="text-base font-normal">Baseline roles</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {roles.map((r) => (
            <div
              key={r.key}
              data-testid={`role-card-${r.key}`}
              className="flex items-start justify-between rounded-sm border border-subtle px-4 py-3"
            >
              <div>
                <div className="font-medium text-strong">
                  {r.name}{" "}
                  <span className="text-[11px] uppercase tracking-wider text-muted-strong">
                    · {r.abbr}
                  </span>
                </div>
                <div className="text-sm text-muted-strong">{r.description}</div>
                {r.privileged && (
                  <Badge className="mt-1 bg-accent text-accent-foreground">privileged</Badge>
                )}
                {r.service_account && (
                  <Badge className="mt-1 ml-1 surface-sage text-sage-deep">
                    service account
                  </Badge>
                )}
              </div>
              <div className="text-right text-xs text-muted-strong">
                {r.grants?.length || 0} grants
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Dialog open={assignDialogOpen} onOpenChange={setAssignDialogOpen}>
        <DialogContent data-testid="assign-role-dialog" className="rounded-sm">
          <DialogHeader>
            <DialogTitle>Assign role</DialogTitle>
            <DialogDescription>
              {selectedUser ? `${selectedUser.email}` : ""}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Select value={pendingRole} onValueChange={setPendingRole}>
              <SelectTrigger data-testid="assign-role-select">
                <SelectValue placeholder="Pick a role…" />
              </SelectTrigger>
              <SelectContent>
                {roles.map((r) => (
                  <SelectItem key={r.key} value={r.key}>
                    {r.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setAssignDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              data-testid="assign-role-confirm"
              onClick={assignRole}
              disabled={!pendingRole}
              className="bg-sage hover:bg-sage-hover"
            >
              <UserPlus className="mr-2 h-4 w-4" />
              Assign
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <UserOverridesDialog
        user={overridesUser}
        open={!!overridesUser}
        onOpenChange={(o) => !o && setOverridesUser(null)}
        permissions={permissions}
      />
    </div>
  );
}

function UserRow({ user, onAssign, onRevoke, onOverrides }) {
  const [userRoles, setUserRoles] = useState([]);
  useEffect(() => {
    // Best-effort lookup via the /authz/roles payload cross-joined client-side.
    (async () => {
      try {
        const { data } = await api.get(`/authz/me/permissions`);
        // Not for this user — the backend currently returns the caller's
        // permissions. For per-user role lists we'd need a dedicated endpoint.
        // For now, show the legacy role as the effective fallback.
        setUserRoles([]);
      } catch {
        setUserRoles([]);
      }
    })();
  }, [user.id]);

  return (
    <tr
      data-testid={`user-row-${user.id}`}
      className="border-t border-border align-top"
    >
      <td className="px-3 py-2">
        <div className="font-medium text-strong">{user.name}</div>
        <div className="text-xs text-muted-strong">{user.email}</div>
      </td>
      <td className="px-3 py-2">
        <Badge
          className={
            user.status === "disabled"
              ? "bg-destructive-soft text-destructive"
              : "surface-sage text-sage-deep"
          }
        >
          {user.status || "active"}
        </Badge>
      </td>
      <td className="px-3 py-2 text-sm text-muted-strong">{user.role}</td>
      <td className="px-3 py-2 text-xs text-muted-strong">
        {userRoles.length ? userRoles.join(", ") : "via legacy role"}
      </td>
      <td className="px-3 py-2">
        <div className="flex gap-2">
          <Button
            data-testid={`assign-role-btn-${user.id}`}
            size="sm"
            variant="outline"
            onClick={onAssign}
            className="rounded-sm"
          >
            <UserPlus className="mr-1 h-3 w-3" />
            Assign role
          </Button>
          <Button
            data-testid={`overrides-btn-${user.id}`}
            size="sm"
            variant="ghost"
            onClick={onOverrides}
            className="rounded-sm text-muted-strong"
          >
            <KeyRound className="mr-1 h-3 w-3" />
            Overrides
          </Button>
        </div>
      </td>
    </tr>
  );
}
