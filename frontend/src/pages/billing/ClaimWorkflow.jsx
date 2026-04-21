import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  FileDown,
  History,
  Send,
  UserCog,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { formatCents, parseDollarsToCents } from "../../utils/money";
import { formatDateTime } from "../../utils/time";
import {
  OUTCOME_LABELS,
  SUBMISSION_METHOD_LABELS,
  createClaimSubmission,
  fetchClaimTimeline,
  fetchSubmissionPayload,
  listClaimSubmissions,
  recordSubmissionOutcome,
  updateClaimAssignment,
} from "./useClaims";

const OUTCOME_ORDER = [
  "accepted", "rejected", "pending", "paid", "partially_paid", "denied",
];

const TIMELINE_KIND_META = {
  history: { label: "Status change", tone: "text-muted-foreground" },
  validation_run: { label: "Scrubber run", tone: "text-warning" },
  submission: { label: "Submission", tone: "text-primary" },
  submission_outcome: { label: "Outcome", tone: "text-success" },
};

/** Phase 4 workflow panel — renders submissions, outcomes, timeline,
 *  and assignment for a single claim. Parent supplies `claim` and
 *  a `onChanged` callback to trigger an outer refresh. */
export default function ClaimWorkflow({ claim, onChanged }) {
  const [submissions, setSubmissions] = useState([]);
  const [timeline, setTimeline] = useState({ entries: [] });
  const [loading, setLoading] = useState(true);
  const [submitOpen, setSubmitOpen] = useState(false);
  const [outcomeSub, setOutcomeSub] = useState(null);
  const [payloadSub, setPayloadSub] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [subs, tl] = await Promise.all([
        listClaimSubmissions(claim.id),
        fetchClaimTimeline(claim.id),
      ]);
      setSubmissions(subs);
      setTimeline(tl);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not load workflow");
    } finally { setLoading(false); }
  }, [claim.id]);

  useEffect(() => { refresh(); }, [refresh]);

  const canSubmit = claim.status === "ready";
  const activeSub = useMemo(
    () => submissions.find((s) => !s.outcome) || null,
    [submissions],
  );

  return (
    <>
      <section
        data-testid="claim-workflow-card"
        className="rounded-sm border border-border bg-card p-6"
      >
        <header className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-xl font-medium tracking-tight">
            Workflow
          </h2>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm" variant="outline"
              onClick={() => setSubmitOpen(true)}
              disabled={!canSubmit}
              data-testid="workflow-new-submission-btn"
              className="rounded-sm"
            >
              <Send className="mr-1 h-4 w-4" /> New submission
            </Button>
            {activeSub && (
              <Button
                size="sm"
                onClick={() => setOutcomeSub(activeSub)}
                data-testid="workflow-record-outcome-btn"
                className="rounded-sm"
              >
                <CheckCircle2 className="mr-1 h-4 w-4" /> Record outcome
              </Button>
            )}
          </div>
        </header>

        <AssignmentRow claim={claim} onSaved={onChanged} />

        <SubmissionsTable
          loading={loading}
          submissions={submissions}
          onViewPayload={setPayloadSub}
          onRecordOutcome={setOutcomeSub}
        />
      </section>

      <TimelineSection loading={loading} timeline={timeline} />

      <SubmissionDialog
        open={submitOpen}
        onOpenChange={setSubmitOpen}
        claimId={claim.id}
        onCreated={async () => {
          await refresh();
          onChanged?.();
        }}
      />
      <OutcomeDialog
        sub={outcomeSub}
        onOpenChange={(v) => !v && setOutcomeSub(null)}
        claimId={claim.id}
        onSaved={async () => {
          setOutcomeSub(null);
          await refresh();
          onChanged?.();
        }}
      />
      <PayloadDialog
        sub={payloadSub}
        onOpenChange={(v) => !v && setPayloadSub(null)}
        claimId={claim.id}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
function AssignmentRow({ claim, onSaved }) {
  const [value, setValue] = useState(claim.assigned_to || "");
  const [saving, setSaving] = useState(false);

  useEffect(() => { setValue(claim.assigned_to || ""); }, [claim.assigned_to]);

  async function save() {
    setSaving(true);
    try {
      await updateClaimAssignment(claim.id, value.trim() || null);
      toast.success(value ? "Assignment saved" : "Assignment cleared");
      onSaved?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not update assignment");
    } finally { setSaving(false); }
  }

  return (
    <div
      data-testid="claim-assignment-row"
      className="mb-4 flex flex-wrap items-end gap-3 rounded-sm bg-muted/40 p-3"
    >
      <div className="flex items-center gap-2">
        <UserCog className="h-4 w-4 text-muted-foreground" />
        <Label htmlFor="cw-assignee" className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
          Assignee
        </Label>
      </div>
      <Input
        id="cw-assignee"
        data-testid="claim-assignee-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="user id (leave blank for unassigned)"
        className="min-w-[18rem] flex-1"
      />
      <Button
        size="sm"
        onClick={save}
        disabled={saving || (value || "") === (claim.assigned_to || "")}
        data-testid="claim-assignee-save"
        className="rounded-sm"
      >
        {saving ? "Saving…" : "Save"}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
function SubmissionsTable({ loading, submissions, onViewPayload, onRecordOutcome }) {
  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading submissions…</p>;
  }
  if (submissions.length === 0) {
    return (
      <p data-testid="workflow-no-submissions" className="text-sm text-muted-foreground">
        No submissions yet.
      </p>
    );
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
        <tr>
          <th className="py-1 pr-2">Submitted</th>
          <th className="py-1 pr-2">Method</th>
          <th className="py-1 pr-2">Ref</th>
          <th className="py-1 pr-2">Outcome</th>
          <th className="py-1 pr-2 text-right">Paid</th>
          <th className="py-1" />
        </tr>
      </thead>
      <tbody>
        {submissions.map((s) => (
          <tr
            key={s.id}
            data-testid={`submission-row-${s.id}`}
            className="border-t border-border"
          >
            <td className="py-1.5 pr-2 text-muted-foreground">
              {formatDateTime(s.submitted_at)}
            </td>
            <td className="py-1.5 pr-2">
              {SUBMISSION_METHOD_LABELS[s.method] || s.method}
            </td>
            <td className="py-1.5 pr-2 text-muted-foreground">
              {s.external_reference || "—"}
            </td>
            <td className="py-1.5 pr-2">
              {s.outcome ? (
                <span className="rounded-sm bg-muted px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide">
                  {OUTCOME_LABELS[s.outcome] || s.outcome}
                </span>
              ) : (
                <span className="text-xs text-muted-foreground">Awaiting outcome</span>
              )}
            </td>
            <td className="py-1.5 pr-2 text-right tabular-nums">
              {s.paid_cents ? formatCents(s.paid_cents) : "—"}
            </td>
            <td className="py-1.5 text-right">
              <Button
                variant="ghost" size="sm"
                onClick={() => onViewPayload(s)}
                data-testid={`submission-payload-${s.id}`}
              >
                <FileDown className="mr-1 h-3.5 w-3.5" /> Payload
              </Button>
              {!s.outcome && (
                <Button
                  variant="ghost" size="sm"
                  onClick={() => onRecordOutcome(s)}
                  data-testid={`submission-outcome-btn-${s.id}`}
                >
                  Record
                </Button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
function TimelineSection({ loading, timeline }) {
  const [expanded, setExpanded] = useState(true);
  const entries = timeline?.entries || [];

  return (
    <section
      data-testid="claim-timeline-card"
      className="rounded-sm border border-border bg-card p-6"
    >
      <button
        type="button"
        className="mb-3 flex w-full items-center justify-between text-left"
        onClick={() => setExpanded((v) => !v)}
        data-testid="timeline-toggle"
      >
        <div className="flex items-center gap-2">
          <History className="h-4 w-4 text-muted-foreground" />
          <h2 className="font-display text-lg font-medium tracking-tight">
            Status timeline
          </h2>
          <span className="rounded-sm bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
            {entries.length}
          </span>
        </div>
        {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
      </button>
      {expanded && (
        loading ? (
          <p className="text-sm text-muted-foreground">Loading timeline…</p>
        ) : entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">No timeline entries yet.</p>
        ) : (
          <ol className="space-y-2">
            {entries.map((e, i) => {
              const meta = TIMELINE_KIND_META[e.kind] || { label: e.kind, tone: "text-foreground" };
              return (
                <li
                  key={i}
                  data-testid={`timeline-entry-${i}`}
                  className="flex items-start gap-3 rounded-sm border border-border bg-muted/30 p-3"
                >
                  <ClipboardList className={`mt-0.5 h-4 w-4 ${meta.tone}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className={`font-semibold uppercase tracking-wide ${meta.tone}`}>
                        {meta.label}
                      </span>
                      <span className="text-muted-foreground">
                        {formatDateTime(e.at)}
                      </span>
                      {e.by && (
                        <span className="text-muted-foreground">
                          · by {e.by.slice(0, 8)}
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-sm">
                      <span className="font-medium">{e.action}</span>
                      {e.from_status && e.to_status && (
                        <span className="ml-2 text-xs text-muted-foreground">
                          {e.from_status} → {e.to_status}
                        </span>
                      )}
                    </div>
                    {e.metadata && Object.keys(e.metadata).length > 0 && (
                      <pre className="mt-1 overflow-x-auto rounded-sm bg-background p-2 text-[11px] text-muted-foreground">
{JSON.stringify(e.metadata, null, 2)}
                      </pre>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
function SubmissionDialog({ open, onOpenChange, claimId, onCreated }) {
  const [method, setMethod] = useState("manual_portal");
  const [externalRef, setExternalRef] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setMethod("manual_portal");
      setExternalRef("");
      setNotes("");
    }
  }, [open]);

  async function onSubmit() {
    setSaving(true);
    try {
      await createClaimSubmission(claimId, {
        method,
        external_reference: externalRef.trim() || null,
        notes: notes.trim() || null,
      });
      toast.success("Submission recorded — claim is now submitted");
      onOpenChange(false);
      await onCreated?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not submit claim");
    } finally { setSaving(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="submission-dialog"
        className="rounded-sm sm:max-w-lg"
      >
        <DialogHeader>
          <DialogTitle className="font-display">New submission</DialogTitle>
          <DialogDescription>
            Record a manual submission attempt. The scaffolded JSON and
            837P preview are stored against this submission for
            downstream review.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label htmlFor="sub-method">Method</Label>
            <Select value={method} onValueChange={setMethod}>
              <SelectTrigger id="sub-method" data-testid="sub-method">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(SUBMISSION_METHOD_LABELS).map(([k, v]) => (
                  <SelectItem key={k} value={k}>{v}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="sub-ref">External reference</Label>
            <Input
              id="sub-ref"
              data-testid="sub-ref"
              placeholder="portal confirmation # or batch id"
              value={externalRef}
              onChange={(e) => setExternalRef(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="sub-notes">Notes</Label>
            <Textarea
              id="sub-notes" rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Internal notes — optional"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            onClick={onSubmit} disabled={saving}
            data-testid="sub-save" className="rounded-sm"
          >
            {saving ? "Submitting…" : "Record submission"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
function OutcomeDialog({ sub, onOpenChange, claimId, onSaved }) {
  const open = !!sub;
  const [outcome, setOutcome] = useState("accepted");
  const [payerRef, setPayerRef] = useState("");
  const [denialCode, setDenialCode] = useState("");
  const [paidStr, setPaidStr] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setOutcome("accepted");
      setPayerRef("");
      setDenialCode("");
      setPaidStr("");
      setNotes("");
    }
  }, [open, sub?.id]);

  const showPaid = outcome === "paid" || outcome === "partially_paid";
  const showDenial = outcome === "denied" || outcome === "rejected";

  async function onSubmit() {
    setSaving(true);
    try {
      await recordSubmissionOutcome(claimId, sub.id, {
        outcome,
        payer_reference: payerRef.trim() || null,
        denial_code: showDenial ? (denialCode.trim() || null) : null,
        paid_cents: showPaid ? parseDollarsToCents(paidStr) : null,
        notes: notes.trim() || null,
      });
      toast.success(`Outcome recorded — ${OUTCOME_LABELS[outcome]}`);
      await onSaved?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not record outcome");
    } finally { setSaving(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="outcome-dialog"
        className="rounded-sm sm:max-w-lg"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Record outcome</DialogTitle>
          <DialogDescription>
            Updates this submission and auto-transitions the claim
            status through the canonical state machine.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label htmlFor="oc-kind">Outcome</Label>
            <Select value={outcome} onValueChange={setOutcome}>
              <SelectTrigger id="oc-kind" data-testid="oc-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {OUTCOME_ORDER.map((k) => (
                  <SelectItem key={k} value={k}>{OUTCOME_LABELS[k]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="oc-ref">Payer reference</Label>
            <Input
              id="oc-ref" data-testid="oc-ref"
              value={payerRef}
              onChange={(e) => setPayerRef(e.target.value)}
              placeholder="e.g. ICN 20261234567890"
            />
          </div>
          {showDenial && (
            <div>
              <Label htmlFor="oc-denial">Denial code</Label>
              <Input
                id="oc-denial" data-testid="oc-denial"
                value={denialCode}
                onChange={(e) => setDenialCode(e.target.value)}
                placeholder="e.g. CO-97"
              />
            </div>
          )}
          {showPaid && (
            <div>
              <Label htmlFor="oc-paid">Amount paid</Label>
              <Input
                id="oc-paid" data-testid="oc-paid"
                value={paidStr}
                onChange={(e) => setPaidStr(e.target.value)}
                placeholder="0.00"
              />
            </div>
          )}
          <div>
            <Label htmlFor="oc-notes">Notes</Label>
            <Textarea
              id="oc-notes" rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            onClick={onSubmit} disabled={saving}
            data-testid="oc-save" className="rounded-sm"
          >
            {saving ? "Saving…" : "Save outcome"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
function PayloadDialog({ sub, onOpenChange, claimId }) {
  const open = !!sub;
  const [payload, setPayload] = useState(null);
  const [tab, setTab] = useState("json");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!open) { setPayload(null); return; }
      try {
        const data = await fetchSubmissionPayload(claimId, sub.id);
        if (!cancelled) setPayload(data);
      } catch (e) {
        toast.error(e?.response?.data?.detail || "Could not load payload");
      }
    }
    load();
    return () => { cancelled = true; };
  }, [open, sub?.id, claimId]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="payload-dialog"
        className="max-h-[85vh] overflow-y-auto rounded-sm sm:max-w-3xl"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Submission payload</DialogTitle>
          <DialogDescription>
            {sub?.method && SUBMISSION_METHOD_LABELS[sub.method]} ·
            {" "}{sub && sub.payload_size_bytes} bytes
          </DialogDescription>
        </DialogHeader>
        <div className="mb-3 flex gap-2">
          <Button
            size="sm"
            variant={tab === "json" ? "default" : "outline"}
            onClick={() => setTab("json")}
            data-testid="payload-tab-json"
          >
            JSON
          </Button>
          <Button
            size="sm"
            variant={tab === "x12" ? "default" : "outline"}
            onClick={() => setTab("x12")}
            data-testid="payload-tab-x12"
          >
            837P preview
          </Button>
        </div>
        {!payload ? (
          <p className="text-sm text-muted-foreground">Loading payload…</p>
        ) : tab === "json" ? (
          <pre
            data-testid="payload-json"
            className="overflow-x-auto rounded-sm bg-muted p-3 text-[11px]"
          >
{JSON.stringify(payload.payload_json, null, 2)}
          </pre>
        ) : (
          <pre
            data-testid="payload-x12"
            className="overflow-x-auto rounded-sm bg-muted p-3 text-[11px]"
          >
{payload.payload_x12}
          </pre>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
