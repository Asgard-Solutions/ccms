import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, Send } from "lucide-react";
import { Button } from "../../components/ui/button";
import {
  fetchClaimTimeline,
  listClaimSubmissions,
} from "./useClaims";
import { AssignmentRow } from "./claim-workflow/AssignmentRow";
import { FollowupRow } from "./claim-workflow/FollowupRow";
import { SubmissionsTable } from "./claim-workflow/SubmissionsTable";
import { TimelineSection } from "./claim-workflow/TimelineSection";
import {
  OutcomeDialog,
  PayloadDialog,
  SubmissionDialog,
} from "./claim-workflow/dialogs";

/** Phase 4 workflow panel — renders submissions, outcomes, timeline,
 *  and assignment for a single claim. Parent supplies `claim` and
 *  a `onChanged` callback to trigger an outer refresh. */
export default function ClaimWorkflow({ claim, refs = {}, onChanged }) {
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

        <AssignmentRow claim={claim} assignee={refs.assignee} onSaved={onChanged} />
        <FollowupRow claim={claim} onSaved={onChanged} />

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
