import { useEffect, useState } from "react";
import { ShieldCheck, Clock3, AlertTriangle, XCircle, CheckCircle2, CircleDashed, History } from "lucide-react";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { formatDateTime } from "../../utils/time";
import { getEntityRaw } from "./api";

// ---------- Status meta (shared across registers) ----------

const TONE_CHIP = {
  success: "bg-primary/10 text-primary",
  warning: "bg-warning-soft text-warning",
  danger: "bg-destructive-soft text-destructive",
  neutral: "bg-muted text-muted-foreground",
  info: "bg-secondary text-foreground",
};

const TONE_DOT = {
  success: "bg-primary",
  warning: "bg-warning",
  danger: "bg-destructive",
  neutral: "bg-muted-foreground",
  info: "bg-foreground/40",
};

export const STATUS_META = {
  // Controls
  planned: { tone: "neutral", icon: CircleDashed, label: "Planned" },
  in_progress: { tone: "info", icon: Clock3, label: "In progress" },
  implemented: { tone: "success", icon: ShieldCheck, label: "Implemented" },
  needs_review: { tone: "warning", icon: AlertTriangle, label: "Needs review" },
  exception_approved: { tone: "warning", icon: AlertTriangle, label: "Exception approved" },
  retired: { tone: "neutral", icon: XCircle, label: "Retired" },
  // Risks
  open: { tone: "danger", icon: AlertTriangle, label: "Open" },
  mitigating: { tone: "warning", icon: Clock3, label: "Mitigating" },
  mitigated: { tone: "success", icon: CheckCircle2, label: "Mitigated" },
  accepted: { tone: "info", icon: ShieldCheck, label: "Accepted" },
  transferred: { tone: "info", icon: ShieldCheck, label: "Transferred" },
  closed: { tone: "neutral", icon: CheckCircle2, label: "Closed" },
  // Policies
  draft: { tone: "warning", icon: CircleDashed, label: "Draft" },
  approved: { tone: "success", icon: CheckCircle2, label: "Approved" },
  // Incidents
  triage: { tone: "danger", icon: AlertTriangle, label: "Triage" },
  investigating: { tone: "warning", icon: Clock3, label: "Investigating" },
  contained: { tone: "info", icon: ShieldCheck, label: "Contained" },
  eradicated: { tone: "info", icon: ShieldCheck, label: "Eradicated" },
  recovered: { tone: "success", icon: CheckCircle2, label: "Recovered" },
  // Vendors
  active: { tone: "success", icon: CheckCircle2, label: "Active" },
  under_review: { tone: "warning", icon: Clock3, label: "Under review" },
  terminated: { tone: "neutral", icon: XCircle, label: "Terminated" },
  // Access reviews
  scheduled: { tone: "info", icon: Clock3, label: "Scheduled" },
  complete: { tone: "success", icon: CheckCircle2, label: "Complete" },
  overdue: { tone: "danger", icon: AlertTriangle, label: "Overdue" },
};

export function StatusChip({ status, testid }) {
  const meta = STATUS_META[status] || { tone: "neutral", icon: CircleDashed, label: status || "—" };
  const Icon = meta.icon;
  return (
    <span
      data-testid={testid}
      className={`inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${TONE_CHIP[meta.tone]}`}
    >
      <Icon className="h-3 w-3" />
      <span className={`h-1.5 w-1.5 rounded-full ${TONE_DOT[meta.tone]}`} />
      {meta.label}
    </span>
  );
}

// ---------- Severity chip (incidents) ----------

const SEV_TONE = {
  low: "bg-muted text-muted-foreground",
  medium: "bg-warning-soft text-warning",
  high: "bg-destructive-soft text-destructive",
  critical: "bg-destructive text-destructive-foreground",
};

export function SeverityChip({ severity }) {
  return (
    <span
      data-testid={`severity-${severity}`}
      className={`inline-flex items-center rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${SEV_TONE[severity] || SEV_TONE.low}`}
    >
      {severity}
    </span>
  );
}

// ---------- Risk score chip ----------

export function ScoreChip({ score, testid }) {
  let tone = "neutral";
  if (score >= 15) tone = "danger";
  else if (score >= 9) tone = "warning";
  else if (score >= 4) tone = "info";
  else tone = "success";
  return (
    <span
      data-testid={testid}
      className={`inline-flex items-center rounded-sm px-2 py-0.5 font-mono text-[11px] font-semibold ${TONE_CHIP[tone]}`}
    >
      {score}
    </span>
  );
}

// ---------- History drawer ----------

export function HistoryDialog({ entityType, entityId, open, onClose }) {
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open || !entityId) return;
    setDoc(null);
    setError(null);
    getEntityRaw(entityType, entityId)
      .then(setDoc)
      .catch((e) => setError(e?.response?.data?.detail || "Failed to load history"));
  }, [open, entityType, entityId]);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="compliance-history-dialog" className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-display flex items-center gap-2">
            <History className="h-4 w-4" /> Activity history
          </DialogTitle>
          <DialogDescription>
            Mutation trail for this {entityType.replace("_", " ")}.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <div data-testid="history-error" className="rounded-sm border border-destructive-soft bg-destructive-soft p-3 text-sm text-destructive">
            {error}
          </div>
        )}
        {!doc && !error && (
          <div data-testid="history-loading" className="text-sm text-muted-foreground">Loading…</div>
        )}
        {doc && (
          <ul className="space-y-3 max-h-[60vh] overflow-y-auto">
            {(doc.history || []).slice().reverse().map((h, i) => (
              <li
                key={i}
                data-testid={`history-row-${i}`}
                className="rounded-sm border border-border bg-card px-3 py-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wider text-foreground">{h.action}</span>
                  <span className="text-[11px] text-muted-foreground">{formatDateTime(h.at)}</span>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {h.actor_email || h.actor_id || "system"}
                  {h.note ? ` · ${h.note}` : ""}
                </div>
              </li>
            ))}
            {!doc.history?.length && (
              <li className="text-sm text-muted-foreground">No history entries yet.</li>
            )}
          </ul>
        )}
        <DialogFooter>
          <Button data-testid="history-close-btn" variant="outline" onClick={onClose}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------- Empty state ----------

export function EmptyState({ label, testid }) {
  return (
    <div
      data-testid={testid || "empty-state"}
      className="rounded-sm border border-dashed border-border bg-card/50 px-6 py-10 text-center text-sm text-muted-foreground"
    >
      {label}
    </div>
  );
}

// ---------- Section header ----------

export function SectionHeader({ title, count, action, testid }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 data-testid={testid} className="font-display text-lg font-medium">
        {title}
        {typeof count === "number" && (
          <span className="ml-2 text-sm font-normal text-muted-foreground">({count})</span>
        )}
      </h2>
      {action}
    </div>
  );
}

// ---------- Date helpers ----------

export const todayIso = () => new Date().toISOString().slice(0, 10);

export const inDaysIso = (n) => {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
};

export const isOverdue = (iso) => {
  if (!iso) return false;
  return new Date(iso).getTime() < Date.now();
};
