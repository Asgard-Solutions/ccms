/**
 * RoleEditorDialog — create / edit / view a role.
 *
 * Permissions are grouped by module (from
 * GET /api/authz/permission-catalog) and rendered as an accordion with
 * a "select all" toggle per section. View mode on built-in roles
 * shows only what the role grants (read-only).
 *
 * Live plain-English preview via
 * POST /api/authz/roles/preview-effective-permissions.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Flame,
  Save,
  ShieldCheck,
} from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

const SENSITIVITY_STYLE = {
  critical: "text-destructive",
  high: "text-amber-700 dark:text-amber-300",
  medium: "text-muted-foreground",
  low: "text-muted-foreground",
};

export default function RoleEditorDialog({ mode, role, onClose, onSaved }) {
  const readOnly = mode === "view";
  const isCreate = mode === "create";
  const open = !!mode;
  const [catalog, setCatalog] = useState(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [explanation, setExplanation] = useState(null);
  const [openModules, setOpenModules] = useState(() => new Set(["patients", "scheduling"]));
  const [saving, setSaving] = useState(false);
  const previewTimer = useRef(null);

  // Load the permission catalog once the dialog opens.
  useEffect(() => {
    if (!open) return;
    setCatalog(null);
    api.get("/authz/permission-catalog")
      .then((res) => setCatalog(res.data))
      .catch(() => setCatalog({ modules: [], groups: [] }));
  }, [open]);

  // Seed form state from props whenever mode/role changes.
  useEffect(() => {
    if (!open) return;
    if (isCreate) {
      setName("");
      setDescription("");
      setSelected(new Set());
      return;
    }
    setName(role?.name || "");
    setDescription(role?.description || "");
    setSelected(new Set((role?.grants || []).map((g) => g.permission_key)));
  }, [open, mode, role, isCreate]);

  // Debounced preview fetch whenever selection changes.
  useEffect(() => {
    if (!open) return;
    clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(() => {
      const keys = Array.from(selected);
      api.post("/authz/roles/preview-effective-permissions",
               { permission_keys: keys })
        .then((res) => setExplanation(res.data.explanation))
        .catch(() => setExplanation(null));
    }, 250);
    return () => clearTimeout(previewTimer.current);
  }, [open, selected]);

  const totalSelected = selected.size;
  const groupStats = useMemo(() => {
    if (!catalog) return {};
    const out = {};
    for (const g of catalog.groups) {
      const gk = g.permissions.map((p) => p.key);
      const sel = gk.filter((k) => selected.has(k)).length;
      out[g.module] = { selected: sel, total: gk.length };
    }
    return out;
  }, [catalog, selected]);

  function togglePerm(key) {
    if (readOnly) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleAllInGroup(group, nextValue) {
    if (readOnly) return;
    setSelected((prev) => {
      const next = new Set(prev);
      for (const p of group.permissions) {
        if (nextValue) next.add(p.key);
        else next.delete(p.key);
      }
      return next;
    });
  }

  function toggleModuleOpen(key) {
    setOpenModules((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function save() {
    if (readOnly) return;
    if (!name.trim() || name.trim().length < 2) {
      toast.error("Role name must be at least 2 characters");
      return;
    }
    if (selected.size === 0) {
      toast.error("Select at least one permission");
      return;
    }
    setSaving(true);
    try {
      const permission_keys = Array.from(selected);
      if (isCreate) {
        await api.post("/authz/roles", {
          name: name.trim(),
          description: description.trim(),
          permission_keys,
        });
        toast.success("Custom role created");
      } else {
        await api.patch(`/authz/roles/${role.key}`, {
          name: name.trim(),
          description: description.trim(),
          permission_keys,
        });
        toast.success("Role updated");
      }
      onSaved?.();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to save role");
    } finally {
      setSaving(false);
    }
  }

  const title = isCreate
    ? "New custom role"
    : readOnly
    ? `${role?.name || "Role"} · Built-in`
    : `Edit ${role?.name || "role"}`;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        data-testid="role-editor-dialog"
        className="max-w-4xl max-h-[92vh] overflow-y-auto rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">{title}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-1">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Role name</Label>
              <Input
                data-testid="role-editor-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={readOnly}
                className="rounded-sm"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Description</Label>
              <Textarea
                data-testid="role-editor-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={readOnly}
                rows={1}
                className="rounded-sm"
              />
            </div>
          </div>

          <div className="flex items-center justify-between rounded-sm border border-border bg-muted/30 px-4 py-2">
            <div className="text-xs text-muted-foreground">
              {readOnly
                ? "Built-in roles are read-only. Clone to customize."
                : `${totalSelected} permission${totalSelected === 1 ? "" : "s"} selected`}
            </div>
            {!readOnly && explanation && (
              <div
                data-testid="role-editor-sensitive-count"
                className="text-xs text-muted-foreground"
              >
                {explanation.sensitive_grants?.length || 0} sensitive
              </div>
            )}
          </div>

          <section data-testid="role-editor-modules" className="space-y-2">
            {catalog === null ? (
              <p className="text-sm text-muted-foreground">Loading permission catalog…</p>
            ) : (
              catalog.groups.map((g) => (
                <ModuleAccordion
                  key={g.module}
                  group={g}
                  selected={selected}
                  stats={groupStats[g.module]}
                  isOpen={openModules.has(g.module)}
                  onToggleOpen={() => toggleModuleOpen(g.module)}
                  onTogglePerm={togglePerm}
                  onToggleAll={(val) => toggleAllInGroup(g, val)}
                  readOnly={readOnly}
                />
              ))
            )}
          </section>

          {explanation && (
            <div
              data-testid="role-editor-preview"
              className="rounded-sm border border-primary/30 bg-primary/5 p-4"
            >
              <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-primary">
                <CheckCircle2 className="h-3.5 w-3.5" />
                What this role can do
              </h3>
              <p className="mt-2 text-sm">{explanation.summary}</p>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            className="rounded-sm"
            data-testid="role-editor-close"
          >
            {readOnly ? "Close" : "Cancel"}
          </Button>
          {!readOnly && (
            <Button
              type="button"
              disabled={saving}
              onClick={save}
              data-testid="role-editor-save"
              className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            >
              <Save className="mr-1.5 h-4 w-4" />
              {saving ? "Saving…" : (isCreate ? "Create role" : "Save changes")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ModuleAccordion({
  group, selected, stats, isOpen, onToggleOpen,
  onTogglePerm, onToggleAll, readOnly,
}) {
  const allSelected = stats && stats.selected === stats.total && stats.total > 0;
  const someSelected = stats && stats.selected > 0 && stats.selected < stats.total;

  return (
    <div
      data-testid={`role-editor-module-${group.module}`}
      className="overflow-hidden rounded-sm border border-border"
    >
      <button
        type="button"
        onClick={onToggleOpen}
        className="flex w-full items-center justify-between gap-2 bg-muted/40 px-4 py-2.5 text-left hover:bg-muted/60"
        data-testid={`role-editor-module-toggle-${group.module}`}
      >
        <div className="flex items-center gap-2">
          {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          <div>
            <p className="font-medium">{group.label}</p>
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {group.description}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge
            variant="outline"
            data-testid={`role-editor-module-count-${group.module}`}
            className="rounded-sm text-[10px]"
          >
            {stats?.selected || 0}/{stats?.total || group.permissions.length}
          </Badge>
          {!readOnly && (
            <span
              role="button"
              data-testid={`role-editor-module-select-all-${group.module}`}
              onClick={(e) => {
                e.stopPropagation();
                onToggleAll(!allSelected);
              }}
              className="rounded-sm border border-border bg-background px-2 py-1 text-[10px] font-medium hover:bg-muted"
            >
              {allSelected ? "Clear all" : someSelected ? "Select rest" : "Select all"}
            </span>
          )}
        </div>
      </button>
      {isOpen && (
        <ul className="divide-y divide-border bg-card">
          {group.permissions.map((p) => {
            const on = selected.has(p.key);
            return (
              <li
                key={p.key}
                data-testid={`role-editor-perm-${p.key}`}
                onClick={() => onTogglePerm(p.key)}
                className={`flex cursor-pointer items-start gap-3 px-4 py-2 text-sm transition
                  ${on ? "bg-primary/5" : "hover:bg-muted/40"}
                  ${readOnly && !on ? "opacity-50" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={on}
                  disabled={readOnly}
                  onChange={() => onTogglePerm(p.key)}
                  className="mt-1 h-4 w-4"
                  onClick={(e) => e.stopPropagation()}
                />
                <div className="flex-1">
                  <p className="font-medium">
                    {p.label}
                    {(p.destructive || p.sensitivity === "critical") && (
                      <Flame className="ml-1.5 inline h-3 w-3 text-destructive" />
                    )}
                  </p>
                  {p.help && (
                    <p className="text-[11px] text-muted-foreground">{p.help}</p>
                  )}
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    <span
                      className={`text-[10px] font-medium uppercase tracking-wide ${
                        SENSITIVITY_STYLE[p.sensitivity] || "text-muted-foreground"
                      }`}
                    >
                      {p.sensitivity}
                    </span>
                    {p.phi && <Badge variant="outline" className="rounded-sm text-[9px]">PHI</Badge>}
                    {p.financial && <Badge variant="outline" className="rounded-sm text-[9px]">Financial</Badge>}
                    {p.privileged && (
                      <Badge variant="outline" className="rounded-sm text-[9px] text-amber-700 dark:text-amber-300">
                        <ShieldCheck className="mr-1 h-2.5 w-2.5" />
                        privileged
                      </Badge>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
