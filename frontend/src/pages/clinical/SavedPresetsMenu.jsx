/**
 * SavedPresetsMenu — Phase 3 Slice 2.
 *
 * Manages the durable, global-scope timeline presets stored under
 * `/me/preferences.clinical_ui_defaults`. All values are sanitized by
 * `timelinePresetsSchema.sanitizePresetFilters` before being sent to
 * the server so patient-scoped fields (`episode_ids`, `q`) can never
 * be persisted — the sanitizer is the last line of defence.
 *
 * Empty / stale-preset states surface as inline chips + toast on
 * apply.
 */
import { Bookmark, ChevronDown, Trash2, X } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { api, formatApiError } from "../../api/client";
import {
  newPresetId,
  sanitizePresetFilters,
  detectStaleness,
} from "./timelinePresetsSchema";
import PresetIconStrip from "./PresetIconStrip";

function usePresets(clinicalUiDefaults, onDefaultsChange) {
  return {
    presets: clinicalUiDefaults?.timeline_presets || [],
    defaultId: clinicalUiDefaults?.default_timeline_preset_id || null,
    async persist(next) {
      try {
        const { data } = await api.patch("/auth/me/preferences", {
          clinical_ui_defaults: next,
        });
        onDefaultsChange?.(data?.clinical_ui_defaults || next);
      } catch (e) {
        toast.error(formatApiError(e));
        throw e;
      }
    },
  };
}

export default function SavedPresetsMenu({
  clinicalUiDefaults,
  onDefaultsChange,
  currentFilters,
  onApplyPreset,
  filterMeta,
}) {
  const { presets, defaultId, persist } = usePresets(
    clinicalUiDefaults,
    onDefaultsChange,
  );
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  // Precompute stale flags on visible presets so the dropdown highlights
  // the ones referencing deleted providers / dropped vocabulary.
  const staleById = useMemo(() => {
    const m = new Map();
    for (const p of presets) {
      const s = detectStaleness(p, filterMeta);
      if (s.stale) m.set(p.id, s);
    }
    return m;
  }, [presets, filterMeta]);

  const doApply = useCallback(
    (preset) => {
      onApplyPreset?.(preset);
      setOpen(false);
      const s = staleById.get(preset.id);
      if (s) {
        const provCount = s.reasons.find((r) => r.key === "provider_ids")?.count || 0;
        toast.warning(
          provCount
            ? `Preset applied — ${provCount} provider${provCount === 1 ? "" : "s"} in this preset ${provCount === 1 ? "is" : "are"} no longer available.`
            : "Preset applied — some filter values are no longer supported and were skipped.",
        );
      }
    },
    [onApplyPreset, staleById],
  );

  const doSaveCurrent = useCallback(async () => {
    const name = newName.trim();
    if (!name) return;
    const { filters, dropped } = sanitizePresetFilters(currentFilters);
    const preset = {
      id: newPresetId(),
      name,
      filters,
    };
    const next = {
      ...(clinicalUiDefaults || {}),
      timeline_presets: [...presets, preset],
    };
    try {
      await persist(next);
      setNewName("");
      setCreating(false);
      setOpen(false);
      if (dropped.length) {
        toast.warning(
          "Preset saved. Patient-specific filters and search text were not included.",
        );
      } else {
        toast.success("Preset saved.");
      }
    } catch {
      /* toast already surfaced */
    }
  }, [newName, currentFilters, clinicalUiDefaults, presets, persist]);

  const doDelete = useCallback(
    async (presetId) => {
      const next = {
        ...(clinicalUiDefaults || {}),
        timeline_presets: presets.filter((p) => p.id !== presetId),
      };
      if (next.default_timeline_preset_id === presetId) {
        delete next.default_timeline_preset_id;
      }
      try {
        await persist(next);
        toast.success("Preset removed.");
      } catch {
        /* toast surfaced */
      }
    },
    [clinicalUiDefaults, presets, persist],
  );

  return (
    <div className="relative inline-block" data-testid="saved-presets-menu">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        data-testid="saved-presets-toggle"
        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-xs text-foreground hover:bg-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
      >
        <Bookmark className="h-3 w-3" aria-hidden="true" />
        Presets
        <ChevronDown className="h-3 w-3" aria-hidden="true" />
      </button>
      {open && (
        <div
          role="menu"
          data-testid="saved-presets-panel"
          className="absolute right-0 z-40 mt-2 w-72 rounded-lg border border-border bg-card p-2 text-xs shadow-lg"
        >
          {presets.length === 0 ? (
            <div
              data-testid="saved-presets-empty"
              className="rounded-md bg-card/40 px-3 py-2 text-muted-foreground"
            >
              No saved presets yet.
            </div>
          ) : (
            <ul className="max-h-64 space-y-1 overflow-y-auto">
              {presets.map((p) => {
                const isDefault = p.id === defaultId;
                const stale = staleById.get(p.id);
                return (
                  <li
                    key={p.id}
                    data-testid={`saved-preset-${p.id}`}
                    className="flex items-center justify-between gap-2 rounded-md px-2 py-1 hover:bg-muted"
                  >
                    <button
                      type="button"
                      onClick={() => doApply(p)}
                      data-testid={`saved-preset-${p.id}-apply`}
                      className="min-w-0 flex-1 space-y-0.5 text-left"
                    >
                      <div className="flex min-w-0 items-center gap-1">
                        {stale && <span aria-hidden="true">⚠ </span>}
                        <span className="truncate font-medium text-foreground">
                          {p.name}
                        </span>
                        {isDefault && (
                          <span className="text-[10px] uppercase text-muted-foreground">
                            · default
                          </span>
                        )}
                      </div>
                      <PresetIconStrip
                        preset={p}
                        filterMeta={filterMeta}
                        testidPrefix={`saved-preset-${p.id}-strip`}
                        size="sm"
                      />
                    </button>
                    <button
                      type="button"
                      aria-label="Delete preset"
                      onClick={() => doDelete(p.id)}
                      data-testid={`saved-preset-${p.id}-delete`}
                      className="shrink-0 rounded-full p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    >
                      <Trash2 className="h-3 w-3" aria-hidden="true" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="mt-2 border-t border-border pt-2">
            {creating ? (
              <div className="space-y-1.5">
                <Input
                  autoFocus
                  value={newName}
                  onChange={(e) => setNewName(e.target.value.slice(0, 40))}
                  placeholder="Preset name (≤ 40 chars)"
                  data-testid="saved-preset-new-name"
                  className="h-7 text-xs"
                />
                <div className="flex justify-end gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setCreating(false);
                      setNewName("");
                    }}
                    data-testid="saved-preset-new-cancel"
                    className="h-7 px-2 text-xs"
                  >
                    <X className="mr-1 h-3 w-3" aria-hidden="true" />
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    onClick={doSaveCurrent}
                    disabled={!newName.trim()}
                    data-testid="saved-preset-new-save"
                    className="h-7 px-2 text-xs"
                  >
                    Save
                  </Button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setCreating(true)}
                data-testid="saved-preset-new"
                className="w-full rounded-md px-2 py-1.5 text-left text-foreground hover:bg-muted"
              >
                + Save current filters as preset
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
