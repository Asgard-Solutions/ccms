/**
 * WorkspaceModeSwitcher — Phase 3 Slice 5A.
 *
 * Compact dropdown that lets the user pick which Clinical workspace
 * mode to render. Only modes their role is allowed to switch into are
 * shown; the selection is persisted to `/me/preferences.clinical_ui_defaults.
 * default_workspace_mode`.
 *
 * This component never grants access — mode is purely a presentational
 * emphasis. Server-side permissions still gate any protected content.
 */
import { useCallback, useState } from "react";
import { LayoutDashboard } from "lucide-react";
import { toast } from "sonner";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { api, formatApiError } from "../../api/client";
import {
  allowedModesForRole,
  effectiveMode,
  MODE_DESCRIPTION,
  MODE_LABEL,
} from "./workspaceModes";
import { trackUiEvent } from "../../utils/telemetry";

export default function WorkspaceModeSwitcher({
  currentUser,
  mode,
  onModeChange,
}) {
  const role = currentUser?.role || "patient";
  const allowed = allowedModesForRole(role);
  const [saving, setSaving] = useState(false);

  const handle = useCallback(
    async (nextMode) => {
      const resolved = effectiveMode({ role, requested: nextMode });
      // Optimistic — the parent updates immediately; if the server
      // rejects the write we roll back and surface a toast.
      onModeChange(resolved);
      trackUiEvent("clinical.workspace.mode_changed", { mode: resolved });
      setSaving(true);
      try {
        const { data } = await api.patch("/auth/me/preferences", {
          clinical_ui_defaults: {
            ...(currentUser?.clinical_ui_defaults || {}),
            default_workspace_mode: resolved,
          },
        });
        // Surface the round-tripped defaults so parents can re-derive.
        if (typeof onModeChange === "function" && data?.clinical_ui_defaults) {
          // no-op — parent already applied optimistically.
        }
      } catch (err) {
        toast.error(formatApiError(err));
        onModeChange(mode); // rollback
      } finally {
        setSaving(false);
      }
    },
    [role, mode, currentUser, onModeChange],
  );

  if (!allowed || allowed.length <= 1) return null;

  return (
    <div
      data-testid="clinical-workspace-mode-switcher"
      className="flex items-center gap-2"
    >
      <LayoutDashboard className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
      <label htmlFor="clinical-workspace-mode" className="sr-only">
        Clinical workspace mode
      </label>
      <Select value={mode} onValueChange={handle}>
        <SelectTrigger
          id="clinical-workspace-mode"
          data-testid="clinical-workspace-mode-trigger"
          disabled={saving}
          className="h-11 min-w-[180px] rounded-full border-border bg-card text-sm"
        >
          <SelectValue placeholder="Workspace mode" />
        </SelectTrigger>
        <SelectContent>
          {allowed.map((m) => (
            <SelectItem
              key={m}
              value={m}
              data-testid={`clinical-workspace-mode-option-${m}`}
              className="text-sm"
            >
              <div className="flex flex-col">
                <span className="font-medium text-foreground">{MODE_LABEL[m]}</span>
                <span className="text-xs text-muted-foreground">{MODE_DESCRIPTION[m]}</span>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
