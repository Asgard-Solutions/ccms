import FeeSchedulesManager from "./billing/FeeSchedulesManager";

/**
 * Fee Schedules — standalone settings page.
 *
 * Per-payer CPT pricing grids that seed claim lines and patient statements.
 * Was previously embedded in Clinic Settings; now a top-level page under
 * Settings for direct deep-linking.
 */
export default function FeeSchedulesPage() {
  return (
    <div
      data-testid="fee-schedules-page"
      className="space-y-8 animate-in fade-in duration-300"
    >
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Settings
        </span>
        <h1 className="mt-2 font-display text-4xl font-medium tracking-tight">
          Fee schedules
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          Payer-specific CPT pricing used when coding claims and generating
          patient statements. Effective dates control which schedule applies to
          each date of service.
        </p>
      </header>

      <FeeSchedulesManager />
    </div>
  );
}
