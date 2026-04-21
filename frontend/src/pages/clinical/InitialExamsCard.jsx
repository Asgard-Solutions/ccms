/**
 * InitialExamsCard — list of Initial Exams for a patient.
 *
 * Renders on the Clinical tab under Encounters. One row per exam with
 * status/sign-off metadata and a deep link into the full editor page.
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { FileCheck2, FilePen, FileText } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { Badge } from "../../components/ui/badge";
import { formatDate, formatDateTime } from "../../utils/time";

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

export default function InitialExamsCard({ patientId, canWrite }) {
  const navigate = useNavigate();
  const [rows, setRows] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/patients/${patientId}/clinical/exams`);
      setRows(data);
    } catch (e) {
      toast.error(formatApiError(e));
      setRows([]);
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  const open = (id) => navigate(`/patients/${patientId}/clinical/exams/${id}`);

  return (
    <section data-testid="clinical-exams-card" className="space-y-4">
      <div>
        <h3 className="font-display text-lg font-semibold text-foreground">
          Initial Exams
        </h3>
        <p className="text-sm text-muted-foreground">
          Structured initial evaluations. Signed exams are permanent chart
          artifacts; drafts stay editable until signed.
        </p>
      </div>

      {rows === null ? (
        <div className="space-y-3">
          <Skeleton className="h-20 rounded-lg" />
        </div>
      ) : rows.length === 0 ? (
        <div
          data-testid="exams-empty"
          className="rounded-lg border border-dashed border-border bg-card p-6 text-center text-sm text-muted-foreground"
        >
          No Initial Exams yet. Launch one from an in-progress encounter on the appointment.
        </div>
      ) : (
        <div data-testid="exams-list" className="space-y-2">
          {rows.map((exam) => {
            const tone = STATUS_TONE[exam.status] || "border-border bg-card";
            const Icon = exam.status === "signed" ? FileCheck2 : FilePen;
            return (
              <div
                key={exam.id}
                data-testid={`exam-row-${exam.id}`}
                className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border bg-card p-4"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Icon className="h-4 w-4 text-primary" />
                    <span className="font-display font-semibold text-foreground">
                      Initial Exam · {formatDate(exam.date_of_service)}
                    </span>
                    <Badge
                      variant="outline"
                      className={`text-[10px] uppercase tracking-wider ${tone}`}
                    >
                      {STATUS_LABEL[exam.status] || exam.status}
                    </Badge>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    {exam.provider_name && <span>Provider · {exam.provider_name}</span>}
                    {exam.episode_title && <span>Episode · {exam.episode_title}</span>}
                    {exam.signed_at && (
                      <span>
                        Signed {formatDateTime(exam.signed_at)}
                        {exam.signed_by_name ? ` · ${exam.signed_by_name}` : ""}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => open(exam.id)}
                    data-testid={`exam-open-${exam.id}`}
                    className="rounded-sm"
                  >
                    <FileText className="mr-1.5 h-3.5 w-3.5" />
                    {exam.status === "signed" ? "View" : canWrite ? "Continue" : "View"}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
