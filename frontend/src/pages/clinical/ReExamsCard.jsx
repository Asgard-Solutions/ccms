/**
 * ReExamsCard — list of Re-Exams on the Clinical tab.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Activity, GitBranch } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Skeleton } from "../../components/ui/skeleton";
import { formatDateTime } from "../../utils/time";

const STATUS_TONE = {
  draft: "border-border bg-card text-muted-foreground",
  sign_ready: "border-warning/40 bg-warning-soft text-warning",
  signed: "border-success/40 bg-success-soft text-success",
};
const STATUS_LABEL = { draft: "Draft", sign_ready: "Sign-ready", signed: "Signed" };
const RECO_LABEL = {
  continue: "Continue",
  modify_plan: "Modify plan",
  discharge: "Discharge",
  transition_maintenance: "Transition to maintenance",
};

export default function ReExamsCard({ patientId }) {
  const [rows, setRows] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/patients/${patientId}/clinical/re-exams`);
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
    <section data-testid="reexams-card" className="space-y-4">
      <div>
        <h3 className="font-display text-lg font-semibold text-foreground">
          Re-Exams
        </h3>
        <p className="text-sm text-muted-foreground">
          Progress reassessment. Launched from a re-evaluation encounter;
          frozen against the initial exam and active plan baselines.
        </p>
      </div>

      {rows === null ? (
        <div className="space-y-2">
          <Skeleton className="h-16 rounded-lg" />
        </div>
      ) : rows.length === 0 ? (
        <div
          data-testid="reexams-empty"
          className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-dashed border-border bg-card/60 px-5 py-4"
        >
          <div className="flex items-center gap-3">
            <Activity className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-foreground">
                No re-evaluation encounter scheduled
              </p>
              <p className="text-xs text-muted-foreground">
                Launch a re-exam from a re-evaluation encounter on the schedule.
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div data-testid="reexams-list" className="space-y-2">
          {rows.map((r) => {
            const tone = STATUS_TONE[r.status] || STATUS_TONE.draft;
            return (
              <Link
                key={r.id}
                to={`/patients/${patientId}/clinical/re-exams/${r.id}`}
                data-testid={`reexam-row-${r.id}`}
                className="block rounded-lg border border-border bg-card p-4 transition-colors hover:bg-muted/40"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <GitBranch className="h-4 w-4 text-muted-foreground" />
                      <span className="font-display text-base font-semibold text-foreground">
                        {formatDateTime(r.date_of_service)}
                      </span>
                      <Badge
                        variant="outline"
                        data-testid={`reexam-row-${r.id}-status`}
                        className={`text-[10px] uppercase tracking-wider ${tone}`}
                      >
                        {STATUS_LABEL[r.status] || r.status}
                      </Badge>
                      {r.recommendation_decision && (
                        <Badge
                          variant="outline"
                          data-testid={`reexam-row-${r.id}-reco`}
                          className="text-[10px]"
                        >
                          {RECO_LABEL[r.recommendation_decision] || r.recommendation_decision}
                        </Badge>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                      {r.provider_name && <span>Provider · {r.provider_name}</span>}
                      {r.episode_title && <span>Episode · {r.episode_title}</span>}
                      {r.visit_number_at_reexam != null && (
                        <span>After {r.visit_number_at_reexam} visits</span>
                      )}
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
