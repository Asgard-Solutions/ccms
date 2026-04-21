/**
 * FollowUpNotesCard — list of Follow-up / Daily Visit notes on the Clinical tab.
 *
 * Renders date / status / visit # / provider / completeness meter per row with
 * a direct link into FollowUpNoteEditor. No create affordance here — the
 * canonical create point is EncountersCard's "Start follow-up note" button
 * against an in-progress encounter.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { FileText, NotebookPen } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Skeleton } from "../../components/ui/skeleton";
import { formatDateTime } from "../../utils/time";

const STATUS_TONE = {
  draft: "border-border bg-card text-muted-foreground",
  sign_ready: "border-warning/40 bg-warning-soft text-warning",
  signed: "border-success/40 bg-success-soft text-success",
};

const STATUS_LABEL = {
  draft: "Draft",
  sign_ready: "Sign-ready",
  signed: "Signed",
};

export default function FollowUpNotesCard({ patientId }) {
  const [rows, setRows] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/patients/${patientId}/clinical/notes`);
      setRows(data);
    } catch (e) {
      toast.error(formatApiError(e));
      setRows([]);
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section data-testid="clinical-notes-card" className="space-y-4">
      <div>
        <h3 className="font-display text-lg font-semibold text-foreground">
          Follow-up &amp; Daily Visit notes
        </h3>
        <p className="text-sm text-muted-foreground">
          Daily charting for ongoing care. Launched from an in-progress encounter;
          appears here and in the Care Timeline once signed.
        </p>
      </div>

      {rows === null ? (
        <div className="space-y-3">
          <Skeleton className="h-16 rounded-lg" />
          <Skeleton className="h-16 rounded-lg" />
        </div>
      ) : rows.length === 0 ? (
        <div
          data-testid="notes-empty"
          className="rounded-lg border border-dashed border-border bg-card p-8 text-center"
        >
          <NotebookPen className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 font-display text-base font-semibold text-foreground">
            No follow-up notes yet
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            Launch a follow-up note from an in-progress encounter to start
            daily-visit charting.
          </p>
        </div>
      ) : (
        <div data-testid="notes-list" className="space-y-2">
          {rows.map((n) => {
            const tone = STATUS_TONE[n.status] || STATUS_TONE.draft;
            const c = n.completeness || { filled: 0, total: 5, score: 0 };
            return (
              <Link
                key={n.id}
                to={`/patients/${patientId}/clinical/follow-up/${n.id}`}
                data-testid={`note-row-${n.id}`}
                className="block rounded-lg border border-border bg-card p-4 transition-colors hover:bg-muted/40"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <FileText className="h-4 w-4 text-muted-foreground" />
                      <span className="font-display text-base font-semibold text-foreground">
                        {formatDateTime(n.date_of_service)}
                      </span>
                      <Badge
                        variant="outline"
                        data-testid={`note-row-${n.id}-status`}
                        className={`text-[10px] uppercase tracking-wider ${tone}`}
                      >
                        {STATUS_LABEL[n.status] || n.status}
                      </Badge>
                      {n.visit_number != null && (
                        <Badge
                          variant="outline"
                          data-testid={`note-row-${n.id}-visit`}
                          className="text-[10px]"
                        >
                          Visit #{n.visit_number}
                        </Badge>
                      )}
                      {(n.copied_fields || []).length > 0 && (
                        <Badge
                          variant="outline"
                          className="border-warning/40 bg-warning-soft text-[10px] text-warning"
                        >
                          {n.copied_fields.length} copied
                        </Badge>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                      {n.provider_name && <span>Provider · {n.provider_name}</span>}
                      {n.episode_title && <span>Episode · {n.episode_title}</span>}
                      {n.assessment?.response_to_care && (
                        <span>Response · {n.assessment.response_to_care.replace("_", " ")}</span>
                      )}
                    </div>
                  </div>
                  <div
                    data-testid={`note-row-${n.id}-completeness`}
                    className="shrink-0 text-right"
                  >
                    <div className="font-display text-sm font-semibold text-foreground">
                      {c.filled}/{c.total}
                    </div>
                    <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      {c.score}% complete
                    </div>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </section>
  );
}
