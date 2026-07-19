/**
 * EditUserAccessDialog — edit role assignments for an existing user.
 *
 * Minimal modal: pick/unpick roles → save. Shows plain-English
 * effective-access preview live. Uses existing
 * POST /authz/users/{id}/roles + DELETE /authz/users/{id}/roles/{key}.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, Save, ShieldCheck } from "lucide-react";
import { api } from "../../api/client";
import { Button } from "../../components/ui/button";
import { Badge } from "../../components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

const ADVANCED_ROLE_KEYS = new Set(["integration_account", "super_admin"]);

export default function EditUserAccessDialog({ user, roles, onClose, onSaved }) {
  const [currentKeys, setCurrentKeys] = useState([]);
  const [selected, setSelected] = useState([]);
  const [explanation, setExplanation] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!user) return;
    api.get(`/authz/users/${user.id}/effective-permissions`, {
      params: { explain: "true" },
    }).then((res) => {
      setCurrentKeys(res.data.role_keys || []);
      setSelected(res.data.role_keys || []);
      setExplanation(res.data.explanation || null);
    }).catch(() => {
      setCurrentKeys([]);
      setSelected([]);
    });
  }, [user]);

  useEffect(() => {
    if (!user) return;
    if (selected.length === 0) {
      setExplanation({
        summary: "This user will have no access yet.",
        sensitive_grants: [],
      });
      return;
    }
    const keys = new Set();
    for (const rk of selected) {
      const r = roles.find((x) => x.key === rk);
      if (!r) continue;
      for (const g of (r.grants || [])) keys.add(g.permission_key);
    }
    api.post("/authz/roles/preview-effective-permissions",
             { permission_keys: Array.from(keys) })
       .then((res) => setExplanation(res.data.explanation))
       .catch(() => setExplanation(null));
  }, [selected, roles, user]);

  if (!user) return null;

  const commonRoles = roles
    .filter((r) => !ADVANCED_ROLE_KEYS.has(r.key))
    .sort((a, b) => a.name.localeCompare(b.name));
  const advancedRoles = roles
    .filter((r) => ADVANCED_ROLE_KEYS.has(r.key))
    .sort((a, b) => a.name.localeCompare(b.name));

  async function save() {
    setSaving(true);
    try {
      const currentSet = new Set(currentKeys);
      const newSet = new Set(selected);
      const toAdd = [...newSet].filter((k) => !currentSet.has(k));
      const toRemove = [...currentSet].filter((k) => !newSet.has(k));
      for (const k of toAdd) {
        await api.post(`/authz/users/${user.id}/roles`, { role_key: k });
      }
      for (const k of toRemove) {
        await api.delete(`/authz/users/${user.id}/roles/${k}`);
      }
      toast.success("Access updated");
      onSaved?.();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to update access");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={!!user} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        data-testid="edit-user-access-dialog"
        className="max-w-2xl max-h-[92vh] overflow-y-auto rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            Edit access — {user.name || user.email}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <p className="text-sm text-muted-foreground">
            Add or remove roles. Changes apply on save.
          </p>
          <div className="space-y-1">
            {commonRoles.map((r) => (
              <RoleCheckbox
                key={r.key}
                role={r}
                checked={selected.includes(r.key)}
                onToggle={(v) => setSelected((prev) =>
                  v ? [...prev, r.key] : prev.filter((k) => k !== r.key),
                )}
              />
            ))}
          </div>
          {advancedRoles.length > 0 && (
            <div>
              <button
                type="button"
                data-testid="edit-user-toggle-advanced"
                onClick={() => setShowAdvanced((s) => !s)}
                className="text-xs font-medium text-primary hover:underline"
              >
                {showAdvanced ? "Hide" : "Show"} advanced / internal roles
              </button>
              {showAdvanced && (
                <div className="mt-2 space-y-1 rounded-sm border border-dashed border-border p-2">
                  {advancedRoles.map((r) => (
                    <RoleCheckbox
                      key={r.key}
                      role={r}
                      checked={selected.includes(r.key)}
                      onToggle={(v) => setSelected((prev) =>
                        v ? [...prev, r.key] : prev.filter((k) => k !== r.key),
                      )}
                    />
                  ))}
                </div>
              )}
            </div>
          )}

          <div
            data-testid="edit-user-effective-summary"
            className="rounded-sm border border-primary/30 bg-primary/5 p-4"
          >
            <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-primary">
              <CheckCircle2 className="h-3.5 w-3.5" />
              Effective access preview
            </h3>
            {explanation ? (
              <>
                <p className="mt-2 text-sm">{explanation.summary}</p>
                {explanation.sensitive_grants?.length > 0 && (
                  <div className="mt-3">
                    <p className="text-xs font-medium text-muted-foreground">
                      Sensitive permissions:
                    </p>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {explanation.sensitive_grants.map((s) => (
                        <Badge
                          key={s}
                          variant="outline"
                          className="rounded-sm text-[10px] text-amber-700 dark:text-amber-300"
                        >
                          {s}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">Computing…</p>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            className="rounded-sm"
            data-testid="edit-user-cancel"
          >
            Cancel
          </Button>
          <Button
            type="button"
            disabled={saving}
            onClick={save}
            data-testid="edit-user-save"
            className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
          >
            <Save className="mr-1.5 h-4 w-4" />
            {saving ? "Saving…" : "Save access"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RoleCheckbox({ role, checked, onToggle }) {
  return (
    <label
      data-testid={`edit-user-role-${role.key}`}
      className={`flex cursor-pointer items-start gap-3 rounded-sm border px-3 py-2.5 transition
        ${checked ? "border-primary bg-primary/5" : "border-border hover:bg-muted/50"}`}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onToggle(e.target.checked)}
        className="mt-1 h-4 w-4"
      />
      <div className="flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium">{role.name}</p>
          {role.is_system && (
            <Badge variant="outline" className="rounded-sm text-[10px] text-muted-foreground">
              built-in
            </Badge>
          )}
          {role.is_custom && !role.is_system && (
            <Badge variant="outline" className="rounded-sm text-[10px] text-primary">
              custom
            </Badge>
          )}
        </div>
        {role.description && (
          <p className="mt-0.5 text-xs text-muted-foreground">{role.description}</p>
        )}
      </div>
      <ShieldCheck className={`h-4 w-4 ${checked ? "text-primary" : "text-muted-foreground/40"}`} />
    </label>
  );
}
