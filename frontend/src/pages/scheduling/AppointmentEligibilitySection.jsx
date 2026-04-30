import { useCallback, useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { Button } from "../../components/ui/button";
import { toast } from "sonner";
import {
  fetchAppointmentEligibilityLatest,
  runAppointmentEligibilityCheck,
} from "../billing/useBillingAdmin";
import {
  EligibilityStatusBanner,
  EligibilityStatusChip,
} from "../billing/eligibility/statusMeta";
import { EligibilityDialog } from "../billing/EligibilityDialog";
import { formatCents } from "../../utils/money";


/** Eligibility strip embedded inside AppointmentWorkflowPanel.
 *  Responsibility: show the coverage chip + Verify/Re-verify CTA for
 *  the patient's DOS, and open the full dialog on demand. */
export function AppointmentEligibilitySection({
  appointment,
  onOpenDialog,
}) {
  const [latest, setLatest] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    if (!appointment?.id) return;
    setLoading(true);
    try {
      const row = await fetchAppointmentEligibilityLatest(appointment.id);
      setLatest(row);
    } catch {
      setLatest(null);
    } finally { setLoading(false); }
  }, [appointment?.id]);

  useEffect(() => { load(); }, [load]);

  async function verify() {
    setRunning(true);
    try {
      const fresh = await runAppointmentEligibilityCheck(appointment.id, {
        service_type_codes: ["30", "33", "98"],
      });
      toast.success(
        fresh.status === "active" ? "Coverage active" :
        fresh.status === "partial" ? "Coverage active (partial)" :
        `Eligibility: ${fresh.status}`,
      );
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Eligibility check failed");
    } finally { setRunning(false); }
  }

  const status = latest?.effective_status || latest?.status || "not_checked";
  const warn = ["inactive", "rejected", "error", "expired", "unknown"]
    .includes(status);

  if (loading) return null;

  return (
    <section
      data-testid="appt-eligibility-section"
      className="space-y-2 border-t border-border/60 pt-3"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs">
          <Activity className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="font-medium uppercase tracking-wide text-muted-foreground">
            Eligibility
          </span>
          <EligibilityStatusChip
            status={status}
            testid={`appt-eligibility-chip-${status}`}
          />
        </div>
        <div className="flex gap-1">
          <Button
            size="sm" variant="outline"
            onClick={verify} disabled={running}
            data-testid="appt-eligibility-verify-btn"
            className="rounded-sm"
          >
            <RefreshCw className={`mr-1 h-3.5 w-3.5 ${running ? "animate-spin" : ""}`} />
            {running ? "Checking…" : latest ? "Re-verify" : "Verify"}
          </Button>
          {latest && onOpenDialog && (
            <Button
              size="sm" variant="ghost"
              onClick={onOpenDialog}
              data-testid="appt-eligibility-details-btn"
              className="rounded-sm"
            >
              Details
            </Button>
          )}
        </div>
      </div>

      {/* Warn banner is only shown for caller-relevant risk states so
          the panel stays uncluttered for the happy path. */}
      {warn && latest && (
        <EligibilityStatusBanner
          status={status}
          subtitle={buildWarning(latest, status)}
          testid={`appt-eligibility-warning-${status}`}
        />
      )}

      {/* One-line financial recap when we have benefits. */}
      {(status === "active" || status === "partial") && latest && (
        <p className="text-[11px] text-muted-foreground">
          {latest.plan_name ? `${latest.plan_name} · ` : ""}
          {latest.copay_cents != null && `copay ${formatCents(latest.copay_cents)}`}
          {latest.deductible_cents != null && ` · deductible ${formatCents(latest.deductible_cents)}`}
          {latest.coinsurance_pct != null && ` · ${latest.coinsurance_pct}% coinsurance`}
        </p>
      )}

      {!latest && (
        <p
          data-testid="appt-eligibility-not-checked"
          className="text-[11px] text-muted-foreground"
        >
          Eligibility has not been checked for this appointment yet.
        </p>
      )}
    </section>
  );
}


function buildWarning(latest, status) {
  if (status === "inactive") return "Payer reports inactive coverage.";
  if (status === "rejected") return latest.rejection_reason || "Payer rejected the inquiry.";
  if (status === "error")    return "Eligibility connection errored — retry before the visit.";
  if (status === "expired")  return "Stored check is stale for this service date.";
  if (status === "unknown")  return "Payer response inconclusive.";
  return null;
}


/** Convenience wrapper — renders the strip PLUS wires up the full
 *  EligibilityDialog modal so the host panel only needs one import. */
export function AppointmentEligibilityWithDialog({ appointment }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <AppointmentEligibilitySection
        appointment={appointment}
        onOpenDialog={() => setOpen(true)}
      />
      <EligibilityDialog
        open={open}
        appointmentId={appointment?.id}
        patientId={appointment?.patient_id}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
