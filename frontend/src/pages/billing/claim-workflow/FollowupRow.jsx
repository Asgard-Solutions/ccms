import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Flag } from "lucide-react";
import { Button } from "../../../components/ui/button";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { formatDateTime } from "../../../utils/time";
import {
  clearClaimFollowupFlag,
  flagClaimForFollowup,
} from "../useClaims";

/** Follow-up flag row — sits above the submissions table and lets
 *  any biller flag the claim for follow-up with a short reason.
 *  When set, the queue filters surface it under "Needs follow-up". */
export function FollowupRow({ claim, onSaved }) {
  const [saving, setSaving] = useState(false);
  const [reason, setReason] = useState(claim.followup_reason || "");

  useEffect(() => {
    setReason(claim.followup_reason || "");
  }, [claim.followup_reason]);

  async function flag() {
    if (!reason.trim()) {
      toast.error("Give a short reason so the queue surfaces this correctly.");
      return;
    }
    setSaving(true);
    try {
      await flagClaimForFollowup(claim.id, { reason: reason.trim() });
      toast.success("Flagged for follow-up");
      onSaved?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not flag claim");
    } finally { setSaving(false); }
  }

  async function clear() {
    setSaving(true);
    try {
      await clearClaimFollowupFlag(claim.id);
      toast.success("Follow-up flag cleared");
      onSaved?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not clear flag");
    } finally { setSaving(false); }
  }

  const flagged = !!claim.followup_flag;

  return (
    <div
      data-testid="claim-followup-row"
      className={`mb-4 flex flex-wrap items-end gap-3 rounded-sm p-3 ${flagged ? "bg-warning-soft/60" : "bg-muted/40"}`}
    >
      <div className="flex items-center gap-2">
        <Flag className={`h-4 w-4 ${flagged ? "text-warning" : "text-muted-foreground"}`} />
        <Label htmlFor="cw-followup" className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
          Follow-up
        </Label>
      </div>
      {flagged ? (
        <>
          <div
            data-testid="claim-followup-status"
            className="flex flex-1 flex-col gap-0.5 text-sm"
          >
            <span className="font-medium">
              Flagged: {claim.followup_reason || "(no reason recorded)"}
            </span>
            <span className="text-[11px] text-muted-foreground">
              {claim.followup_flagged_at
                ? `Flagged ${formatDateTime(claim.followup_flagged_at)}`
                : ""}
              {claim.next_action_at
                ? ` · next action ${formatDateTime(claim.next_action_at)}`
                : ""}
            </span>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={clear}
            disabled={saving}
            data-testid="claim-followup-clear"
            className="rounded-sm"
          >
            {saving ? "Clearing…" : "Clear follow-up"}
          </Button>
        </>
      ) : (
        <>
          <Input
            id="cw-followup"
            data-testid="claim-followup-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. payer no response after 30 days"
            className="min-w-[18rem] flex-1"
          />
          <Button
            size="sm"
            onClick={flag}
            disabled={saving || !reason.trim()}
            data-testid="claim-followup-flag"
            className="rounded-sm"
          >
            {saving ? "Flagging…" : "Flag for follow-up"}
          </Button>
        </>
      )}
    </div>
  );
}
