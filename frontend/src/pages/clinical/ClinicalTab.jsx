/**
 * ClinicalTab — Phase 1 shell for Patient Profile > Clinical.
 *
 * The Patient Profile owns the longitudinal clinical record. This tab is the
 * authoritative home for every clinical artifact (episodes/cases, notes,
 * diagnoses, treatment plans, outcomes, media, encounter links) regardless of
 * whether it was authored from the patient chart directly or from an
 * appointment encounter workflow in a later phase.
 *
 * Phase 1 scope:
 *   - Clinical Summary header (live episode counts + placeholders).
 *   - Episodes & Cases section: list + create + close + reopen.
 *   - Placeholder cards for the nine other sections the Patient Profile
 *     clinical shell must surface; each placeholder explains exactly what
 *     Phase 2+ will put there so the architecture intent is visible in the
 *     UI itself.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  PlayCircle,
  PlusCircle,
  Stethoscope,
  XCircle,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import IntakeHistoryCard from "./IntakeHistoryCard";
import DiagnosesCard from "./DiagnosesCard";
import EncountersCard from "./EncountersCard";
import InitialExamsCard from "./InitialExamsCard";
import FollowUpNotesCard from "./FollowUpNotesCard";
import CareTimelineCard from "./CareTimelineCard";
import TreatmentPlansCard from "./TreatmentPlansCard";
import ReExamsCard from "./ReExamsCard";
import MediaCard from "./MediaCard";
import OutcomesCard from "./OutcomesCard";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { Badge } from "../../components/ui/badge";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { formatDate, formatDateTime } from "../../utils/time";

export const CASE_TYPES = [
  { value: "new_patient_eval", label: "New patient evaluation" },
  { value: "injury_episode", label: "Injury episode" },
  { value: "recurrence", label: "Recurrence / Flare-up" },
  { value: "maintenance", label: "Maintenance / Wellness" },
  { value: "mva", label: "Motor vehicle accident" },
  { value: "workers_comp", label: "Workers compensation" },
  { value: "personal_injury", label: "Personal injury" },
];

const STATUS_TONE = {
  active: "bg-success-soft text-success",
  on_hold: "bg-warning-soft text-warning",
  closed: "bg-muted text-muted-foreground",
  archived: "bg-muted text-muted-foreground",
};

function caseTypeLabel(value) {
  return CASE_TYPES.find((c) => c.value === value)?.label || value;
}

function StatBadge({ label, value, testId }) {
  return (
    <div
      data-testid={testId}
      className="rounded-lg border border-border bg-card p-4"
    >
      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 font-display text-2xl font-medium tracking-tight text-foreground">
        {value}
      </div>
    </div>
  );
}

function EpisodeCreateDialog({
  open,
  onOpenChange,
  providers,
  onCreated,
  patientId,
  onReauthNeeded,
}) {
  const [form, setForm] = useState({
    case_type: "new_patient_eval",
    title: "",
    chief_complaint: "",
    mechanism_of_injury: "",
    responsible_provider_id: "",
    onset_date: "",
  });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) {
      setForm({
        case_type: "new_patient_eval",
        title: "",
        chief_complaint: "",
        mechanism_of_injury: "",
        responsible_provider_id: "",
        onset_date: "",
      });
    }
  }, [open]);

  async function submit(e) {
    e.preventDefault();
    if (!form.title.trim()) {
      toast.error("Episode title is required");
      return;
    }
    setSubmitting(true);
    try {
      const body = {
        case_type: form.case_type,
        title: form.title.trim(),
      };
      if (form.chief_complaint.trim()) body.chief_complaint = form.chief_complaint.trim();
      if (form.mechanism_of_injury.trim()) body.mechanism_of_injury = form.mechanism_of_injury.trim();
      if (form.onset_date) body.onset_date = form.onset_date;
      if (form.responsible_provider_id) body.responsible_provider_id = form.responsible_provider_id;

      const { data } = await api.post(
        `/patients/${patientId}/clinical/episodes`,
        body,
      );
      toast.success("Episode created");
      onCreated(data);
      onOpenChange(false);
    } catch (err) {
      if (err?.response?.status === 401 && /re-auth/i.test(err.response?.data?.detail || "")) {
        onReauthNeeded();
      } else {
        toast.error(formatApiError(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="clinical-episode-create-dialog" className="max-w-xl rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Open new episode / case</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="case-type">Case type</Label>
            <Select
              value={form.case_type}
              onValueChange={(v) => setForm({ ...form, case_type: v })}
            >
              <SelectTrigger
                id="case-type"
                data-testid="episode-case-type"
                className="rounded-sm"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CASE_TYPES.map((c) => (
                  <SelectItem key={c.value} value={c.value}>
                    {c.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1">
            <Label htmlFor="episode-title">Title</Label>
            <Input
              id="episode-title"
              required
              data-testid="episode-title"
              placeholder="e.g. Lumbar flare post-Dec MVA"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              className="rounded-sm"
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="episode-chief">Chief complaint</Label>
            <Textarea
              id="episode-chief"
              data-testid="episode-chief-complaint"
              rows={2}
              value={form.chief_complaint}
              onChange={(e) => setForm({ ...form, chief_complaint: e.target.value })}
              className="rounded-sm"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <Label htmlFor="episode-onset">Onset date</Label>
              <Input
                id="episode-onset"
                type="date"
                data-testid="episode-onset-date"
                value={form.onset_date}
                onChange={(e) => setForm({ ...form, onset_date: e.target.value })}
                className="rounded-sm"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="episode-provider">Responsible provider</Label>
              <Select
                value={form.responsible_provider_id}
                onValueChange={(v) =>
                  setForm({ ...form, responsible_provider_id: v === "__none" ? "" : v })
                }
              >
                <SelectTrigger
                  id="episode-provider"
                  data-testid="episode-provider"
                  className="rounded-sm"
                >
                  <SelectValue placeholder="Unassigned" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none">Unassigned</SelectItem>
                  {providers.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name || p.email}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1">
            <Label htmlFor="episode-moi">Mechanism of injury</Label>
            <Textarea
              id="episode-moi"
              data-testid="episode-mechanism"
              rows={2}
              value={form.mechanism_of_injury}
              onChange={(e) => setForm({ ...form, mechanism_of_injury: e.target.value })}
              className="rounded-sm"
            />
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              className="rounded-sm"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={submitting}
              data-testid="episode-submit-btn"
              className="rounded-sm"
            >
              {submitting ? "Creating…" : "Create episode"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EpisodeCloseDialog({ open, onOpenChange, episode, patientId, onClosed, onReauthNeeded }) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) setReason("");
  }, [open]);

  async function submit() {
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
      onClosed(data);
      onOpenChange(false);
    } catch (err) {
      if (err?.response?.status === 401 && /re-auth/i.test(err.response?.data?.detail || "")) {
        onReauthNeeded();
      } else {
        toast.error(formatApiError(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="clinical-episode-close-dialog" className="max-w-md rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Close episode</DialogTitle>
        </DialogHeader>
        {episode && (
          <div className="space-y-3">
            <div className="rounded-sm border border-border bg-muted/40 p-3 text-sm">
              <div className="font-semibold text-foreground">{episode.title}</div>
              <div className="text-xs text-muted-foreground">
                {caseTypeLabel(episode.case_type)} · opened {formatDate(episode.start_date)}
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="close-reason">Reason for closing</Label>
              <Textarea
                id="close-reason"
                rows={3}
                data-testid="episode-close-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="rounded-sm"
                placeholder="Condition resolved, transitioned to maintenance, etc."
              />
            </div>
          </div>
        )}
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            className="rounded-sm"
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={submitting}
            onClick={submit}
            data-testid="episode-close-submit-btn"
            className="rounded-sm"
          >
            {submitting ? "Closing…" : "Close episode"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EpisodeRow({ episode, onClose, onReopen, canWrite }) {
  const tone = STATUS_TONE[episode.status] || "bg-muted text-muted-foreground";
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
              className={`rounded-sm px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${tone}`}
              data-testid={`clinical-episode-${episode.id}-status`}
            >
              {episode.status.replace("_", " ")}
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
                <XCircle className="mr-1.5 h-3.5 w-3.5" />
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
                <PlayCircle className="mr-1.5 h-3.5 w-3.5" />
                Reopen
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ClinicalTab({
  patientId,
  providers = [],
  canWrite = false,
  onReauthNeeded,
}) {
  const [summary, setSummary] = useState(null);
  const [episodes, setEpisodes] = useState(null);
  const [creating, setCreating] = useState(false);
  const [closing, setClosing] = useState(null); // episode being closed
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [sumRes, epRes] = await Promise.all([
        api.get(`/patients/${patientId}/clinical/summary`),
        api.get(`/patients/${patientId}/clinical/episodes`),
      ]);
      setSummary(sumRes.data);
      setEpisodes(epRes.data);
    } catch (e) {
      setErr(formatApiError(e));
      setSummary(null);
      setEpisodes([]);
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreated = (row) => {
    setEpisodes((prev) => [row, ...(prev || [])]);
    setSummary((s) =>
      s
        ? {
            ...s,
            episodes: {
              total: s.episodes.total + 1,
              open: s.episodes.open + 1,
            },
          }
        : s,
    );
  };

  const handleClosed = (row) => {
    setEpisodes((prev) =>
      (prev || []).map((e) => (e.id === row.id ? row : e)),
    );
    setSummary((s) =>
      s
        ? {
            ...s,
            episodes: {
              ...s.episodes,
              open: Math.max(0, s.episodes.open - 1),
            },
          }
        : s,
    );
  };

  const handleReopen = async (episode) => {
    try {
      const { data } = await api.post(
        `/patients/${patientId}/clinical/episodes/${episode.id}/reopen`,
      );
      toast.success("Episode reopened");
      setEpisodes((prev) =>
        (prev || []).map((e) => (e.id === data.id ? data : e)),
      );
      setSummary((s) =>
        s ? { ...s, episodes: { ...s.episodes, open: s.episodes.open + 1 } } : s,
      );
    } catch (e) {
      if (e?.response?.status === 401 && /re-auth/i.test(e.response?.data?.detail || "")) {
        onReauthNeeded?.();
      } else {
        toast.error(formatApiError(e));
      }
    }
  };

  const stats = useMemo(() => {
    const s = summary || {};
    return [
      { label: "In-progress visits", value: s.encounters?.open ?? "—", id: "stat-encounters" },
      { label: "Open exams", value: s.initial_exams?.open ?? "—", id: "stat-exams" },
      { label: "Active plans", value: s.treatment_plans?.open ?? "—", id: "stat-treatment-plans" },
      { label: "Open re-exams", value: s.re_exams?.open ?? "—", id: "stat-reexams" },
      { label: "Open notes", value: s.notes?.open ?? "—", id: "stat-notes" },
      {
        label: "Active diagnoses",
        value: s.diagnoses?.open ?? "—",
        id: "stat-diagnoses",
      },
    ];
  }, [summary]);

  return (
    <section data-testid="patient-clinical-tab" className="space-y-8">
      {/* Clinical Summary */}
      <div>
        <div className="mb-3 flex items-end justify-between">
          <div>
            <h2 className="font-display text-xl font-semibold text-foreground">
              Clinical Summary
            </h2>
            <p className="text-sm text-muted-foreground">
              Longitudinal chart view. Every artifact lives under this patient even when authored from an appointment.
            </p>
          </div>
          {summary?.generated_at && (
            <span className="text-xs text-muted-foreground">
              Synced {formatDateTime(summary.generated_at)}
            </span>
          )}
        </div>

        <div
          data-testid="clinical-summary-stats"
          className="grid grid-cols-2 gap-3 sm:grid-cols-4"
        >
          {summary === null
            ? Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-20 rounded-lg" />
              ))
            : stats.map((s) => (
                <StatBadge key={s.id} label={s.label} value={s.value} testId={s.id} />
              ))}
        </div>
        {err && (
          <div
            data-testid="clinical-summary-error"
            className="mt-3 rounded-sm border border-destructive/30 bg-destructive-soft p-3 text-sm text-destructive"
          >
            {err}
          </div>
        )}
      </div>

      {/* Episodes & Cases */}
      <div>
        <div className="mb-3 flex items-end justify-between">
          <div>
            <h3 className="font-display text-lg font-semibold text-foreground">
              Episodes &amp; Cases
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
              className="rounded-sm"
            >
              <PlusCircle className="mr-1.5 h-4 w-4" />
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
            className="rounded-lg border border-dashed border-border bg-card p-8 text-center"
          >
            <Stethoscope className="mx-auto h-8 w-8 text-muted-foreground" />
            <p className="mt-3 font-display text-base font-semibold text-foreground">
              No episodes yet
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              Open the patient&apos;s first case to anchor intake, diagnoses, and care plans.
            </p>
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
      </div>

      {/* Phase 2 — Intake & History + Diagnoses are live */}
      <IntakeHistoryCard
        patientId={patientId}
        canWrite={canWrite}
        onReauthNeeded={onReauthNeeded}
      />

      <DiagnosesCard
        patientId={patientId}
        episodes={episodes || []}
        canWrite={canWrite}
        onReauthNeeded={onReauthNeeded}
      />

      {/* Phase 3 — Appointment-launched encounters */}
      <EncountersCard
        patientId={patientId}
        canWrite={canWrite}
        onReauthNeeded={onReauthNeeded}
      />

      {/* Phase 4 — Initial Exams */}
      <InitialExamsCard
        patientId={patientId}
        canWrite={canWrite}
      />

      {/* Phase 6 — Treatment Plans */}
      <TreatmentPlansCard
        patientId={patientId}
        canWrite={canWrite}
        episodes={episodes || []}
        onReauthNeeded={onReauthNeeded}
      />

      {/* Phase 5 — Follow-up / Daily Visit notes */}
      <FollowUpNotesCard patientId={patientId} />

      {/* Phase 6 — Re-Exams */}
      <ReExamsCard patientId={patientId} />

      {/* Phase 5 — Care Timeline */}
      <CareTimelineCard patientId={patientId} />

      {/* Phase 7 — Imaging & Clinical Media */}
      <MediaCard
        patientId={patientId}
        canWrite={canWrite}
        onReauthNeeded={onReauthNeeded}
      />

      {/* Phase 7 — Outcomes & Functional Measures */}
      <OutcomesCard
        patientId={patientId}
        canWrite={canWrite}
        onReauthNeeded={onReauthNeeded}
      />

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
