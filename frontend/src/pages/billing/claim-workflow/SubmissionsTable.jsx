import { FileDown } from "lucide-react";
import { Button } from "../../../components/ui/button";
import { formatCents } from "../../../utils/money";
import { formatDateTime } from "../../../utils/time";
import { OUTCOME_LABELS, SUBMISSION_METHOD_LABELS } from "../useClaims";

/** Read-only table of past submission attempts against the claim,
 *  plus inline buttons to view the stored 837P/JSON payload and
 *  record an outcome when still pending. */
export function SubmissionsTable({
  loading, submissions, onViewPayload, onRecordOutcome,
}) {
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
