/**
 * EpisodesSection — episodes list + create/close/reopen dialogs, extracted
 * from the legacy ClinicalTab so ClinicalTabV2 can render it inside the
 * new Summary section without duplicating dialog code.
 *
 * The legacy ClinicalTab.jsx keeps its own inline copy; this component is
 * the redesign-only surface. Business logic (endpoints, statuses, dialogs)
 * matches ClinicalTab exactly.
 */
import { useState } from "react";
import { toast } from "sonner";
import { PlayCircle, PlusCircle, Stethoscope, XCircle } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { Badge } from "../../components/ui/badge";
import { formatDate } from "../../utils/time";
import {
  EpisodeCreateDialog,
  EpisodeCloseDialog,
  CASE_TYPES,
} from "./episodeDialogs";

const STATUS_TONE = {
  active: "bg-success-soft text-success",
  on_hold: "bg-warning-soft text-warning",
  closed: "bg-muted text-muted-foreground",
  archived: "bg-muted text-muted-foreground",
};

function caseTypeLabel(value) {
  return CASE_TYPES.find((c) => c.value === value)?.label || value;
}

function EpisodeRow({ episode, onClose, onReopen, canWrite }) {
  const tone = STATUS_TONE[episode.status] || "bg-muted text-muted-foreground";
  const statusLabel = episode.status.replace("_", " ");
  return (
    <div
      data-testid={`clinical-episode-${episode.id}`}
      className="rounded-lg border border-border bg-card p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h4 className="font-display text-base font-semibold text-foreground">
              {episode.title}
            </h4>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium capitalize ${tone}`}
              data-testid={`clinical-episode-${episode.id}-status`}
            >
              {statusLabel}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>{caseTypeLabel(episode.case_type)}</span>
            <span>Opened {formatDate(episode.start_date)}</span>
            {episode.end_date && <span>Closed {formatDate(episode.end_date)}</span>}
            {episode.responsible_provider_name && (
              <span>Provider · {episode.responsible_provider_name}</span>
            )}
          </div>
          {episode.chief_complaint && (
            <p className="mt-2 text-sm text-muted-foreground">{episode.chief_complaint}</p>
          )}
          {episode.closed_reason && (
            <p className="mt-2 text-xs italic text-muted-foreground">
              Close reason: {episode.closed_reason}
            </p>
          )}
          {(episode.tags || []).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {episode.tags.map((t) => (
                <Badge key={t} variant="outline" className="text-[10px]">
                  {t}
                </Badge>
              ))}
            </div>
          )}
        </div>

        {canWrite && (
          <div className="flex shrink-0 gap-2">
            {episode.status === "active" || episode.status === "on_hold" ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => onClose(episode)}
                data-testid={`clinical-episode-${episode.id}-close-btn`}
                className="rounded-sm"
              >
                <XCircle className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Close
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={() => onReopen(episode)}
                data-testid={`clinical-episode-${episode.id}-reopen-btn`}
                className="rounded-sm"
              >
                <PlayCircle className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Reopen
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function EpisodesSection({
  patientId,
  providers,
  canWrite,
  onReauthNeeded,
  episodes,
  onEpisodesChange,
  onSummaryReload,
}) {
  const [creating, setCreating] = useState(false);
  const [closing, setClosing] = useState(null);

  const handleCreated = (row) => {
    onEpisodesChange((prev) => [row, ...(prev || [])]);
    onSummaryReload?.();
  };
  const handleClosed = (row) => {
    onEpisodesChange((prev) =>
      (prev || []).map((e) => (e.id === row.id ? row : e)),
    );
    onSummaryReload?.();
  };
  const handleReopen = async (episode) => {
    try {
      const { data } = await api.post(
        `/patients/${patientId}/clinical/episodes/${episode.id}/reopen`,
      );
      toast.success("Episode reopened");
      onEpisodesChange((prev) =>
        (prev || []).map((e) => (e.id === data.id ? data : e)),
      );
      onSummaryReload?.();
    } catch (e) {
      if (e?.response?.status === 401 && /re-auth/i.test(e.response?.data?.detail || "")) {
        onReauthNeeded?.();
      } else {
        toast.error(formatApiError(e));
      }
    }
  };

  return (
    <section data-testid="clinical-episodes-section" className="space-y-4">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h3 className="font-display text-lg font-semibold text-foreground">
            Episodes &amp; cases
          </h3>
          <p className="text-sm text-muted-foreground">
            Injury episodes, maintenance courses, MVA/WC/PI case structures.
          </p>
        </div>
        {canWrite && (
          <Button
            size="sm"
            onClick={() => setCreating(true)}
            data-testid="clinical-new-episode-btn"
            className="rounded-full"
          >
            <PlusCircle className="mr-1.5 h-4 w-4" aria-hidden="true" />
            New episode
          </Button>
        )}
      </div>

      {episodes === null ? (
        <div className="space-y-3">
          <Skeleton className="h-20 rounded-lg" />
          <Skeleton className="h-20 rounded-lg" />
        </div>
      ) : episodes.length === 0 ? (
        <div
          data-testid="clinical-episodes-empty"
          className="flex items-center justify-between gap-4 rounded-lg border border-dashed border-border bg-card/60 px-5 py-4"
        >
          <div className="flex items-center gap-3">
            <Stethoscope className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-foreground">
                No episodes yet
              </p>
              <p className="text-xs text-muted-foreground">
                Open the patient&apos;s first case to anchor intake, diagnoses, and care plans.
              </p>
            </div>
          </div>
          {canWrite && (
            <Button
              size="sm"
              onClick={() => setCreating(true)}
              data-testid="clinical-empty-new-episode"
              className="rounded-full"
            >
              <PlusCircle className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              New episode
            </Button>
          )}
        </div>
      ) : (
        <div data-testid="clinical-episodes-list" className="space-y-3">
          {episodes.map((ep) => (
            <EpisodeRow
              key={ep.id}
              episode={ep}
              onClose={setClosing}
              onReopen={handleReopen}
              canWrite={canWrite}
            />
          ))}
        </div>
      )}

      {canWrite && (
        <>
          <EpisodeCreateDialog
            open={creating}
            onOpenChange={setCreating}
            providers={providers}
            patientId={patientId}
            onCreated={handleCreated}
            onReauthNeeded={() => {
              setCreating(false);
              onReauthNeeded?.();
            }}
          />
          <EpisodeCloseDialog
            open={!!closing}
            onOpenChange={(v) => !v && setClosing(null)}
            episode={closing}
            patientId={patientId}
            onClosed={handleClosed}
            onReauthNeeded={() => {
              setClosing(null);
              onReauthNeeded?.();
            }}
          />
        </>
      )}
    </section>
  );
}
