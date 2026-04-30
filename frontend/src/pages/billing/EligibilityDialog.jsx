import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  AlertCircle,
  CheckCircle2,
  FileText,
  RefreshCw,
  ShieldCheck,
  Timer,
  XCircle,
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
  listEligibilityChecks,
  runEligibilityCheck,
} from "./useBillingAdmin";
import { formatCents } from "../../utils/money";
import { formatDate, formatDateTime } from "../../utils/time";
import { useReauth } from "../../components/ReauthGate";

/** Three-pane modal — "Check eligibility" across the policy.
 *  Tab 1: latest result summary (coverage, copay, deductible, plan).
 *  Tab 2: recent history (scoped to this policy, reverse chrono).
 *  Tab 3: 270/271 raw payloads (MFA-gated — shown once user reauth'd).
 */
export function EligibilityDialog({ open, policy, onClose }) {
  const [tab, setTab] = useState("result");
  const [latest, setLatest] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [payloadDetail, setPayloadDetail] = useState(null);
  const { requestReauth } = useReauth();

  const load = useCallback(async (options = {}) => {
    const { preserveLatest = false } = options;
    if (!policy?.id) return;
    setLoading(true);
    try {
      const rows = await listEligibilityChecks(policy.id);
      setHistory(rows);
      if (rows.length && !preserveLatest) {
        // Summary row is cheap; full result comes from the just-ran
        // response. History rows are summaries only (list endpoint
        // intentionally omits the wires).
        setLatest({
          result: {
            coverage_active: rows[0].coverage_active,
            plan_name: rows[0].plan_name,
            copay_cents: rows[0].copay_cents,
            deductible_cents: rows[0].deductible_cents,
            deductible_met_cents: rows[0].deductible_met_cents,
          },
          checked_at: rows[0].checked_at,
          sandbox: rows[0].sandbox,
          service_type_codes: rows[0].service_type_codes,
          id: rows[0].id,
        });
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not load history");
    } finally { setLoading(false); }
  }, [policy?.id]);

  useEffect(() => {
    if (open) { setTab("result"); load(); }
  }, [open, load]);

  async function onCheckNow() {
    setRunning(true);
    try {
      const fresh = await runEligibilityCheck(policy.id, {
        service_type_codes: ["30", "33", "98"],
      });
      setLatest(fresh);
      toast.success(
        fresh.result.coverage_active
          ? "Coverage active — benefits loaded"
          : "Coverage inactive — see result for details",
      );
      // Refresh history without overwriting our fresh full-shape result
      // (list endpoint intentionally omits coinsurance / OOP / wires).
      await load({ preserveLatest: true });
      setTab("result");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Eligibility check failed");
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
      toast.error(e?.response?.data?.detail || "Could not load payload");
    }
  }

  return (
    <Dialog open={!!open} onOpenChange={(v) => !v && onClose?.()}>
      <DialogContent
        data-testid="eligibility-dialog"
        className="max-w-3xl"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            Eligibility · {policy?.subscriber_name || "—"}
          </DialogTitle>
          <DialogDescription>
            X12 270/271 inquiry against member {policy?.member_id}.
          </DialogDescription>
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
      <div className="flex flex-col items-center gap-2 py-12 text-center text-sm text-muted-foreground">
        <ShieldCheck className="h-8 w-8 text-muted-foreground/60" />
        No eligibility check on file. Click "Check now" to run one.
      </div>
    );
  }
  const r = latest.result || {};
  const active = r.coverage_active;
  return (
    <div data-testid="eligibility-result-pane" className="space-y-3">
      <div
        className={`flex items-center gap-2 rounded-sm border p-3 ${
          active
            ? "border-success/40 bg-success-soft/40 text-success"
            : "border-destructive/40 bg-destructive/10 text-destructive"
        }`}
      >
        {active ? (
          <CheckCircle2 className="h-5 w-5" />
        ) : (
          <XCircle className="h-5 w-5" />
        )}
        <div className="flex-1">
          <div className="font-medium">
            {active ? "Coverage active" : "Coverage inactive"}
          </div>
          <div className="text-xs opacity-80">
            {r.plan_name || "Plan details pending"}
          </div>
        </div>
        {latest.sandbox && (
          <span
            data-testid="eligibility-sandbox-badge"
            className="rounded-sm bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"
          >
            Sandbox
          </span>
        )}
      </div>

      {active && (
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
      </div>

      {r.messages && r.messages.length > 0 && (
        <div className="rounded-sm border border-warning/30 bg-warning-soft/40 p-2 text-xs">
          <div className="mb-1 flex items-center gap-1 font-medium text-warning">
            <AlertCircle className="h-3.5 w-3.5" /> Payer messages
          </div>
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
          {r.coverage_active ? (
            <CheckCircle2 className="h-4 w-4 text-success" />
          ) : (
            <XCircle className="h-4 w-4 text-destructive" />
          )}
          <div className="flex-1 min-w-0">
            <div className="truncate">
              {r.plan_name || "Plan details pending"}
            </div>
            <div className="text-[11px] text-muted-foreground">
              {formatDateTime(r.checked_at)}
              {r.service_type_codes?.length
                ? ` · inquired ${r.service_type_codes.join(", ")}`
                : ""}
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
        Raw 270/271 wires require an MFA re-auth.
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
{detail.request_wire}
        </pre>
      </section>
      <section>
        <h3 className="mb-1 font-semibold uppercase tracking-wide text-muted-foreground">
          271 response
        </h3>
        <pre className="max-h-56 overflow-auto rounded-sm border border-border bg-muted/40 p-2 font-mono text-[11px] leading-relaxed">
{detail.response_wire}
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
