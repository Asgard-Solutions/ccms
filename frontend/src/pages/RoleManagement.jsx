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
import { Users, UserPlus, Trash2, Shield } from "lucide-react";

export default function RoleManagement() {
  const [roles, setRoles] = useState([]);
  const [users, setUsers] = useState([]);
  const [locations, setLocations] = useState([]);
  const [assignDialogOpen, setAssignDialogOpen] = useState(false);
  const [selectedUser, setSelectedUser] = useState(null);
  const [pendingRole, setPendingRole] = useState("");

  const load = async () => {
    try {
      const [r, u, l] = await Promise.all([
        api.get("/authz/roles"),
        api.get("/auth/users?include_disabled=true"),
        api.get("/authz/locations"),
      ]);
      setRoles(r.data);
      setUsers(u.data);
      setLocations(l.data);
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
          <h1 className="font-['Outfit'] text-3xl font-medium text-[#1F2924]">
            Role management
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-[#5C6A61]">
            Assign baseline roles to users. Each assignment bumps the user's
            session epoch so existing tokens are revoked immediately.
          </p>
        </div>
        <Badge className="bg-[#EDF2EE] text-[#526B58]">
          <Shield className="mr-1 h-3 w-3" />
          {roles.length} roles · {users.length} users
        </Badge>
      </header>

      <Card className="rounded-sm">
        <CardHeader>
          <CardTitle className="text-base font-normal flex items-center gap-2">
            <Users className="h-4 w-4 text-[#5C6A61]" /> Users & role assignments
          </CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-[#5C6A61]">
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
              className="flex items-start justify-between rounded-sm border border-stone-200 px-4 py-3"
            >
              <div>
                <div className="font-medium text-[#1F2924]">
                  {r.name}{" "}
                  <span className="text-[11px] uppercase tracking-wider text-[#5C6A61]">
                    · {r.abbr}
                  </span>
                </div>
                <div className="text-sm text-[#5C6A61]">{r.description}</div>
                {r.privileged && (
                  <Badge className="mt-1 bg-[#FDF0EA] text-[#B8715C]">privileged</Badge>
                )}
                {r.service_account && (
                  <Badge className="mt-1 ml-1 bg-[#EDF2EE] text-[#526B58]">
                    service account
                  </Badge>
                )}
              </div>
              <div className="text-right text-xs text-[#5C6A61]">
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
              className="bg-[#7B9A82] hover:bg-[#65826C]"
            >
              <UserPlus className="mr-2 h-4 w-4" />
              Assign
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function UserRow({ user, onAssign, onRevoke }) {
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
      className="border-t border-stone-100 align-top"
    >
      <td className="px-3 py-2">
        <div className="font-medium text-[#1F2924]">{user.name}</div>
        <div className="text-xs text-[#5C6A61]">{user.email}</div>
      </td>
      <td className="px-3 py-2">
        <Badge
          className={
            user.status === "disabled"
              ? "bg-[#FDE8E3] text-[#B8715C]"
              : "bg-[#EDF2EE] text-[#526B58]"
          }
        >
          {user.status || "active"}
        </Badge>
      </td>
      <td className="px-3 py-2 text-sm text-[#5C6A61]">{user.role}</td>
      <td className="px-3 py-2 text-xs text-[#5C6A61]">
        {userRoles.length ? userRoles.join(", ") : "via legacy role"}
      </td>
      <td className="px-3 py-2">
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
      </td>
    </tr>
  );
}
