import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  FileText,
  RefreshCw,
  ShieldCheck,
  Timer,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  fetchEligibilityCheckDetail,
  fetchPatientEligibilityHistory,
  listEligibilityChecks,
  runAppointmentEligibilityCheck,
  runEligibilityCheck,
  runPatientEligibilityCheck,
} from "./useBillingAdmin";
import { formatCents } from "../../utils/money";
import { formatDate, formatDateTime } from "../../utils/time";
import { useReauth } from "../../components/ReauthGate";
import {
  EligibilityStatusBanner,
  EligibilityStatusChip,
} from "./eligibility/statusMeta";


const DISCLAIMER =
  "Eligibility information is payer-reported and is not a guarantee of payment.";


/** Three-pane "Eligibility" dialog. Works against a policy,
 *  a patient, or an appointment depending on which anchor id is
 *  provided. Exactly one of `policy`, `patientId`, or
 *  `appointmentId` should be set per render. */
export function EligibilityDialog({
  open,
  policy,
  patientId,
  appointmentId,
  onClose,
  onUpdated,
}) {
  const [tab, setTab] = useState("result");
  const [latest, setLatest] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [payloadDetail, setPayloadDetail] = useState(null);
  const { requestReauth } = useReauth();

  const anchorPatientId = patientId || policy?.patient_id || null;

  const load = useCallback(async (options = {}) => {
    const { preserveLatest = false } = options;
    if (!open) return;
    setLoading(true);
    try {
      let rows = [];
      if (policy?.id) {
        rows = await listEligibilityChecks(policy.id);
      } else if (anchorPatientId) {
        rows = await fetchPatientEligibilityHistory(anchorPatientId);
      }
      // When anchored to an appointment we still show the patient's
      // history so the operator has context before running a new check.
      setHistory(rows);
      if (rows.length && !preserveLatest) {
        const top = rows[0];
        setLatest({
          id: top.id,
          status: top.effective_status || top.status,
          checked_at: top.checked_at,
          sandbox: top.sandbox,
          service_type_codes: top.service_type_codes,
          service_date: top.service_date,
          appointment_id: top.appointment_id,
          result: {
            plan_name: top.plan_name,
            payer_name: top.payer_name,
            copay_cents: top.copay_cents,
            deductible_cents: top.deductible_cents,
            deductible_met_cents: top.deductible_met_cents,
            coinsurance_pct: top.coinsurance_pct,
            out_of_pocket_cents: top.out_of_pocket_cents,
            rejection_reason: top.rejection_reason,
          },
        });
      } else if (!rows.length && !preserveLatest) {
        setLatest(null);
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not load history");
    } finally { setLoading(false); }
  }, [open, policy?.id, anchorPatientId]);

  useEffect(() => {
    if (open) { setTab("result"); setPayloadDetail(null); load(); }
  }, [open, load]);

  async function onCheckNow() {
    setRunning(true);
    try {
      let fresh;
      const body = { service_type_codes: ["30", "33", "98"] };
      if (appointmentId) {
        fresh = await runAppointmentEligibilityCheck(appointmentId, body);
      } else if (policy?.id) {
        fresh = await runEligibilityCheck(policy.id, body);
      } else if (anchorPatientId) {
        fresh = await runPatientEligibilityCheck(anchorPatientId, body);
      } else {
        toast.error("No policy, patient, or appointment anchor provided");
        return;
      }
      setLatest({
        ...fresh,
        status: fresh.status,
      });
      const toastText = (
        fresh.status === "active"  ? "Coverage active — benefits loaded" :
        fresh.status === "partial" ? "Coverage active — some benefits missing" :
        fresh.status === "inactive"? "Coverage inactive — see details" :
        fresh.status === "rejected"? "Rejected by payer — verify member info" :
        fresh.status === "error"   ? "Eligibility inquiry errored" :
                                     "Eligibility check complete"
      );
      (fresh.status === "active" || fresh.status === "partial"
        ? toast.success : toast.warning)(toastText);
      await load({ preserveLatest: true });
      setTab("result");
      onUpdated?.(fresh);
    } catch (e) {
      toast.error(
        e?.response?.data?.detail
          || "Eligibility check failed — please try again.",
      );
    } finally { setRunning(false); }
  }

  async function onViewPayload(checkId) {
    try {
      let data = await fetchEligibilityCheckDetail(checkId)
        .catch(async (e) => {
          if (e?.response?.status === 401) {
            const ok = await requestReauth({
              reason: "Viewing eligibility 270/271 payload",
            });
            if (!ok) return null;
            return fetchEligibilityCheckDetail(checkId);
          }
          throw e;
        });
      if (!data) return;
      setPayloadDetail(data);
      setTab("payload");
    } catch (e) {
      toast.error(
        e?.response?.data?.detail
          || "Could not load payload (admin / billing access required)",
      );
    }
  }

  const anchorTitle = (
    policy ? `Eligibility · ${policy.subscriber_name || "Policy"}` :
    appointmentId ? "Eligibility · Appointment" :
    "Eligibility"
  );
  const anchorDescription = (
    policy ? `X12 270/271 inquiry against member ${policy.member_id}.` :
    "X12 270/271 inquiry on file for this patient."
  );

  return (
    <Dialog open={!!open} onOpenChange={(v) => !v && onClose?.()}>
      <DialogContent
        data-testid="eligibility-dialog"
        className="max-w-3xl"
      >
        <DialogHeader>
          <DialogTitle className="font-display">{anchorTitle}</DialogTitle>
          <DialogDescription>{anchorDescription}</DialogDescription>
        </DialogHeader>

        <div className="flex items-center justify-between border-b border-border pb-2">
          <div className="flex gap-1">
            {[
              ["result", "Latest result"],
              ["history", `History (${history.length})`],
              ["payload", "270 / 271"],
            ].map(([k, label]) => (
              <button
                key={k} type="button"
                onClick={() => setTab(k)}
                data-testid={`eligibility-tab-${k}`}
                className={`rounded-sm px-3 py-1 text-sm transition ${
                  tab === k
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <Button
            size="sm"
            onClick={onCheckNow}
            disabled={running}
            data-testid="eligibility-run-btn"
            className="rounded-sm"
          >
            <RefreshCw className={`mr-1 h-3.5 w-3.5 ${running ? "animate-spin" : ""}`} />
            {running ? "Checking…" : "Check now"}
          </Button>
        </div>

        <div className="min-h-[16rem] py-3">
          {tab === "result" && (
            <ResultPane latest={latest} loading={loading}
                       onViewPayload={onViewPayload} />
          )}
          {tab === "history" && (
            <HistoryPane rows={history} loading={loading}
                         onViewPayload={onViewPayload} />
          )}
          {tab === "payload" && (
            <PayloadPane detail={payloadDetail} latestId={latest?.id}
                         onLoad={onViewPayload} />
          )}
        </div>

        <p
          className="rounded-sm bg-muted/40 px-2 py-1 text-[11px] italic text-muted-foreground"
          data-testid="eligibility-disclaimer"
        >
          {DISCLAIMER}
        </p>

        <DialogFooter>
          <Button
            variant="outline" size="sm"
            onClick={onClose} data-testid="eligibility-close-btn"
            className="rounded-sm"
          >
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


function ResultPane({ latest, loading, onViewPayload }) {
  if (loading && !latest) return <Skeleton className="h-40 w-full" />;
  if (!latest) {
    return (
      <div
        data-testid="eligibility-empty-state"
        className="flex flex-col items-center gap-2 py-12 text-center text-sm text-muted-foreground"
      >
        <ShieldCheck className="h-8 w-8 text-muted-foreground/60" />
        Eligibility has not been checked. Click "Check now" to verify.
      </div>
    );
  }
  const r = latest.result || {};
  const status = latest.status || "unknown";
  const subtitle = (
    status === "active"   ? (r.plan_name || "Benefits loaded") :
    status === "partial"  ? (r.plan_name || "Some benefits missing") :
    status === "inactive" ? "Coverage is terminated for this member" :
    status === "rejected" ? (r.rejection_reason || "Subscriber / payer mismatch") :
    status === "error"    ? "Payer connection failed — retry required" :
    status === "expired"  ? "Stored check is stale for this date of service" :
    status === "unknown"  ? "Payer response inconclusive" :
                            "Eligibility not yet checked"
  );
  const showBenefits = (status === "active" || status === "partial");
  return (
    <div data-testid="eligibility-result-pane" className="space-y-3">
      <EligibilityStatusBanner
        status={status}
        subtitle={subtitle}
        sandbox={latest.sandbox}
      />

      {showBenefits && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label="Copay"
                    value={r.copay_cents != null ? formatCents(r.copay_cents) : "—"}
                    testid="eligibility-copay" />
          <StatCard label="Deductible"
                    value={r.deductible_cents != null ? formatCents(r.deductible_cents) : "—"}
                    sub={r.deductible_met_cents != null
                      ? `${formatCents(r.deductible_met_cents)} met`
                      : null}
                    testid="eligibility-deductible" />
          <StatCard label="Coinsurance"
                    value={r.coinsurance_pct != null ? `${r.coinsurance_pct}%` : "—"}
                    testid="eligibility-coinsurance" />
          <StatCard label="OOP max"
                    value={r.out_of_pocket_cents != null ? formatCents(r.out_of_pocket_cents) : "—"}
                    testid="eligibility-oop" />
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 text-xs">
        <MetaField label="Payer" value={r.payer_name} />
        <MetaField label="Plan" value={r.plan_name} />
        <MetaField label="Effective" value={r.effective_date ? formatDate(r.effective_date) : null} />
        <MetaField label="Terminates" value={r.termination_date ? formatDate(r.termination_date) : null} />
        <MetaField label="Subscriber" value={r.subscriber_name} />
        <MetaField label="Member ID" value={r.member_id} />
        <MetaField label="Service date" value={latest.service_date ? formatDate(latest.service_date) : null} />
        <MetaField label="Appointment" value={latest.appointment_id ? "Linked" : "—"} />
      </div>

      {r.messages && r.messages.length > 0 && (
        <div
          data-testid="eligibility-payer-messages"
          className="rounded-sm border border-warning/30 bg-warning-soft/40 p-2 text-xs"
        >
          <div className="mb-1 font-medium text-warning">Payer messages</div>
          <ul className="list-disc pl-4 text-foreground/80">
            {r.messages.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </div>
      )}

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <Timer className="h-3 w-3" />
          Checked {formatDateTime(latest.checked_at)}
        </span>
        {latest.id && (
          <Button
            variant="ghost" size="sm"
            onClick={() => onViewPayload(latest.id)}
            data-testid="eligibility-view-payload-btn"
            className="rounded-sm text-xs"
          >
            <FileText className="mr-1 h-3 w-3" /> View 270 / 271
          </Button>
        )}
      </div>
    </div>
  );
}


function HistoryPane({ rows, loading, onViewPayload }) {
  if (loading) return <Skeleton className="h-40 w-full" />;
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">No history yet.</p>;
  }
  return (
    <ul className="divide-y divide-border text-sm" data-testid="eligibility-history-list">
      {rows.map((r) => (
        <li key={r.id} className="flex items-center gap-2 py-2">
          <EligibilityStatusChip status={r.effective_status || r.status} />
          <div className="flex-1 min-w-0">
            <div className="truncate">
              {r.plan_name || r.payer_name || "Plan details pending"}
            </div>
            <div className="text-[11px] text-muted-foreground">
              {formatDateTime(r.checked_at)}
              {r.service_date ? ` · DOS ${formatDate(r.service_date)}` : ""}
              {r.appointment_id ? " · appt-linked" : ""}
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs">
            {r.copay_cents != null && (
              <span className="tabular-nums">copay {formatCents(r.copay_cents)}</span>
            )}
            {r.deductible_cents != null && (
              <span className="tabular-nums">ded {formatCents(r.deductible_cents)}</span>
            )}
            <Button
              variant="ghost" size="sm"
              onClick={() => onViewPayload(r.id)}
              data-testid={`eligibility-history-payload-${r.id}`}
              className="rounded-sm text-xs"
            >
              <FileText className="mr-1 h-3 w-3" /> Payload
            </Button>
          </div>
        </li>
      ))}
    </ul>
  );
}


function PayloadPane({ detail, latestId, onLoad }) {
  if (!detail && latestId) {
    return (
      <div className="flex flex-col items-center gap-3 py-10 text-center text-sm text-muted-foreground">
        <FileText className="h-8 w-8 text-muted-foreground/60" />
        Raw 270/271 wires require an MFA re-auth. Payload visibility is
        limited to billing and admin roles.
        <Button
          size="sm"
          onClick={() => onLoad(latestId)}
          data-testid="eligibility-load-payload-btn"
          className="rounded-sm"
        >
          Load 270 / 271
        </Button>
      </div>
    );
  }
  if (!detail) {
    return <p className="text-sm text-muted-foreground">Run a check first, then view the payload.</p>;
  }
  return (
    <div className="space-y-3 text-xs" data-testid="eligibility-payload-pane">
      <section>
        <h3 className="mb-1 font-semibold uppercase tracking-wide text-muted-foreground">
          270 request
        </h3>
        <pre className="max-h-56 overflow-auto rounded-sm border border-border bg-muted/40 p-2 font-mono text-[11px] leading-relaxed">
{detail.request_wire || "—"}
        </pre>
      </section>
      <section>
        <h3 className="mb-1 font-semibold uppercase tracking-wide text-muted-foreground">
          271 response
        </h3>
        <pre className="max-h-56 overflow-auto rounded-sm border border-border bg-muted/40 p-2 font-mono text-[11px] leading-relaxed">
{detail.response_wire || "—"}
        </pre>
      </section>
    </div>
  );
}


function StatCard({ label, value, sub, testid }) {
  return (
    <div
      data-testid={testid}
      className="rounded-sm border border-border bg-muted/30 p-2"
    >
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="font-display text-lg tabular-nums">{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  );
}


function MetaField({ label, value }) {
  return (
    <div className="rounded-sm border border-border bg-background p-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="text-sm">{value || "—"}</div>
    </div>
  );
}
