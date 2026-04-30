import { useCallback, useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import {
  fetchPatientEligibilityLatest,
  runPatientEligibilityCheck,
} from "./useBillingAdmin";
import { formatDate, formatDateTime } from "../../utils/time";
import { formatCents } from "../../utils/money";
import { EligibilityDialog } from "./EligibilityDialog";
import {
  EligibilityStatusBanner,
  EligibilityStatusChip,
} from "./eligibility/statusMeta";
import { toast } from "sonner";


/** Top-level Eligibility card shown on the Patient overview.
 *  Exposes: latest status chip + last-checked-at + Verify-now CTA +
 *  click-through to the full history dialog. */
export function PatientEligibilityCard({ patientId, canRun = true }) {
  const [latest, setLatest] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    if (!patientId) return;
    setLoading(true);
    try {
      const row = await fetchPatientEligibilityLatest(patientId);
      setLatest(row);
    } catch (e) {
      // Silent on 404 — card renders "Not checked".
      setLatest(null);
    } finally { setLoading(false); }
  }, [patientId]);

  useEffect(() => { load(); }, [load]);

  async function verifyNow() {
    setRunning(true);
    try {
      const fresh = await runPatientEligibilityCheck(patientId, {
        service_type_codes: ["30", "33", "98"],
      });
      toast.success(
        fresh.status === "active" ? "Coverage active" :
        fresh.status === "partial" ? "Coverage active (partial)" :
        `Eligibility status: ${fresh.status}`,
      );
      await load();
    } catch (e) {
      toast.error(
        e?.response?.data?.detail
          || "Eligibility check failed — see patient insurance card.",
      );
    } finally { setRunning(false); }
  }

  const status = latest?.effective_status || latest?.status || "not_checked";
  const subtitle = buildSubtitle(latest);
  const showStats = status === "active" || status === "partial";

  return (
    <>
      <section
        data-testid="patient-eligibility-card"
        className="rounded-sm border border-border bg-card p-4"
      >
        <header className="mb-3 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-muted-foreground" />
            <h3 className="font-display text-sm font-medium tracking-tight">
              Insurance eligibility
            </h3>
            {latest && (
              <EligibilityStatusChip
                status={status}
                className="ml-1"
                testid="patient-eligibility-chip"
              />
            )}
          </div>
          <div className="flex items-center gap-1">
            {canRun && (
              <Button
                size="sm" variant="outline"
                onClick={verifyNow} disabled={running}
                data-testid="patient-eligibility-verify-btn"
                className="rounded-sm"
              >
                <RefreshCw className={`mr-1 h-3.5 w-3.5 ${running ? "animate-spin" : ""}`} />
                {running ? "Checking…" : latest ? "Re-verify" : "Verify now"}
              </Button>
            )}
            {latest && (
              <Button
                size="sm" variant="ghost"
                onClick={() => setOpen(true)}
                data-testid="patient-eligibility-open-btn"
                className="rounded-sm"
              >
                History
              </Button>
            )}
          </div>
        </header>

        {loading ? (
          <Skeleton className="h-16 w-full" />
        ) : !latest ? (
          <EligibilityStatusBanner
            status="not_checked"
            subtitle="No eligibility check on file. Verify coverage before the next visit."
            testid="patient-eligibility-empty"
          />
        ) : (
          <div className="space-y-2">
            <EligibilityStatusBanner
              status={status}
              subtitle={subtitle}
              sandbox={latest.sandbox}
              testid={`patient-eligibility-banner-${status}`}
            />
            {showStats && (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 text-xs">
                <MiniStat label="Copay"
                          value={latest.copay_cents != null ? formatCents(latest.copay_cents) : "—"} />
                <MiniStat label="Deductible"
                          value={latest.deductible_cents != null ? formatCents(latest.deductible_cents) : "—"}
                          sub={latest.deductible_met_cents != null ? `${formatCents(latest.deductible_met_cents)} met` : null} />
                <MiniStat label="Coinsurance"
                          value={latest.coinsurance_pct != null ? `${latest.coinsurance_pct}%` : "—"} />
                <MiniStat label="OOP max"
                          value={latest.out_of_pocket_cents != null ? formatCents(latest.out_of_pocket_cents) : "—"} />
              </div>
            )}
            <p className="text-[11px] text-muted-foreground">
              {latest.payer_name ? `${latest.payer_name} · ` : ""}
              Last checked {formatDateTime(latest.checked_at)}
            </p>
            <p
              data-testid="patient-eligibility-disclaimer"
              className="text-[11px] italic text-muted-foreground"
            >
              Eligibility information is payer-reported and is not a guarantee of payment.
            </p>
          </div>
        )}
      </section>

      <EligibilityDialog
        open={open}
        patientId={patientId}
        onClose={() => setOpen(false)}
        onUpdated={() => load()}
      />
    </>
  );
}


function buildSubtitle(latest) {
  if (!latest) return null;
  const status = latest.effective_status || latest.status;
  if (status === "active") return latest.plan_name || "Benefits confirmed";
  if (status === "partial") return "Coverage active, some benefits missing";
  if (status === "inactive") return "Payer reports this policy is inactive";
  if (status === "rejected") return latest.rejection_reason || "Subscriber mismatch — verify member ID";
  if (status === "error") return "Payer connection errored — retry required";
  if (status === "expired") return `Stale for ${latest.service_date ? "DOS " + formatDate(latest.service_date) : "current date"} — re-verify`;
  if (status === "unknown") return "Payer response inconclusive";
  return null;
}


function MiniStat({ label, value, sub }) {
  return (
    <div className="rounded-sm border border-border bg-muted/30 p-1.5">
      <div className="text-[9px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="font-display text-sm tabular-nums">{value}</div>
      {sub && <div className="text-[9px] text-muted-foreground">{sub}</div>}
    </div>
  );
}
