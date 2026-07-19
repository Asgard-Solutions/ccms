/**
 * NextActionsPanel — Phase 3 Slice 1.
 *
 * Renders the deterministic Next Actions surface computed by
 * `nextActionsEngine.deriveNextActions`. Each row emits a strictly
 * scoped telemetry event (`clinical_next_action_interaction`) on
 * *interaction attempt* — never on rule generation and never on
 * dismissal-persistence (dismissals are transient and stay client-side).
 *
 * Rendering rules:
 *  - Rows are sorted by rule priority (engine handles this).
 *  - Optional rows expose a "Dismiss" affordance. Dismissed ids are
 *    kept in session-scope only via `useClinicalReturnState`, so a
 *    logout / tenant switch / TTL expiry resets them.
 *  - Mandatory rows (unsigned notes, missing docs, blocked billing,
 *    missing intake, billing warnings) are non-dismissible.
 */
import { AlertTriangle, ChevronRight, Info, X } from "lucide-react";
import { useCallback, useMemo } from "react";
import { Button } from "../../components/ui/button";
import { trackNextActionInteraction } from "../../utils/telemetry";
import { deriveNextActions } from "./nextActionsEngine";
import { useClinicalReturnState } from "./useClinicalReturnState";

const TONE_CLASS = {
  destructive: "border-destructive/40 bg-destructive-soft text-destructive",
  warning: "border-warning/40 bg-warning-soft text-warning",
  info: "border-primary/30 bg-primary/10 text-primary",
};

function ToneIcon({ tone }) {
  if (tone === "destructive" || tone === "warning") {
    return <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />;
  }
  return <Info className="h-4 w-4 shrink-0" aria-hidden="true" />;
}

export default function NextActionsPanel({
  canWrite,
  summary,
  activePlan,
  primaryDx,
  missingIntakeCount,
  reExamDue,
  billingAggregate,
  encounterGroups,
  routeInstanceToken,
  onJumpTo,
}) {
  const { state, saveState } = useClinicalReturnState({
    section: "next-actions",
    routeInstanceToken,
  });

  const dismissedIds = useMemo(
    () => new Set(Array.isArray(state?.dismissed) ? state.dismissed : []),
    [state],
  );

  const actions = useMemo(
    () =>
      deriveNextActions({
        canWrite,
        summary,
        activePlan,
        primaryDx,
        missingIntakeCount,
        reExamDue,
        billingAggregate,
        encounterGroups,
        dismissedIds,
      }),
    [
      canWrite,
      summary,
      activePlan,
      primaryDx,
      missingIntakeCount,
      reExamDue,
      billingAggregate,
      encounterGroups,
      dismissedIds,
    ],
  );

  const handleOpen = useCallback(
    (action) => {
      trackNextActionInteraction({
        action_id: action.id,
        interaction: "opened",
      });
      // Persist a return breadcrumb: which action the user opened,
      // and the section they departed from. Kept in session-scope,
      // opaque to patient identity.
      saveState({
        last_opened: {
          id: action.id,
          section: action.target?.section || null,
        },
      });
      if (action.target?.section) {
        onJumpTo?.(action.target.section, { userInitiated: true });
      }
    },
    [saveState, onJumpTo],
  );

  const handleDismiss = useCallback(
    (action) => {
      trackNextActionInteraction({
        action_id: action.id,
        interaction: "dismissed",
      });
      const next = new Set(dismissedIds);
      next.add(action.id);
      saveState({ dismissed: Array.from(next) });
    },
    [dismissedIds, saveState],
  );

  if (!actions.length) {
    return (
      <section
        data-testid="next-actions-panel"
        aria-labelledby="next-actions-title"
        className="rounded-xl border border-border bg-card/60 p-5"
      >
        <div className="mb-2">
          <h3
            id="next-actions-title"
            className="font-display text-lg font-semibold text-foreground"
          >
            Next actions
          </h3>
          <p className="text-sm text-muted-foreground">
            Deterministic workflow follow-ups based on this chart&rsquo;s current state.
          </p>
        </div>
        <div
          data-testid="next-actions-empty"
          className="rounded-lg border border-dashed border-border bg-card/40 px-5 py-4 text-sm text-muted-foreground"
        >
          Nothing to follow up on right now.
        </div>
      </section>
    );
  }

  return (
    <section
      data-testid="next-actions-panel"
      aria-labelledby="next-actions-title"
      className="rounded-xl border border-border bg-card/60 p-5"
    >
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3
            id="next-actions-title"
            className="font-display text-lg font-semibold text-foreground"
          >
            Next actions
          </h3>
          <p className="text-sm text-muted-foreground">
            {actions.length} workflow follow-up{actions.length === 1 ? "" : "s"} derived from this chart&rsquo;s structured data.
          </p>
        </div>
      </div>
      <ul data-testid="next-actions-list" className="space-y-2">
        {actions.map((a) => (
          <li
            key={a.id}
            data-testid={`next-action-${a.id}`}
            className={[
              "flex flex-wrap items-start justify-between gap-3 rounded-lg border p-3",
              TONE_CLASS[a.tone] || "border-border bg-card",
            ].join(" ")}
          >
            <div className="flex min-w-0 flex-1 items-start gap-2">
              <ToneIcon tone={a.tone} />
              <div className="min-w-0 flex-1">
                <div
                  className="text-sm font-medium text-foreground"
                  data-testid={`next-action-${a.id}-label`}
                >
                  {a.label}
                </div>
                <div
                  className="mt-0.5 text-xs text-muted-foreground"
                  data-testid={`next-action-${a.id}-why`}
                >
                  {a.why}
                </div>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleOpen(a)}
                data-testid={`next-action-${a.id}-open`}
                className="rounded-full"
              >
                Open
                <ChevronRight className="ml-1 h-3.5 w-3.5" aria-hidden="true" />
              </Button>
              {a.dismissible && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleDismiss(a)}
                  data-testid={`next-action-${a.id}-dismiss`}
                  aria-label="Dismiss"
                  className="rounded-full text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
