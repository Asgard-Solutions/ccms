/**
 * CareTimelineCard — merged chronology of encounters + initial exams +
 * follow-up notes for a patient. Read-only; every entry deep-links to its
 * authoring surface.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  Activity,
  CalendarCheck2,
  FileText,
  GitBranch,
  Image as ImageIcon,
  NotebookPen,
  Stethoscope,
  Target,
  Timer,
  ClipboardList,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Skeleton } from "../../components/ui/skeleton";
import { formatDateTime } from "../../utils/time";

const KIND_META = {
  encounter: { Icon: CalendarCheck2, tone: "text-primary" },
  initial_exam: { Icon: Stethoscope, tone: "text-warning" },
  follow_up_note: { Icon: NotebookPen, tone: "text-success" },
  re_exam: { Icon: GitBranch, tone: "text-primary" },
  treatment_plan: { Icon: Target, tone: "text-foreground" },
  clinical_media: { Icon: ImageIcon, tone: "text-primary" },
  outcome_entry: { Icon: Activity, tone: "text-success" },
  diagnosis_change: { Icon: GitBranch, tone: "text-warning" },
  intake_submission: { Icon: ClipboardList, tone: "text-muted-foreground" },
};

const STATUS_TONE = {
  draft: "border-border bg-card text-muted-foreground",
  sign_ready: "border-warning/40 bg-warning-soft text-warning",
  signed: "border-success/40 bg-success-soft text-success",
  in_progress: "border-primary/30 bg-primary/10 text-primary",
  completed: "border-success/30 bg-success-soft text-success",
  cancelled: "border-border bg-muted text-muted-foreground",
  active: "border-success/40 bg-success-soft text-success",
  on_hold: "border-warning/40 bg-warning-soft text-warning",
  discharged: "border-border bg-muted text-muted-foreground",
  uploaded: "border-primary/30 bg-primary/10 text-primary",
  provider_charted: "border-success/30 bg-success-soft text-success",
  patient_reported: "border-primary/30 bg-primary/10 text-primary",
  reexam: "border-primary/30 bg-primary/10 text-primary",
  created: "border-success/30 bg-success-soft text-success",
  updated: "border-warning/40 bg-warning-soft text-warning",
  resolved: "border-border bg-muted text-muted-foreground",
  activated: "border-success/30 bg-success-soft text-success",
  submitted: "border-primary/30 bg-primary/10 text-primary",
};

export default function CareTimelineCard({ patientId }) {
  const [entries, setEntries] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/patients/${patientId}/clinical/care-timeline`);
      setEntries(data.entries || []);
    } catch (e) {
      toast.error(formatApiError(e));
      setEntries([]);
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section data-testid="care-timeline-card" className="space-y-4">
      <div>
        <h3 className="font-display text-lg font-semibold text-foreground">
          Care Timeline
        </h3>
        <p className="text-sm text-muted-foreground">
          Chronological patient story — encounters, initial exams, and follow-up
          notes — most recent first.
        </p>
      </div>

      {entries === null ? (
        <div className="space-y-2">
          <Skeleton className="h-12 rounded-lg" />
          <Skeleton className="h-12 rounded-lg" />
          <Skeleton className="h-12 rounded-lg" />
        </div>
      ) : entries.length === 0 ? (
        <div
          data-testid="care-timeline-empty"
          className="rounded-lg border border-dashed border-border bg-card p-8 text-center"
        >
          <Timer className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 font-display text-base font-semibold text-foreground">
            No chart activity yet
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            Launch an encounter from a calendar appointment to start this timeline.
          </p>
        </div>
      ) : (
        <ol
          data-testid="care-timeline-list"
          className="relative space-y-0 before:absolute before:left-[11px] before:top-2 before:bottom-2 before:w-px before:bg-border"
        >
          {entries.map((e) => {
            const meta = KIND_META[e.kind] || KIND_META.encounter;
            const tone = STATUS_TONE[e.status] || "border-border bg-card text-muted-foreground";
            return (
              <li
                key={`${e.kind}-${e.id}`}
                data-testid={`timeline-entry-${e.kind}-${e.id}`}
                className="relative flex gap-3 py-2 pl-6"
              >
                <span
                  aria-hidden="true"
                  className={`absolute left-0 top-3 inline-flex h-6 w-6 items-center justify-center rounded-full border border-border bg-card ${meta.tone}`}
                >
                  <meta.Icon className="h-3 w-3" />
                </span>
                <div className="flex-1 rounded-sm border border-border bg-card p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-display text-sm font-semibold text-foreground">
                      {e.title}
                    </span>
                    <Badge
                      variant="outline"
                      className={`text-[10px] uppercase tracking-wider ${tone}`}
                    >
                      {e.status?.replace("_", " ")}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {formatDateTime(e.date_of_service)}
                    </span>
                    {e.provider_name && (
                      <span className="text-xs text-muted-foreground">
                        · {e.provider_name}
                      </span>
                    )}
                  </div>
                  {e.subtitle && (
                    <p className="mt-1 text-xs text-muted-foreground">{e.subtitle}</p>
                  )}
                  {e.link_path && (
                    <Link
                      to={e.link_path}
                      data-testid={`timeline-open-${e.kind}-${e.id}`}
                      className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                    >
                      <FileText className="h-3 w-3" /> Open
                    </Link>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
