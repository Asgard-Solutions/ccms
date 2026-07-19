/**
 * Episode create/close dialog components — extracted so both the legacy
 * ClinicalTab and the redesigned ClinicalTabV2 can use them.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api, formatApiError } from "../../api/client";
import { Button } from "../../components/ui/button";
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
import { formatDate } from "../../utils/time";

export const CASE_TYPES = [
  { value: "new_patient_eval", label: "New patient evaluation" },
  { value: "injury_episode", label: "Injury episode" },
  { value: "recurrence", label: "Recurrence / Flare-up" },
  { value: "maintenance", label: "Maintenance / Wellness" },
  { value: "mva", label: "Motor vehicle accident" },
  { value: "workers_comp", label: "Workers compensation" },
  { value: "personal_injury", label: "Personal injury" },
];

function caseTypeLabel(value) {
  return CASE_TYPES.find((c) => c.value === value)?.label || value;
}

export function EpisodeCreateDialog({
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
      const body = { case_type: form.case_type, title: form.title.trim() };
      if (form.chief_complaint.trim()) body.chief_complaint = form.chief_complaint.trim();
      if (form.mechanism_of_injury.trim())
        body.mechanism_of_injury = form.mechanism_of_injury.trim();
      if (form.onset_date) body.onset_date = form.onset_date;
      if (form.responsible_provider_id)
        body.responsible_provider_id = form.responsible_provider_id;

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
              <SelectTrigger id="case-type" data-testid="episode-case-type" className="rounded-sm">
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
                <SelectTrigger id="episode-provider" data-testid="episode-provider" className="rounded-sm">
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

export function EpisodeCloseDialog({
  open,
  onOpenChange,
  episode,
  patientId,
  onClosed,
  onReauthNeeded,
}) {
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
          <Button variant="outline" onClick={() => onOpenChange(false)} className="rounded-sm">
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
