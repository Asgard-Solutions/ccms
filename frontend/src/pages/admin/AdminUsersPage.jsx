/**
 * /admin/users — Redesigned user management screen.
 *
 * Primary access-management surface for clinic admins. Replaces the
 * matrix-first experience with a users-first, role-chip view.
 *
 * Features:
 *   - Searchable list (name, email)
 *   - Status filter (active / disabled)
 *   - Role chips per row
 *   - Add user (multi-step dialog)
 *   - Edit access (inline)
 *   - Disable / Reactivate
 *
 * Old routes /roles, /permissions, /access-review stay accessible under
 * "Advanced" for backward compatibility during the transition.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Link } from "react-router-dom";
import {
  Plus,
  Search,
  ShieldCheck,
  UserPlus,
  UserX,
  UserCheck,
  Lock,
  Wand2,
} from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../contexts/AuthContext";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Badge } from "../../components/ui/badge";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import CreateUserDialog from "./CreateUserDialog";
import EditUserAccessDialog from "./EditUserAccessDialog";

export default function AdminUsersPage() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState(null);
  const [roles, setRoles] = useState([]);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [migration, setMigration] = useState(null);

  async function refreshMigration() {
    try {
      const { data } = await api.get("/authz/migration/legacy/dry-run");
      setMigration(data);
    } catch {
      setMigration(null);
    }
  }

  async function runMigration() {
    try {
      const { data } = await api.post("/authz/migration/legacy/apply");
      toast.success(`Migration applied — ${data.inserted_count} user(s) assigned`);
      refreshMigration();
      refresh();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Migration failed");
    }
  }

  async function refresh() {
    try {
      const [u, r] = await Promise.all([
        api.get("/auth/users", { params: { include_disabled: true } }),
        api.get("/authz/roles", { params: { include_user_counts: true } }),
      ]);
      // Hydrate role_keys per user from /authz/users/{id}/effective-permissions
      // would be heavy — instead, fetch user_roles collection via a bulk helper.
      const list = u.data;
      // Pull role assignments individually but parallelized.
      const withRoles = await Promise.all(list.map(async (usr) => {
        try {
          const p = await api.get(`/authz/users/${usr.id}/effective-permissions`);
          return { ...usr, role_keys: p.data.role_keys || [] };
        } catch {
          return { ...usr, role_keys: [] };
        }
      }));
      setUsers(withRoles);
      setRoles(r.data);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to load users");
      setUsers([]);
    }
  }

  useEffect(() => { refresh(); refreshMigration(); }, []);

  const roleMap = useMemo(() => {
    const m = new Map();
    for (const r of roles) m.set(r.key, r);
    return m;
  }, [roles]);

  const filtered = useMemo(() => {
    if (!users) return [];
    return users.filter((u) => {
      if (statusFilter === "active" && u.status !== "active") return false;
      if (statusFilter === "disabled" && u.status !== "disabled") return false;
      if (!query) return true;
      const q = query.toLowerCase();
      return (
        (u.email || "").toLowerCase().includes(q)
        || (u.name || "").toLowerCase().includes(q)
      );
    });
  }, [users, query, statusFilter]);

  async function toggleStatus(u) {
    const endpoint = u.status === "active" ? "disable" : "enable";
    try {
      await api.post(`/auth/users/${u.id}/${endpoint}`);
      toast.success(`User ${endpoint === "disable" ? "disabled" : "reactivated"}`);
      refresh();
    } catch (err) {
      toast.error(err.response?.data?.detail || `Failed to ${endpoint}`);
    }
  }

  return (
    <div data-testid="admin-users-page" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            User Management
          </span>
          <h1 className="mt-1 font-display text-3xl font-medium tracking-tight">Users</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage who works in your clinic and what they can do.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to="/admin/roles"
            data-testid="admin-users-go-roles"
            className="rounded-sm border border-border px-3 py-2 text-xs font-medium hover:bg-muted"
          >
            <ShieldCheck className="mr-1 inline h-3.5 w-3.5" />
            Manage roles
          </Link>
          <Button
            data-testid="admin-users-add-btn"
            onClick={() => setCreateOpen(true)}
            className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
          >
            <UserPlus className="mr-1.5 h-4 w-4" />
            Add user
          </Button>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[240px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            data-testid="admin-users-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name or email"
            className="rounded-sm pl-9"
          />
        </div>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger data-testid="admin-users-status-filter" className="w-[160px] rounded-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="active">Active only</SelectItem>
            <SelectItem value="disabled">Disabled only</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {migration && migration.count_mapped > 0 && (
        <div
          data-testid="admin-users-migration-banner"
          className="flex flex-wrap items-center gap-3 rounded-sm border border-amber-500/40 bg-amber-500/5 px-4 py-3"
        >
          <Wand2 className="h-4 w-4 text-amber-700 dark:text-amber-300" />
          <div className="flex-1 text-xs">
            <p className="font-medium">
              {migration.count_mapped} user{migration.count_mapped === 1 ? "" : "s"} still on the legacy role model.
            </p>
            <p className="text-muted-foreground">
              Run the one-click migration to assign matching baseline roles.
              {migration.count_ambiguous > 0 && (
                <> {migration.count_ambiguous} user(s) have an ambiguous legacy role and need manual review.</>
              )}
            </p>
          </div>
          <Button
            size="sm"
            data-testid="admin-users-run-migration"
            onClick={runMigration}
            className="h-8 rounded-sm bg-amber-600 px-3 text-xs text-white hover:bg-amber-700"
          >
            Apply migration
          </Button>
        </div>
      )}

      <section data-testid="admin-users-list" className="rounded-sm border border-border bg-card">
        {users === null ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 rounded-sm" />)}
          </div>
        ) : filtered.length === 0 ? (
          <div data-testid="admin-users-empty" className="px-5 py-14 text-center text-sm text-muted-foreground">
            {users.length === 0 ? "No users yet — add your first one above." : "No users match your filters."}
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {filtered.map((u) => {
              const roleKeys = u.role_keys?.length
                ? u.role_keys
                : (u.role ? [u.role] : []);
              return (
                <li
                  key={u.id}
                  data-testid={`admin-user-row-${u.id}`}
                  className="flex flex-wrap items-center gap-3 px-5 py-4"
                >
                  <div className="min-w-[220px] flex-1">
                    <p className="font-medium">
                      <span data-testid={`admin-user-name-${u.id}`}>{u.name || "—"}</span>
                      {u.id === currentUser?.id && (
                        <span className="ml-2 text-[10px] font-semibold uppercase text-muted-foreground">
                          You
                        </span>
                      )}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">{u.email}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {roleKeys.length === 0 ? (
                      <Badge variant="outline" className="rounded-sm text-[10px] text-muted-foreground">
                        no role
                      </Badge>
                    ) : (
                      roleKeys.slice(0, 3).map((k) => (
                        <Badge
                          key={k}
                          variant="outline"
                          data-testid={`admin-user-role-chip-${u.id}-${k}`}
                          className="rounded-sm text-[11px]"
                        >
                          {roleMap.get(k)?.name || k}
                        </Badge>
                      ))
                    )}
                    {roleKeys.length > 3 && (
                      <Badge variant="outline" className="rounded-sm text-[10px] text-muted-foreground">
                        +{roleKeys.length - 3} more
                      </Badge>
                    )}
                  </div>
                  <Badge
                    variant="outline"
                    data-testid={`admin-user-status-${u.id}`}
                    className={`rounded-sm text-[11px] ${
                      u.status === "active"
                        ? "text-emerald-700 dark:text-emerald-300"
                        : "text-muted-foreground"
                    }`}
                  >
                    {u.status || "active"}
                  </Badge>
                  <div className="ml-auto flex items-center gap-1.5">
                    <Button
                      size="sm"
                      variant="outline"
                      data-testid={`admin-user-edit-${u.id}`}
                      onClick={() => setEditing(u)}
                      className="h-8 rounded-sm px-3 text-xs"
                    >
                      <Lock className="mr-1 h-3 w-3" />
                      Edit access
                    </Button>
                    {u.id !== currentUser?.id && (
                      u.status === "active" ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          data-testid={`admin-user-disable-${u.id}`}
                          onClick={() => toggleStatus(u)}
                          className="h-8 rounded-sm px-3 text-xs text-destructive hover:bg-destructive-soft"
                        >
                          <UserX className="mr-1 h-3 w-3" />
                          Disable
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          data-testid={`admin-user-enable-${u.id}`}
                          onClick={() => toggleStatus(u)}
                          className="h-8 rounded-sm px-3 text-xs"
                        >
                          <UserCheck className="mr-1 h-3 w-3" />
                          Reactivate
                        </Button>
                      )
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <CreateUserDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false);
          refresh();
        }}
        roles={roles}
      />
      <EditUserAccessDialog
        user={editing}
        roles={roles}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          refresh();
        }}
      />
    </div>
  );
}
