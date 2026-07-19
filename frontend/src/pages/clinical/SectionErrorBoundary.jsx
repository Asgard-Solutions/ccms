/**
 * SectionErrorBoundary — Phase 3 Slice 6B.
 *
 * Isolates a single Clinical section's failures so one broken card
 * cannot blank the whole chart. Renders a compact `Section unavailable`
 * message with an accessible Retry when the boundary's `resetKey`
 * changes (or the user clicks Retry).
 *
 * Rules:
 *   - Boundary catches render + lifecycle errors ONLY. Fetch failures
 *     inside a section render normally (each card handles them today)
 *     and this boundary is a defence-in-depth for unexpected crashes.
 *   - The error object is not surfaced verbatim to the user — stack
 *     traces stay behind the scenes for the audit / monitoring layer.
 *   - The boundary never coerces a failed section into a zero count.
 *   - `data-testid="section-error-<slug>"` for automated tests.
 */
import React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

export default class SectionErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, resetToken: 0 };
    this.handleRetry = this.handleRetry.bind(this);
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error) {
    // Emit a PHI-safe telemetry marker so the audit dashboard can spot
    // repeated failures. We deliberately do NOT send the message body.
    try {
      console.error(
        `[clinical.section] "${this.props.slug || "unknown"}" failed to render`,
        { name: error?.name || null },
      );
    } catch {
      /* ignore */
    }
  }

  handleRetry() {
    this.setState((s) => ({ hasError: false, resetToken: s.resetToken + 1 }));
  }

  render() {
    const { slug, label, children } = this.props;
    if (!this.state.hasError) {
      // The `key` forces React to remount children when we retry so
      // stale local state doesn't survive the recovery.
      return (
        <React.Fragment key={this.state.resetToken}>{children}</React.Fragment>
      );
    }
    return (
      <div
        role="alert"
        data-testid={`section-error-${slug || "unknown"}`}
        className="rounded-lg border border-destructive/40 bg-destructive-soft p-4 text-sm"
      >
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
          <div className="flex-1">
            <p className="font-medium text-destructive">
              {label || "Section"} unavailable
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Other parts of the chart are still working. This section will
              try again when you press Retry.
            </p>
          </div>
          <button
            type="button"
            onClick={this.handleRetry}
            data-testid={`section-error-${slug || "unknown"}-retry`}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-destructive/40 bg-card px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive-soft focus:outline-none focus-visible:ring-2 focus-visible:ring-destructive/60"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            Retry
          </button>
        </div>
      </div>
    );
  }
}
