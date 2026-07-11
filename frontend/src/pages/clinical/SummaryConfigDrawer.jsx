/**
 * SummaryConfigDrawer — Phase 3 Slice 5B.
 *
 * A keyboard-native (Move up / Move down) reorder drawer for the
 * summary rail. No drag-and-drop by design — a Move up / Move down
 * pair is fully WCAG-compliant, easier to test, and matches the
 * project's roll-out constraints.
 *
 * Persistence: writes the entire `summary_module_order` array to
 * `/me/preferences.clinical_ui_defaults`. The backend `extra=forbid`
 * schema rejects any patient identifiers, so the payload is safe.
 *
 * Never renders modules the user's role can't see — the caller passes
 * the pruned list of allowed slugs. `Reset to role default` re-derives
 * the ordering from workspaceModes.summaryDefaultsForMode.
 */
import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowUp, RotateCcw, Sliders } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { api, formatApiError } from "../../api/client";
import { reorderSummary, resolveSummaryOrder, summaryDefaultsForMode } from "./workspaceModes";
import { trackUiEvent } from "../../utils/telemetry";

const MODULE_LABEL = {
  active_episode:            "Active episode",
  primary_diagnosis:         "Primary diagnosis",
  current_treatment_plan:    "Current treatment plan",
  next_appointment:          "Next appointment",
  reexam_status:             "Re-exam status",
  documentation_tasks:       "Documentation tasks",
  billing_readiness:         "Billing readiness",
  safety_summary:            "Allergies and medications",
  latest_clinical_response:  "Latest clinical response",
  outcomes_trend:            "Outcomes trend",
  recent_imaging:            "Recent imaging",
  data_quality:              "Data quality",
  next_actions:              "Next actions",
};

export default function SummaryConfigDrawer({
  open,
  onOpenChange,
  mode,
  currentUser,
  order,
  allowedModules,
  onOrderChange,
}) {
  // Local draft so cancel restores the original.
  const [draft, setDraft] = useState(order);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) setDraft(order);
  }, [open, order]);

  const move = useCallback((slug, delta) => {
    setDraft((prev) => reorderSummary({ order: prev, slug, delta }));
  }, []);

  const resetToDefault = useCallback(() => {
    const next = resolveSummaryOrder({ mode, stored: summaryDefaultsForMode(mode) });
    setDraft(next);
    trackUiEvent("clinical.summary_module.reset", { mode });
  }, [mode]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await api.patch("/auth/me/preferences", {
        clinical_ui_defaults: {
          ...(currentUser?.clinical_ui_defaults || {}),
          summary_module_order: draft,
        },
      });
      onOrderChange?.(draft);
      trackUiEvent("clinical.summary_module.reorder_saved", { count: draft.length });
      toast.success("Summary layout saved");
      onOpenChange?.(false);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSaving(false);
    }
  }, [draft, currentUser, onOrderChange, onOpenChange]);

  // Only present modules the caller's role can see. `allowedModules`
  // is the pruned set derived from summary-module registry + role.
  const visible = draft.filter((slug) => (allowedModules ? allowedModules.has(slug) : true));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="summary-config-drawer"
        className="max-w-lg rounded-xl"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-display">
            <Sliders className="h-5 w-5 text-primary" aria-hidden="true" />
            Configure summary
          </DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          Reorder modules with the arrow buttons. Modules you can&apos;t view for
          your role are hidden. This preference is saved to your account, not
          to this patient.
        </p>
        <ol
          data-testid="summary-config-list"
          aria-label="Summary module order"
          className="space-y-1.5"
        >
          {visible.map((slug, idx) => (
            <li
              key={slug}
              data-testid={`summary-config-row-${slug}`}
              className="flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2.5"
            >
              <div className="flex items-center gap-3">
                <span
                  aria-hidden="true"
                  className="inline-flex h-6 min-w-6 items-center justify-center rounded-full bg-muted px-1.5 text-xs font-medium text-muted-foreground"
                >
                  {idx + 1}
                </span>
                <span className="text-sm text-foreground">{MODULE_LABEL[slug] || slug}</span>
              </div>
              <div className="flex gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={idx === 0}
                  onClick={() => move(slug, -1)}
                  data-testid={`summary-config-row-${slug}-up`}
                  aria-label={`Move ${MODULE_LABEL[slug] || slug} up`}
                  className="h-9 w-9 rounded-full"
                >
                  <ArrowUp className="h-4 w-4" aria-hidden="true" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={idx === visible.length - 1}
                  onClick={() => move(slug, +1)}
                  data-testid={`summary-config-row-${slug}-down`}
                  aria-label={`Move ${MODULE_LABEL[slug] || slug} down`}
                  className="h-9 w-9 rounded-full"
                >
                  <ArrowDown className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            </li>
          ))}
        </ol>
        <DialogFooter className="flex items-center justify-between gap-2">
          <Button
            variant="ghost"
            onClick={resetToDefault}
            data-testid="summary-config-reset"
            className="rounded-full"
          >
            <RotateCcw className="mr-1.5 h-4 w-4" aria-hidden="true" />
            Reset to role default
          </Button>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => onOpenChange?.(false)}
              data-testid="summary-config-cancel"
              className="rounded-full"
            >
              Cancel
            </Button>
            <Button
              onClick={save}
              disabled={saving}
              data-testid="summary-config-save"
              className="rounded-full"
            >
              {saving ? "Saving…" : "Save layout"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
