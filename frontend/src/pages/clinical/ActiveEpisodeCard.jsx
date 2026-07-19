/**
 * ActiveEpisodeCard — Phase 2 Wave A primary object below the Care
 * Status panel. Shows episode + plan + upcoming milestones in one
 * grouped card. Primary/secondary/overflow action tiers per spec.
 *
 * Does not fetch — parent (ClinicalTabV2) already holds episodes,
 * activePlan, primaryDx, nextAppt, reExamDue in memory.
 */
import { useState } from "react";
import { toast } from "sonner";
import { CalendarPlus, Edit3, FileText, MoreHorizontal, PlayCircle, Plus, StickyNote, Target, Trash2, XCircle } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import { formatDate } from "../../utils/time";
import StatusBadge from "./status/StatusBadge";

function DefRow({ label, children, testId }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2" data-testid={testId}>
      <div className="min-w-[150px] text-sm font-medium text-muted-foreground">
        {label}
      </div>
      <div className="min-w-0 flex-1 text-base text-foreground">{children ?? <span className="text-muted-foreground italic">Not documented</span>}</div>
    </div>
  );
}

export default function ActiveEpisodeCard({
  patientId,
  episode,
  activePlan,
  primaryDx,
  nextAppt,
  reExamDue,
  canWrite,
  onJumpTo,
  onNewEpisode,
  onReauthNeeded,
  onEpisodeClosed,
}) {
  const [closing, setClosing] = useState(false);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!episode) {
    return (
      <section
        data-testid="active-episode-empty"
        aria-labelledby="active-episode-empty-title"
        className="rounded-xl border border-dashed border-border bg-card/40 p-6 text-center"
      >
        <Target className="mx-auto h-6 w-6 text-muted-foreground" aria-hidden="true" />
        <h3 id="active-episode-empty-title" className="mt-2 font-display text-base font-semibold text-foreground">
          No active episode
        </h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Open a case to anchor intake, diagnoses, and care plans.
        </p>
        {canWrite && (
          <Button
            size="sm"
            onClick={onNewEpisode}
            data-testid="active-episode-empty-new"
            className="mt-3 rounded-full"
          >
            <Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
            New episode
          </Button>
        )}
      </section>
    );
  }

  const closed = ["closed", "archived"].includes(episode.status);
  const completed = activePlan?.visits_completed ?? 0;
  const planned = activePlan?.total_visits_planned ?? activePlan?.visits_planned ?? null;
  const scheduled = activePlan?.visits_scheduled ?? null;

  async function closeEpisode() {
    if (reason.trim().length < 3) {
      toast.error("Closing reason must be at least 3 characters");
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await api.post(
        `/patients/${patientId}/clinical/episodes/${episode.id}/close`,
        { closed_reason: reason.trim() },
      );
      toast.success("Episode closed");
      onEpisodeClosed?.(data);
      setClosing(false);
      setReason("");
    } catch (err) {
      if (err?.response?.status === 401 && /re-auth/i.test(err.response?.data?.detail || "")) {
        setClosing(false);
        onReauthNeeded?.();
      } else {
        toast.error(formatApiError(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section
      data-testid="active-episode-card"
      aria-labelledby="active-episode-title"
      className="rounded-xl border border-primary/30 bg-card p-5 shadow-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 id="active-episode-title" className="font-display text-lg font-semibold text-foreground" data-testid="active-episode-title">
              {episode.title}
            </h3>
            <StatusBadge
              dim="record_state"
              value={episode.status === "on_hold" ? "inactive" : episode.status === "active" ? "active" : "archived"}
              testId="active-episode-record-state"
            />
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Opened {formatDate(episode.start_date)}
            {episode.responsible_provider_name ? ` · ${episode.responsible_provider_name}` : ""}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canWrite && !closed && (
            <>
              <Button size="sm" onClick={() => onJumpTo("encounters")} data-testid="active-episode-open" className="rounded-full">
                <PlayCircle className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Open episode
              </Button>
              <Button size="sm" variant="outline" onClick={() => onJumpTo("history")} data-testid="active-episode-edit" className="rounded-full">
                <Edit3 className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Edit
              </Button>
              <Button size="sm" variant="outline" onClick={() => onJumpTo("encounters")} data-testid="active-episode-add-note" className="rounded-full">
                <StickyNote className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Add note
              </Button>
              <Button size="sm" variant="outline" onClick={() => onJumpTo("care-plan")} data-testid="active-episode-view-plan" className="rounded-full">
                <FileText className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Care plan
              </Button>
            </>
          )}
          {canWrite && !closed && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost" data-testid="active-episode-more" aria-label="More episode actions" className="rounded-full">
                  <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-[220px]">
                <DropdownMenuLabel>Episode</DropdownMenuLabel>
                <DropdownMenuItem onSelect={() => setClosing(true)} data-testid="active-episode-menu-close" className="text-warning focus:bg-warning-soft focus:text-warning">
                  <XCircle className="mr-2 h-4 w-4" aria-hidden="true" />
                  Close episode
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={onNewEpisode} data-testid="active-episode-menu-new">
                  <Plus className="mr-2 h-4 w-4" aria-hidden="true" />
                  New episode
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-x-8 gap-y-1 divide-y divide-border/40 md:grid-cols-2 md:divide-y-0">
        <div className="md:pr-4">
          <DefRow label="Chief complaint" testId="episode-chief">
            {episode.chief_complaint || null}
          </DefRow>
          <DefRow label="Primary diagnosis" testId="episode-primary-dx">
            {primaryDx ? [primaryDx.icd10_code, primaryDx.label].filter(Boolean).join(" · ") : null}
          </DefRow>
          <DefRow label="Provider" testId="episode-provider">
            {episode.responsible_provider_name || null}
          </DefRow>
          <DefRow label="Latest response" testId="episode-latest-response">
            {activePlan?.latest_patient_response ? (
              <StatusBadge dim="clinical_response" value={activePlan.latest_patient_response} />
            ) : null}
          </DefRow>
        </div>
        <div className="md:pl-4">
          <DefRow label="Treatment plan" testId="episode-plan">
            {activePlan ? (
              <div className="flex flex-wrap items-center gap-2">
                <span>{activePlan.plan_name || "Active plan"}</span>
                <StatusBadge dim="record_state" value={activePlan.plan_status || "active"} />
              </div>
            ) : (
              <span className="text-muted-foreground italic">No active plan</span>
            )}
          </DefRow>
          <DefRow label="Visits progress" testId="episode-visits-progress">
            {planned != null ? (
              <span>
                {completed} of {planned} completed
                {scheduled != null ? ` · ${scheduled} scheduled` : ""}
                {planned != null && scheduled != null
                  ? ` · ${Math.max(planned - completed - scheduled, 0)} unscheduled`
                  : ""}
              </span>
            ) : null}
          </DefRow>
          <DefRow label="Next appointment" testId="episode-next-appt">
            {nextAppt ? formatDate(nextAppt.start_time) : <span className="text-muted-foreground italic">Not scheduled</span>}
          </DefRow>
          <DefRow label="Re-exam due" testId="episode-reexam-due">
            {reExamDue ? formatDate(reExamDue) : <span className="text-muted-foreground italic">Not scheduled</span>}
          </DefRow>
        </div>
      </div>

      <AlertDialog open={closing} onOpenChange={(v) => !v && setClosing(false)}>
        <AlertDialogContent data-testid="active-episode-close-dialog" className="rounded-sm">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Close this episode?</AlertDialogTitle>
            <AlertDialogDescription>
              Closing removes the episode from active workflows and locks its plans and diagnoses to read-only.
              This action is audited.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-1">
            <Label htmlFor="episode-close-reason">Reason for closing (3+ characters)</Label>
            <Textarea
              id="episode-close-reason"
              data-testid="active-episode-close-reason"
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="rounded-sm"
              placeholder="e.g. Condition resolved; transitioned to maintenance."
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-sm">Keep open</AlertDialogCancel>
            <AlertDialogAction
              data-testid="active-episode-close-confirm"
              disabled={submitting || reason.trim().length < 3}
              onClick={closeEpisode}
              className="rounded-sm bg-warning text-warning-foreground hover:brightness-95"
            >
              Close episode
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
