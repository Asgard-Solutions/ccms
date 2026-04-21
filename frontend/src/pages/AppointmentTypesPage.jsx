import AppointmentTypesManager from "./AppointmentTypesManager";

/**
 * Appointment Types — standalone settings page.
 *
 * Thin wrapper that adds the page-level header and hands off to the existing
 * `AppointmentTypesManager` section. Lives under Settings in the sidebar,
 * next to Clinic Settings, Payers and Fee Schedules.
 */
export default function AppointmentTypesPage() {
  return (
    <div
      data-testid="appointment-types-page"
      className="space-y-8 animate-in fade-in duration-300"
    >
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Settings
        </span>
        <h1 className="mt-2 font-display text-4xl font-medium tracking-tight">
          Appointment types
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          Define the reusable visit types that drive default durations in the
          Book Appointment modal. Inactive types are hidden from booking but
          retained for historical reporting.
        </p>
      </header>

      <AppointmentTypesManager />
    </div>
  );
}
