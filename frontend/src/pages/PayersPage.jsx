import PayersManager from "./billing/PayersManager";

/**
 * Payers — standalone settings page.
 *
 * Insurance/contract payer catalogue used throughout billing. Admin-only CRUD
 * was previously embedded in Clinic Settings; it now has its own route under
 * Settings for scalability.
 */
export default function PayersPage() {
  return (
    <div
      data-testid="payers-page"
      className="space-y-8 animate-in fade-in duration-300"
    >
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Settings
        </span>
        <h1 className="mt-2 font-display text-4xl font-medium tracking-tight">
          Payers
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          Insurance companies, self-pay tiers, auto-insurance carriers and
          workers-comp carriers that claims and fee schedules are anchored to.
        </p>
      </header>

      <PayersManager />
    </div>
  );
}
