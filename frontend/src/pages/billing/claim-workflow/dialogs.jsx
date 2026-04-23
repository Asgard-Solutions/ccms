import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "../../../components/ui/button";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { Textarea } from "../../../components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../../components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../../components/ui/select";
import { parseDollarsToCents } from "../../../utils/money";
import {
  OUTCOME_LABELS,
  SUBMISSION_METHOD_LABELS,
  createClaimSubmission,
  fetchSubmissionPayload,
  recordSubmissionOutcome,
} from "../useClaims";

const OUTCOME_ORDER = [
  "accepted", "rejected", "pending", "paid", "partially_paid", "denied",
];

/** "New submission" modal — creates a claim_submissions row with the
 *  scaffolded 837P preview + JSON payload stored against it. */
export function SubmissionDialog({ open, onOpenChange, claimId, onCreated }) {
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

/** Outcome-recording modal — updates an open claim_submission with
 *  the payer's verdict. Auto-transitions the parent claim through
 *  the canonical state machine server-side. */
export function OutcomeDialog({ sub, onOpenChange, claimId, onSaved }) {
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

/** Read-only payload viewer — switches between the JSON claim snapshot
 *  and the 837P wire preview stored against a submission. */
export function PayloadDialog({ sub, onOpenChange, claimId }) {
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
