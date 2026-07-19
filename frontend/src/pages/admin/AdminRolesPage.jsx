/**
 * /admin/roles — Simplified role management surface.
 *
 * Replaces the matrix-first /roles experience with a card-style list:
 *   - Built-in (system) roles — view-only with "Clone to customize"
 *   - Custom roles — view / edit / duplicate / archive
 *
 * Role editing happens inline via RoleEditorDialog, which groups
 * permissions by module with plain-English labels.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Copy,
  Lock,
  Plus,
  Shield,
  ShieldCheck,
  Sparkles,
  Trash2,
  Users,
} from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import RoleEditorDialog from "./RoleEditorDialog";
import ConfirmDialog from "../../components/ConfirmDialog";

const COMMON_ROLE_ORDER = [
  "clinic_manager", "org_owner", "provider", "front_desk",
  "clinical_staff", "billing_specialist", "auditor",
  "compliance_officer", "patient_portal",
];
const INTERNAL_ROLE_KEYS = new Set(["super_admin", "integration_account"]);

export default function AdminRolesPage() {
  const [roles, setRoles] = useState(null);
  const [editor, setEditor] = useState(null); // {mode, role}
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [showInternal, setShowInternal] = useState(false);

  async function refresh() {
    try {
      const { data } = await api.get("/authz/roles", {
        params: { include_user_counts: true },
      });
      setRoles(data);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to load roles");
      setRoles([]);
    }
  }
  useEffect(() => { refresh(); }, []);

  const { common, custom, internal } = useMemo(() => {
    const list = roles || [];
    const buckets = { common: [], custom: [], internal: [] };
    for (const r of list) {
      if (INTERNAL_ROLE_KEYS.has(r.key)) buckets.internal.push(r);
      else if (r.is_system) buckets.common.push(r);
      else buckets.custom.push(r);
    }
    buckets.common.sort((a, b) => {
      const ai = COMMON_ROLE_ORDER.indexOf(a.key);
      const bi = COMMON_ROLE_ORDER.indexOf(b.key);
      if (ai === -1 && bi === -1) return a.name.localeCompare(b.name);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });
    buckets.custom.sort((a, b) => a.name.localeCompare(b.name));
    return buckets;
  }, [roles]);

  async function cloneRole(role) {
    const name = window.prompt(
      `Name for the new custom role (cloning "${role.name}")`,
      `${role.name} (copy)`,
    );
    if (!name) return;
    try {
      const { data } = await api.post(`/authz/roles/${role.key}/clone`, { name });
      toast.success("Role cloned — now editing");
      setEditor({ mode: "edit", role: data });
      refresh();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to clone role");
    }
  }

  async function archiveRole(role, { force = false } = {}) {
    try {
      await api.delete(`/authz/roles/${role.key}`, { params: { force } });
      toast.success("Role archived");
      setConfirmDelete(null);
      refresh();
    } catch (err) {
      const msg = err.response?.data?.detail || "Failed to archive role";
      if (err.response?.status === 409 && !force) {
        setConfirmDelete({ role, inUseMessage: msg });
      } else {
        toast.error(msg);
      }
    }
  }

  return (
    <div data-testid="admin-roles-page" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            User Management
          </span>
          <h1 className="mt-1 font-display text-3xl font-medium tracking-tight">Roles</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Reusable job templates. Built-in roles match common clinic
            jobs — clone any of them to make a custom role.
          </p>
        </div>
        <Button
          data-testid="admin-roles-new-btn"
          onClick={() => setEditor({ mode: "create", role: null })}
          className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
        >
          <Plus className="mr-1.5 h-4 w-4" />
          New custom role
        </Button>
      </header>

      {roles === null ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-24 rounded-sm" />)}
        </div>
      ) : (
        <>
          <RoleGrid
            testid="admin-roles-common"
            title="Built-in roles"
            subtitle="Standard clinic jobs. View-only — clone to customize."
            roles={common}
            icon={Shield}
            onView={(r) => setEditor({ mode: "view", role: r })}
            onClone={cloneRole}
          />

          <RoleGrid
            testid="admin-roles-custom"
            title="Custom roles"
            subtitle="Your clinic's custom role templates."
            roles={custom}
            icon={Sparkles}
            emptyText={"No custom roles yet — clone a built-in role or click \u201cNew custom role\u201d."}
            onView={(r) => setEditor({ mode: "edit", role: r })}
            onClone={cloneRole}
            onDelete={(r) => setConfirmDelete({ role: r, inUseMessage: null })}
            canEdit
          />

          {internal.length > 0 && (
            <section data-testid="admin-roles-internal">
              <button
                type="button"
                data-testid="admin-roles-toggle-internal"
                onClick={() => setShowInternal((v) => !v)}
                className="text-xs font-medium text-primary hover:underline"
              >
                {showInternal ? "Hide" : "Show"} internal / service roles ({internal.length})
              </button>
              {showInternal && (
                <div className="mt-3">
                  <RoleGrid
                    testid="admin-roles-internal-list"
                    title=""
                    subtitle=""
                    roles={internal}
                    icon={Lock}
                    onView={(r) => setEditor({ mode: "view", role: r })}
                  />
                </div>
              )}
            </section>
          )}
        </>
      )}

      <RoleEditorDialog
        mode={editor?.mode}
        role={editor?.role}
        onClose={() => setEditor(null)}
        onSaved={() => { setEditor(null); refresh(); }}
      />

      <ConfirmDialog
        open={!!confirmDelete}
        onOpenChange={(v) => !v && setConfirmDelete(null)}
        title="Archive this role?"
        description={
          confirmDelete?.inUseMessage
          ? `${confirmDelete.inUseMessage} Archiving now will unassign it from every user.`
          : `The role "${confirmDelete?.role?.name}" will be archived. Users currently assigned to it will lose that access.`
        }
        confirmLabel={confirmDelete?.inUseMessage ? "Archive and unassign" : "Archive"}
        destructive
        testId="admin-roles-confirm-delete"
        onConfirm={() => archiveRole(
          confirmDelete.role,
          { force: !!confirmDelete.inUseMessage },
        )}
      />
    </div>
  );
}

function RoleGrid({
  testid, title, subtitle, roles, icon: Icon, emptyText,
  onView, onClone, onDelete, canEdit = false,
}) {
  return (
    <section data-testid={testid} className="space-y-3">
      {title && (
        <div>
          <h2 className="font-display text-base font-medium">{title}</h2>
          {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
        </div>
      )}
      {roles.length === 0 ? (
        <div
          data-testid={`${testid}-empty`}
          className="rounded-sm border border-dashed border-border px-5 py-10 text-center text-sm text-muted-foreground"
        >
          {emptyText || "No roles yet."}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {roles.map((r) => (
            <article
              key={r.key}
              data-testid={`admin-roles-card-${r.key}`}
              className="flex flex-col gap-3 rounded-sm border border-border bg-card p-4"
            >
              <div className="flex items-start gap-2">
                <Icon className="mt-0.5 h-4 w-4 text-muted-foreground" />
                <div className="flex-1">
                  <p className="font-medium">{r.name}</p>
                  <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                    {r.description || "No description."}
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="outline" className="rounded-sm text-[10px]">
                  {(r.grants || []).length} permissions
                </Badge>
                <Badge
                  variant="outline"
                  data-testid={`admin-roles-card-users-${r.key}`}
                  className="rounded-sm text-[10px] text-muted-foreground"
                >
                  <Users className="mr-1 h-2.5 w-2.5" />
                  {r.user_count ?? 0} users
                </Badge>
                {r.is_system && (
                  <Badge variant="outline" className="rounded-sm text-[10px]">
                    built-in
                  </Badge>
                )}
                {r.privileged && (
                  <Badge variant="outline" className="rounded-sm text-[10px] text-amber-700 dark:text-amber-300">
                    privileged
                  </Badge>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5">
                <Button
                  size="sm"
                  variant="outline"
                  data-testid={`admin-roles-view-${r.key}`}
                  onClick={() => onView(r)}
                  className="h-8 rounded-sm px-3 text-xs"
                >
                  <ShieldCheck className="mr-1 h-3 w-3" />
                  {canEdit ? "Edit" : "View"}
                </Button>
                {onClone && (
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid={`admin-roles-clone-${r.key}`}
                    onClick={() => onClone(r)}
                    className="h-8 rounded-sm px-3 text-xs"
                  >
                    <Copy className="mr-1 h-3 w-3" />
                    Clone
                  </Button>
                )}
                {canEdit && onDelete && (
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid={`admin-roles-delete-${r.key}`}
                    onClick={() => onDelete(r)}
                    className="h-8 rounded-sm px-3 text-xs text-destructive hover:bg-destructive-soft"
                  >
                    <Trash2 className="mr-1 h-3 w-3" />
                    Archive
                  </Button>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
