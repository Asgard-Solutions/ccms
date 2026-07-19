import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ExternalLink, Sparkles, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../../../components/ui/button";
import {
  fetchBillingFailures,
  dismissBillingFailure,
  runScheduleNow,
} from "./api";
import { formatDateTime } from "../../../utils/time";

export default function BillingFailuresPanel({ limit = 10 }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState({});

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchBillingFailures({ limit });
      setRows(data);
    } catch (_) {
      // Permission-restricted or no Helcim — fail soft on dashboard.
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => { load(); }, [load]);

  const onRetry = async (row) => {
    if (!row.schedule_id) {
      toast.error("Underlying schedule could not be resolved.");
      return;
    }
    setBusy((b) => ({ ...b, [row.id]: "retry" }));
    try {
      const res = await runScheduleNow(row.schedule_id);
      if (res.outcome === "success") {
        toast.success(`Retry succeeded — charged via Helcim (txn ${res.transaction_id || "n/a"}).`);
        await dismissBillingFailure(row.id);
      } else {
        toast.error(`Retry ${res.outcome}: ${res.error || ""}`);
      }
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Retry failed.");
    } finally {
      setBusy((b) => ({ ...b, [row.id]: null }));
    }
  };

  const onDismiss = async (row) => {
    setBusy((b) => ({ ...b, [row.id]: "dismiss" }));
    try {
      await dismissBillingFailure(row.id);
      toast.success("Notification dismissed.");
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to dismiss.");
    } finally {
      setBusy((b) => ({ ...b, [row.id]: null }));
    }
  };

  if (loading) {
    return (
      <section data-testid="billing-failures-loading"
               className="rounded-sm border border-border bg-card p-4 text-xs text-muted-foreground">
        Loading billing failures…
      </section>
    );
  }
  if (rows.length === 0) {
    // Hide entirely on the happy path — don't waste dashboard real estate.
    return null;
  }

  return (
    <section
      data-testid="billing-failures-panel"
      className="rounded-sm border border-destructive/40 bg-destructive-soft/40 p-5 space-y-3"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-display text-base font-medium flex items-center gap-2 text-destructive">
          <AlertTriangle className="h-4 w-4" />
          Billing auto-pay failures
          <span className="ml-1 text-xs font-normal opacity-80">({rows.length})</span>
        </h2>
      </div>
      <p className="text-xs text-foreground/80">
        These payment schedules hit the retry limit and need attention. Update the card on file
        or contact the patient, then click <span className="font-medium">Retry now</span> to
        resume the charge.
      </p>
      <ul className="space-y-2">
        {rows.map((r) => (
          <li
            key={r.id}
            data-testid={`billing-failure-row-${r.id}`}
            className="rounded-sm border border-destructive/30 bg-card px-3 py-2.5"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-foreground">
                  {r.title || "Billing failure"}
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{r.body}</div>
                <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
                  <span>{formatDateTime(r.created_at)}</span>
                  {r.patient_name && (
                    <Link
                      to={`/billing/patients/${r.patient_id}/ledger`}
                      data-testid={`billing-failure-patient-${r.id}`}
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                    >
                      {r.patient_name} <ExternalLink className="h-3 w-3" />
                    </Link>
                  )}
                  {r.schedule_label && (
                    <span data-testid={`billing-failure-schedule-${r.id}`} className="rounded-sm bg-muted px-1.5 py-0.5 uppercase tracking-wider">
                      {r.schedule_label}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-shrink-0 items-center gap-1">
                {r.schedule_id && (
                  <Button
                    data-testid={`billing-failure-retry-${r.id}`}
                    size="sm" variant="outline"
                    disabled={busy[r.id] === "retry"}
                    onClick={() => onRetry(r)}
                    className="gap-1"
                  >
                    <Sparkles className="h-3.5 w-3.5" />
                    {busy[r.id] === "retry" ? "Retrying…" : "Retry now"}
                  </Button>
                )}
                <Button
                  data-testid={`billing-failure-dismiss-${r.id}`}
                  size="sm" variant="ghost"
                  disabled={busy[r.id] === "dismiss"}
                  onClick={() => onDismiss(r)}
                  className="text-destructive"
                  title="Dismiss"
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
