/**
 * DiagnosesCard — Phase 2 problem list.
 *
 * List + create + edit + resolve + reactivate. Each diagnosis can optionally
 * link to any episode (active, on-hold, or closed — for historical cleanup
 * and prior-injury tagging).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, Pencil, PlayCircle, PlusCircle, Star } from "lucide-react";
import { api, formatApiError } from "../../api/client";
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
import { formatDate } from "../../utils/time";
import { CASE_TYPES } from "./ClinicalTab";

const LATERALITY_OPTIONS = [
  { value: "left", label: "Left" },
  { value: "right", label: "Right" },
  { value: "bilateral", label: "Bilateral" },
  { value: "midline", label: "Midline" },
];

const CHRONICITY_OPTIONS = [
  { value: "acute", label: "Acute" },
  { value: "subacute", label: "Subacute" },
  { value: "chronic", label: "Chronic" },
];

const EMPTY = {
  icd10_code: "",
  label: "",
  episode_id: "",
  is_primary: false,
  body_region: "",
  laterality: "",
  chronicity: "",
  onset_date: "",
  notes: "",
};

function episodeLabel(ep) {
  const t = CASE_TYPES.find((c) => c.value === ep.case_type)?.label || ep.case_type;
  return `${ep.title} · ${t}`;
}

function DiagnosisDialog({ open, onOpenChange, initial, episodes, onSubmit, submitting }) {
  const [form, setForm] = useState(EMPTY);

  useEffect(() => {
    if (!open) return;
    setForm({
      ...EMPTY,
      ...(initial || {}),
      icd10_code: initial?.icd10_code || "",
      label: initial?.label || "",
      episode_id: initial?.episode_id || "",
      is_primary: !!initial?.is_primary,
      body_region: initial?.body_region || "",
      laterality: initial?.laterality || "",
      chronicity: initial?.chronicity || "",
      onset_date: initial?.onset_date || "",
      notes: initial?.notes || "",
    });
  }, [open, initial]);

  const submit = async (e) => {
    e.preventDefault();
    if (!form.icd10_code.trim() || !form.label.trim()) {
      toast.error("ICD-10 code and label are required");
      return;
    }
    const body = {
      icd10_code: form.icd10_code.trim().toUpperCase(),
      label: form.label.trim(),
      is_primary: !!form.is_primary,
    };
    if (form.episode_id) body.episode_id = form.episode_id;
    else if (initial && initial.episode_id && !form.episode_id) body.episode_id = null;
    if (form.body_region.trim()) body.body_region = form.body_region.trim();
    else if (initial?.body_region) body.body_region = null;
    if (form.laterality) body.laterality = form.laterality;
    else if (initial?.laterality) body.laterality = null;
    if (form.chronicity) body.chronicity = form.chronicity;
    else if (initial?.chronicity) body.chronicity = null;
    if (form.onset_date) body.onset_date = form.onset_date;
    else if (initial?.onset_date) body.onset_date = null;
    if (form.notes.trim()) body.notes = form.notes.trim();
    else if (initial?.notes) body.notes = null;
    await onSubmit(body);
  };

  const isEdit = !!initial?.id;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="diagnosis-dialog" className="max-w-xl rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">
            {isEdit ? "Edit diagnosis" : "Add diagnosis"}
          </DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid grid-cols-[1fr_2fr] gap-3">
            <div className="space-y-1">
              <Label>ICD-10 code</Label>
              <Input
                required
                placeholder="M54.50"
                value={form.icd10_code}
                onChange={(e) => setForm({ ...form, icd10_code: e.target.value.toUpperCase() })}
                data-testid="dx-icd10"
                className="rounded-sm font-mono uppercase"
              />
            </div>
            <div className="space-y-1">
              <Label>Label</Label>
              <Input
                required
                placeholder="Low back pain, unspecified"
                value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                data-testid="dx-label"
                className="rounded-sm"
              />
            </div>
          </div>

          <div className="space-y-1">
            <Label>Linked episode / case</Label>
            <Select
              value={form.episode_id || "__none"}
              onValueChange={(v) => setForm({ ...form, episode_id: v === "__none" ? "" : v })}
            >
              <SelectTrigger data-testid="dx-episode" className="rounded-sm">
                <SelectValue placeholder="No link" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none">No link</SelectItem>
                {(episodes || []).map((ep) => (
                  <SelectItem key={ep.id} value={ep.id}>
                    {episodeLabel(ep)} {ep.status !== "active" ? `· ${ep.status}` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>Body region</Label>
              <Input
                placeholder="Lumbar spine, cervical, shoulder…"
                value={form.body_region}
                onChange={(e) => setForm({ ...form, body_region: e.target.value })}
                data-testid="dx-body-region"
                className="rounded-sm"
              />
            </div>
            <div className="space-y-1">
              <Label>Onset date</Label>
              <Input
                type="date"
                value={form.onset_date}
                onChange={(e) => setForm({ ...form, onset_date: e.target.value })}
                data-testid="dx-onset"
                className="rounded-sm"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>Laterality</Label>
              <Select
                value={form.laterality || "__none"}
                onValueChange={(v) => setForm({ ...form, laterality: v === "__none" ? "" : v })}
              >
                <SelectTrigger data-testid="dx-laterality" className="rounded-sm">
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none">—</SelectItem>
                  {LATERALITY_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>Chronicity</Label>
              <Select
                value={form.chronicity || "__none"}
                onValueChange={(v) => setForm({ ...form, chronicity: v === "__none" ? "" : v })}
              >
                <SelectTrigger data-testid="dx-chronicity" className="rounded-sm">
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none">—</SelectItem>
                  {CHRONICITY_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={!!form.is_primary}
              onChange={(e) => setForm({ ...form, is_primary: e.target.checked })}
              data-testid="dx-is-primary"
              className="h-4 w-4"
            />
            <span>Primary diagnosis for this episode</span>
          </label>

          <div className="space-y-1">
            <Label>Notes</Label>
            <Textarea
              rows={3}
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              data-testid="dx-notes"
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
              data-testid="dx-submit-btn"
              className="rounded-sm"
            >
              {submitting ? "Saving…" : isEdit ? "Save changes" : "Add diagnosis"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DiagnosisRow({ dx, episodes, canWrite, onEdit, onResolve, onReactivate }) {
  const linkedEpisode = episodes.find((ep) => ep.id === dx.episode_id);
  return (
    <div
      data-testid={`dx-row-${dx.id}`}
      className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border bg-card p-4"
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-semibold text-foreground">
            {dx.icd10_code}
          </span>
          <span className="text-sm text-foreground">{dx.label}</span>
          {dx.is_primary && (
            <Badge
              variant="outline"
              className="border-warning/40 bg-warning-soft text-warning text-[10px]"
              data-testid={`dx-primary-${dx.id}`}
            >
              <Star className="mr-1 h-3 w-3" />
              Primary
            </Badge>
          )}
          <Badge
            variant="outline"
            className={`text-[10px] uppercase ${
              dx.status === "resolved"
                ? "border-border bg-muted text-muted-foreground"
                : "border-success/30 bg-success-soft text-success"
            }`}
          >
            {dx.status}
          </Badge>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {dx.body_region && <span>Region · {dx.body_region}</span>}
          {dx.laterality && <span>Laterality · {dx.laterality}</span>}
          {dx.chronicity && <span>Chronicity · {dx.chronicity}</span>}
          {dx.onset_date && <span>Onset · {formatDate(dx.onset_date)}</span>}
          {dx.resolved_date && <span>Resolved · {formatDate(dx.resolved_date)}</span>}
          {linkedEpisode && <span>Episode · {linkedEpisode.title}</span>}
        </div>
        {dx.notes && <p className="mt-2 text-sm text-muted-foreground">{dx.notes}</p>}
        {dx.resolution_notes && (
          <p className="mt-1 text-xs italic text-muted-foreground">
            Resolution: {dx.resolution_notes}
          </p>
        )}
      </div>

      {canWrite && (
        <div className="flex shrink-0 gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => onEdit(dx)}
            data-testid={`dx-edit-${dx.id}`}
            className="rounded-sm"
          >
            <Pencil className="mr-1.5 h-3.5 w-3.5" />
            Edit
          </Button>
          {dx.status === "active" ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onResolve(dx)}
              data-testid={`dx-resolve-${dx.id}`}
              className="rounded-sm"
            >
              <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
              Resolve
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onReactivate(dx)}
              data-testid={`dx-reactivate-${dx.id}`}
              className="rounded-sm"
            >
              <PlayCircle className="mr-1.5 h-3.5 w-3.5" />
              Reactivate
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function ResolveDialog({ open, onOpenChange, dx, onSubmit, submitting }) {
  const [notes, setNotes] = useState("");
  useEffect(() => {
    if (!open) setNotes("");
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="dx-resolve-dialog" className="max-w-md rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Resolve diagnosis</DialogTitle>
        </DialogHeader>
        {dx && (
          <div className="space-y-3">
            <div className="rounded-sm border border-border bg-muted/40 p-3 text-sm">
              <span className="font-mono font-semibold">{dx.icd10_code}</span> · {dx.label}
            </div>
            <div className="space-y-1">
              <Label>Resolution notes (optional)</Label>
              <Textarea
                rows={3}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                data-testid="dx-resolve-notes"
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
            data-testid="dx-resolve-submit-btn"
            className="rounded-sm"
          >
            {submitting ? "Resolving…" : "Resolve"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function DiagnosesCard({ patientId, episodes = [], canWrite, onReauthNeeded }) {
  const [rows, setRows] = useState(null);
  const [statusFilter, setStatusFilter] = useState("active");
  const [episodeFilter, setEpisodeFilter] = useState("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [resolving, setResolving] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    try {
      const params = {};
      if (statusFilter !== "all") params.status_in = statusFilter;
      if (episodeFilter !== "all") params.episode_id = episodeFilter;
      const { data } = await api.get(`/patients/${patientId}/clinical/diagnoses`, { params });
      setRows(data);
    } catch (e) {
      toast.error(formatApiError(e));
      setRows([]);
    }
  }, [patientId, statusFilter, episodeFilter]);

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

  const handleCreate = async (body) => {
    setSubmitting(true);
    try {
      await api.post(`/patients/${patientId}/clinical/diagnoses`, body);
      toast.success("Diagnosis added");
      setCreateOpen(false);
      load();
    } catch (e) {
      if (!handleReauthAware(e)) toast.error(formatApiError(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleEdit = async (body) => {
    if (!editing) return;
    setSubmitting(true);
    try {
      await api.patch(`/patients/${patientId}/clinical/diagnoses/${editing.id}`, body);
      toast.success("Diagnosis updated");
      setEditing(null);
      load();
    } catch (e) {
      if (!handleReauthAware(e)) toast.error(formatApiError(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleResolve = async (notes) => {
    if (!resolving) return;
    setSubmitting(true);
    try {
      await api.post(
        `/patients/${patientId}/clinical/diagnoses/${resolving.id}/resolve`,
        { resolution_notes: notes },
      );
      toast.success("Diagnosis resolved");
      setResolving(null);
      load();
    } catch (e) {
      if (!handleReauthAware(e)) toast.error(formatApiError(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleReactivate = async (dx) => {
    try {
      await api.post(
        `/patients/${patientId}/clinical/diagnoses/${dx.id}/reactivate`,
      );
      toast.success("Diagnosis reactivated");
      load();
    } catch (e) {
      if (!handleReauthAware(e)) toast.error(formatApiError(e));
    }
  };

  const episodeOptions = useMemo(
    () => [
      { id: "all", label: "All episodes" },
      { id: "__orphan", label: "Not linked to an episode (use server-side note)" },
      ...episodes.map((ep) => ({ id: ep.id, label: episodeLabel(ep) })),
    ],
    [episodes],
  );

  return (
    <section data-testid="clinical-diagnoses-card" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="font-display text-lg font-semibold text-foreground">
            Diagnoses &amp; Problem List
          </h3>
          <p className="text-sm text-muted-foreground">
            ICD-10 coded problems. Link to any episode — including closed ones — for chart cleanup and historical tagging.
          </p>
        </div>
        {canWrite && (
          <Button
            size="sm"
            onClick={() => setCreateOpen(true)}
            data-testid="dx-new-btn"
            className="rounded-sm"
          >
            <PlusCircle className="mr-1.5 h-4 w-4" />
            Add diagnosis
          </Button>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label className="text-xs uppercase tracking-wider text-muted-foreground">Status</Label>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger data-testid="dx-filter-status" className="h-9 w-40 rounded-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="resolved">Resolved</SelectItem>
              <SelectItem value="all">All</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {episodes.length > 0 && (
          <div className="space-y-1">
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">Episode</Label>
            <Select
              value={episodeFilter}
              onValueChange={(v) => setEpisodeFilter(v === "__orphan" ? "all" : v)}
            >
              <SelectTrigger data-testid="dx-filter-episode" className="h-9 w-72 rounded-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {episodeOptions.map((o) => (
                  <SelectItem key={o.id} value={o.id}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>

      {rows === null ? (
        <div className="space-y-3">
          <Skeleton className="h-16 rounded-lg" />
          <Skeleton className="h-16 rounded-lg" />
        </div>
      ) : rows.length === 0 ? (
        <div
          data-testid="dx-empty"
          className="rounded-lg border border-dashed border-border bg-card p-6 text-center text-sm text-muted-foreground"
        >
          No diagnoses matching the current filter.
        </div>
      ) : (
        <div data-testid="dx-list" className="space-y-2">
          {rows.map((dx) => (
            <DiagnosisRow
              key={dx.id}
              dx={dx}
              episodes={episodes}
              canWrite={canWrite}
              onEdit={setEditing}
              onResolve={setResolving}
              onReactivate={handleReactivate}
            />
          ))}
        </div>
      )}

      <DiagnosisDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        episodes={episodes}
        onSubmit={handleCreate}
        submitting={submitting}
      />
      <DiagnosisDialog
        open={!!editing}
        onOpenChange={(v) => !v && setEditing(null)}
        initial={editing}
        episodes={episodes}
        onSubmit={handleEdit}
        submitting={submitting}
      />
      <ResolveDialog
        open={!!resolving}
        onOpenChange={(v) => !v && setResolving(null)}
        dx={resolving}
        onSubmit={handleResolve}
        submitting={submitting}
      />
    </section>
  );
}
