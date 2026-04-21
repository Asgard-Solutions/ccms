/**
 * EncountersCard — Phase 3 chart-visible encounters list.
 *
 * Shows encounters for the patient with appointment date + provider +
 * episode link + status. Links back out to the appointment and supports
 * complete/cancel transitions for in-progress encounters.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  AlertTriangle,
  Calendar,
  CheckCircle2,
  ExternalLink,
  Stethoscope,
  XCircle,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { Badge } from "../../components/ui/badge";
import { Textarea } from "../../components/ui/textarea";
import { Label } from "../../components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { formatDateTime } from "../../utils/time";

const ENCOUNTER_TYPE_LABEL = {
  new_patient_exam: "New patient exam",
  follow_up: "Follow-up / Adjustment",
  re_evaluation: "Re-evaluation",
  treatment_visit: "Treatment visit",
};

const STATUS_TONE = {
  in_progress: "border-primary/30 bg-primary/10 text-primary",
  completed: "border-success/30 bg-success-soft text-success",
  cancelled: "border-border bg-muted text-muted-foreground",
};

function CompleteDialog({ open, onOpenChange, encounter, onSubmit, submitting }) {
  const [notes, setNotes] = useState("");
  useEffect(() => {
    if (!open) setNotes("");
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="encounter-complete-dialog"
        className="max-w-md rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Complete encounter</DialogTitle>
        </DialogHeader>
        {encounter && (
          <div className="space-y-3">
            <div className="rounded-sm border border-border bg-muted/40 p-3 text-sm">
              <div className="font-semibold text-foreground">
                {ENCOUNTER_TYPE_LABEL[encounter.encounter_type] || encounter.encounter_type}
              </div>
              <div className="text-xs text-muted-foreground">
                {formatDateTime(encounter.date_of_service)}
              </div>
            </div>
            <div className="space-y-1">
              <Label>Closing notes (optional)</Label>
              <Textarea
                rows={3}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                data-testid="encounter-complete-notes"
                className="rounded-sm"
              />
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} className="rounded-sm">
            Cancel
          </Button>
          <Button
            disabled={submitting}
            onClick={() => onSubmit(notes.trim() || null)}
            data-testid="encounter-complete-submit-btn"
            className="rounded-sm"
          >
            {submitting ? "Completing…" : "Complete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CancelDialog({ open, onOpenChange, encounter, onSubmit, submitting }) {
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (!open) setReason("");
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="encounter-cancel-dialog"
        className="max-w-md rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Cancel encounter</DialogTitle>
        </DialogHeader>
        {encounter && (
          <div className="space-y-3">
            <div className="rounded-sm border border-border bg-muted/40 p-3 text-sm">
              Cancelling this encounter does <strong>not</strong> cancel the
              underlying appointment. Use this to abandon a mis-launched
              encounter.
            </div>
            <div className="space-y-1">
              <Label>Reason</Label>
              <Textarea
                rows={3}
                required
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                data-testid="encounter-cancel-reason"
                className="rounded-sm"
                placeholder="Launched on wrong appointment / duplicate launch / ..."
              />
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} className="rounded-sm">
            Keep it
          </Button>
          <Button
            variant="destructive"
            disabled={submitting || reason.trim().length < 3}
            onClick={() => onSubmit(reason.trim())}
            data-testid="encounter-cancel-submit-btn"
            className="rounded-sm"
          >
            {submitting ? "Cancelling…" : "Cancel encounter"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EncounterRow({ enc, canWrite, isHighlighted, onOpenAppt, onComplete, onCancel }) {
  const tone = STATUS_TONE[enc.status] || "border-border bg-muted";
  return (
    <div
      data-testid={`encounter-row-${enc.id}`}
      className={`rounded-lg border p-4 transition-colors ${
        isHighlighted ? "border-primary bg-primary/5" : "border-border bg-card"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-display text-base font-semibold text-foreground">
              {ENCOUNTER_TYPE_LABEL[enc.encounter_type] || enc.encounter_type}
            </span>
            <span
              className={`rounded-sm border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${tone}`}
              data-testid={`encounter-row-${enc.id}-status`}
            >
              {enc.status.replace("_", " ")}
            </span>
            {enc.is_exception && (
              <Badge
                variant="outline"
                className="border-warning/40 bg-warning-soft text-warning text-[10px]"
                data-testid={`encounter-row-${enc.id}-exception`}
              >
                <AlertTriangle className="mr-1 h-3 w-3" />
                Exception
              </Badge>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <Calendar className="h-3 w-3" />
              {formatDateTime(enc.date_of_service)}
            </span>
            {enc.scheduled_duration_min != null && (
              <span>{enc.scheduled_duration_min} min</span>
            )}
            {enc.provider_name && <span>Provider · {enc.provider_name}</span>}
            {enc.episode_title && <span>Episode · {enc.episode_title}</span>}
            <span>Status at launch · {enc.appointment_status_at_launch}</span>
          </div>
          {enc.exception_reason && (
            <p className="mt-2 text-xs italic text-warning">
              Exception reason: {enc.exception_reason}
            </p>
          )}
          {enc.cancelled_reason && (
            <p className="mt-2 text-xs italic text-muted-foreground">
              Cancelled: {enc.cancelled_reason}
            </p>
          )}
          {enc.notes && (
            <p className="mt-2 text-sm text-muted-foreground">{enc.notes}</p>
          )}
        </div>

        <div className="flex shrink-0 flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => onOpenAppt(enc)}
            data-testid={`encounter-open-appt-${enc.id}`}
            className="rounded-sm"
          >
            <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
            Appointment
          </Button>
          {canWrite && enc.status === "in_progress" && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onComplete(enc)}
                data-testid={`encounter-complete-${enc.id}`}
                className="rounded-sm"
              >
                <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
                Complete
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onCancel(enc)}
                data-testid={`encounter-cancel-${enc.id}`}
                className="rounded-sm"
              >
                <XCircle className="mr-1.5 h-3.5 w-3.5" />
                Cancel
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function EncountersCard({ patientId, canWrite, onReauthNeeded }) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [rows, setRows] = useState(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [completing, setCompleting] = useState(null);
  const [cancelling, setCancelling] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const highlightedId = searchParams.get("encounter");

  const load = useCallback(async () => {
    try {
      const params = statusFilter !== "all" ? { status_in: statusFilter } : {};
      const { data } = await api.get(`/patients/${patientId}/clinical/encounters`, { params });
      setRows(data);
    } catch (e) {
      toast.error(formatApiError(e));
      setRows([]);
    }
  }, [patientId, statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const handleReauthAware = (err) => {
    if (err?.response?.status === 401 && /re-auth/i.test(err.response?.data?.detail || "")) {
      onReauthNeeded?.();
      return true;
    }
    return false;
  };

  const handleComplete = async (notes) => {
    if (!completing) return;
    setSubmitting(true);
    try {
      await api.post(
        `/patients/${patientId}/clinical/encounters/${completing.id}/complete`,
        { notes },
      );
      toast.success("Encounter completed");
      setCompleting(null);
      load();
    } catch (e) {
      if (!handleReauthAware(e)) toast.error(formatApiError(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async (reason) => {
    if (!cancelling) return;
    setSubmitting(true);
    try {
      await api.post(
        `/patients/${patientId}/clinical/encounters/${cancelling.id}/cancel`,
        { reason },
      );
      toast.success("Encounter cancelled");
      setCancelling(null);
      load();
    } catch (e) {
      if (!handleReauthAware(e)) toast.error(formatApiError(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleOpenAppt = (enc) => {
    // The calendar reroutes by date; for Phase 3 we just open the scheduling
    // page on that date. Deep-linking into the appointment dialog is a
    // later polish item.
    const d = new Date(enc.date_of_service);
    const iso = d.toISOString().slice(0, 10);
    navigate(`/scheduling?date=${iso}`);
  };

  const emptyLabel = useMemo(() => {
    if (statusFilter === "in_progress") return "No in-progress encounters.";
    if (statusFilter === "completed") return "No completed encounters yet.";
    if (statusFilter === "cancelled") return "No cancelled encounters.";
    return "No encounters launched yet for this patient.";
  }, [statusFilter]);

  return (
    <section data-testid="clinical-encounters-card" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="font-display text-lg font-semibold text-foreground">
            Appointment-launched encounters
          </h3>
          <p className="text-sm text-muted-foreground">
            Every encounter started from a calendar appointment lands here. Complete or cancel from this card; the underlying appointment stays untouched.
          </p>
        </div>
        <div className="space-y-1">
          <Label className="text-xs uppercase tracking-wider text-muted-foreground">
            Status
          </Label>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger
              data-testid="encounter-filter-status"
              className="h-9 w-40 rounded-sm"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value="in_progress">In progress</SelectItem>
              <SelectItem value="completed">Completed</SelectItem>
              <SelectItem value="cancelled">Cancelled</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {rows === null ? (
        <div className="space-y-3">
          <Skeleton className="h-20 rounded-lg" />
          <Skeleton className="h-20 rounded-lg" />
        </div>
      ) : rows.length === 0 ? (
        <div
          data-testid="encounters-empty"
          className="rounded-lg border border-dashed border-border bg-card p-8 text-center"
        >
          <Stethoscope className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 font-display text-base font-semibold text-foreground">
            {emptyLabel}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            Launch an encounter from the appointment details dialog to start documentation.
          </p>
        </div>
      ) : (
        <div data-testid="encounters-list" className="space-y-3">
          {rows.map((enc) => (
            <EncounterRow
              key={enc.id}
              enc={enc}
              canWrite={canWrite}
              isHighlighted={enc.id === highlightedId}
              onOpenAppt={handleOpenAppt}
              onComplete={setCompleting}
              onCancel={setCancelling}
            />
          ))}
        </div>
      )}

      <CompleteDialog
        open={!!completing}
        onOpenChange={(v) => !v && setCompleting(null)}
        encounter={completing}
        onSubmit={handleComplete}
        submitting={submitting}
      />
      <CancelDialog
        open={!!cancelling}
        onOpenChange={(v) => !v && setCancelling(null)}
        encounter={cancelling}
        onSubmit={handleCancel}
        submitting={submitting}
      />
    </section>
  );
}
