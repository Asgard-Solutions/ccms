/**
 * EncounterLaunchDialog — Phase 3 appointment → encounter launch.
 *
 * Opened from the appointment (BookDialog reschedule mode). Picks encounter
 * type + optional episode and POSTs to
 *   /api/appointments/{aid}/clinical/encounters
 * The endpoint is idempotent — a duplicate launch returns the existing
 * encounter with existed=true and we route straight to the chart.
 *
 * For a cancelled appointment, the dialog requires a structured
 * `exception_reason` before it enables submit. Non-cancelled appointments
 * go through the normal path.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Stethoscope } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { Badge } from "../../components/ui/badge";

const ENCOUNTER_TYPES = [
  { value: "new_patient_exam", label: "New patient exam" },
  { value: "follow_up", label: "Follow-up / Adjustment" },
  { value: "re_evaluation", label: "Re-evaluation" },
  { value: "treatment_visit", label: "Treatment visit" },
];

/** Maps the appointment "reason" or appointment type name to a sensible
 *  default encounter type. Purely a UX convenience; the provider can always
 *  change it before submit. */
function inferEncounterType(reason = "") {
  const r = (reason || "").toLowerCase();
  if (r.includes("new patient") || r.includes("initial") || r.includes("exam"))
    return "new_patient_exam";
  if (r.includes("re-eval") || r.includes("re eval") || r.includes("re-exam"))
    return "re_evaluation";
  if (r.includes("follow") || r.includes("adjust")) return "follow_up";
  if (r.includes("treatment")) return "treatment_visit";
  return "follow_up";
}

export default function EncounterLaunchDialog({
  open,
  onOpenChange,
  appointment,
  onReauthNeeded,
}) {
  const navigate = useNavigate();
  const [encounterType, setEncounterType] = useState("follow_up");
  const [episodeId, setEpisodeId] = useState("__none");
  const [exceptionReason, setExceptionReason] = useState("");
  const [episodes, setEpisodes] = useState([]);
  const [existingEncounter, setExistingEncounter] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(false);

  const isCancelled = appointment?.status === "cancelled";

  const load = useCallback(async () => {
    if (!open || !appointment?.id || !appointment?.patient_id) return;
    setLoading(true);
    try {
      const [epsRes, encRes] = await Promise.all([
        api.get(`/patients/${appointment.patient_id}/clinical/episodes`),
        api.get(`/appointments/${appointment.id}/clinical/encounter`),
      ]);
      setEpisodes(epsRes.data || []);
      setExistingEncounter(encRes.data || null);
      setEncounterType(inferEncounterType(appointment.reason));
      // Default to the most recently opened active episode, if any.
      const active = (epsRes.data || []).find((e) => e.status === "active");
      setEpisodeId(active ? active.id : "__none");
      setExceptionReason("");
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [open, appointment?.id, appointment?.patient_id, appointment?.reason, appointment?.status]);

  useEffect(() => {
    load();
  }, [load]);

  const submitDisabled = useMemo(() => {
    if (submitting || loading) return true;
    if (isCancelled && exceptionReason.trim().length < 3) return true;
    return false;
  }, [submitting, loading, isCancelled, exceptionReason]);

  async function submit() {
    setSubmitting(true);
    try {
      const body = { encounter_type: encounterType };
      if (episodeId && episodeId !== "__none") body.episode_id = episodeId;
      if (isCancelled) body.exception_reason = exceptionReason.trim();
      const { data } = await api.post(
        `/appointments/${appointment.id}/clinical/encounters`,
        body,
      );
      const enc = data.encounter;
      toast.success(
        data.existed
          ? "Encounter already in progress — opening it now"
          : "Encounter launched",
      );
      onOpenChange(false);
      navigate(`/patients/${enc.patient_id}?tab=clinical&encounter=${enc.id}`);
    } catch (e) {
      if (e?.response?.status === 401 && /re-auth/i.test(e.response?.data?.detail || "")) {
        onReauthNeeded?.();
      } else {
        toast.error(formatApiError(e));
      }
    } finally {
      setSubmitting(false);
    }
  }

  const openExisting = () => {
    if (!existingEncounter) return;
    onOpenChange(false);
    navigate(
      `/patients/${existingEncounter.patient_id}?tab=clinical&encounter=${existingEncounter.id}`,
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="encounter-launch-dialog"
        className="max-w-lg rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-display">
            <Stethoscope className="h-5 w-5 text-primary" />
            Launch clinical encounter
          </DialogTitle>
        </DialogHeader>

        {existingEncounter ? (
          <div className="space-y-3">
            <div
              data-testid="encounter-existing-banner"
              className="rounded-sm border border-primary/30 bg-primary/10 p-3 text-sm"
            >
              <div className="font-semibold text-foreground">
                Encounter already launched
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                Status:{" "}
                <Badge variant="outline" className="uppercase text-[10px]">
                  {existingEncounter.status.replace("_", " ")}
                </Badge>
                {" · "}Type:{" "}
                {ENCOUNTER_TYPES.find((t) => t.value === existingEncounter.encounter_type)?.label
                  || existingEncounter.encounter_type}
                {existingEncounter.is_exception && (
                  <>
                    {" · "}
                    <Badge variant="outline" className="bg-warning-soft text-warning text-[10px]">
                      Exception
                    </Badge>
                  </>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                className="rounded-sm"
              >
                Close
              </Button>
              <Button
                type="button"
                onClick={openExisting}
                data-testid="encounter-open-existing-btn"
                className="rounded-sm"
              >
                Open in chart
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-4">
            {isCancelled && (
              <div
                data-testid="encounter-exception-banner"
                className="rounded-sm border border-destructive/30 bg-destructive-soft p-3 text-sm"
              >
                <div className="font-semibold text-destructive">
                  This appointment is cancelled
                </div>
                <p className="mt-1 text-xs text-destructive/80">
                  Same-day documentation against a cancelled appointment requires a written exception reason. Only doctors and admins can launch an exception encounter.
                </p>
              </div>
            )}

            <div className="space-y-1">
              <Label>Encounter type</Label>
              <Select value={encounterType} onValueChange={setEncounterType}>
                <SelectTrigger data-testid="encounter-type-select" className="rounded-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ENCOUNTER_TYPES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <Label>Linked episode / case</Label>
              <Select value={episodeId} onValueChange={setEpisodeId}>
                <SelectTrigger data-testid="encounter-episode-select" className="rounded-sm">
                  <SelectValue placeholder="No link" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none">No link</SelectItem>
                  {episodes.map((ep) => (
                    <SelectItem key={ep.id} value={ep.id}>
                      {ep.title}
                      {ep.status !== "active" ? ` · ${ep.status}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                Episodes from this patient — any status. Leave unlinked if unsure.
              </p>
            </div>

            {isCancelled && (
              <div className="space-y-1">
                <Label>Exception reason</Label>
                <Textarea
                  rows={3}
                  value={exceptionReason}
                  onChange={(e) => setExceptionReason(e.target.value)}
                  data-testid="encounter-exception-reason"
                  className="rounded-sm"
                  placeholder="e.g. patient arrived anyway; treated same-day"
                />
              </div>
            )}

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
                type="button"
                onClick={submit}
                disabled={submitDisabled}
                data-testid="encounter-launch-submit-btn"
                className="rounded-sm"
              >
                {submitting ? "Launching…" : "Start visit"}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
