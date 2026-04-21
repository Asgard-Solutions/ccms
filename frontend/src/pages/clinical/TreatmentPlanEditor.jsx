/**
 * TreatmentPlanEditor — full-page plan-of-care authoring.
 *
 * Route: /patients/:pid/clinical/treatment-plans/:tpid
 *
 * Chart-level artifact. Sections: Overview · Diagnoses & regions ·
 * Frequency & duration · Interventions · Goals · Baselines · Home-care ·
 * Activity/work · Discharge criteria · Maintenance notes.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowLeft,
  Loader2,
  PlusCircle,
  Save,
  Trash2,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../../components/ui/select";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "../../components/ui/dialog";
import { useAuth } from "../../contexts/AuthContext";
import { useReauth } from "../../components/ReauthGate";
import { formatDateTime } from "../../utils/time";

const STATUS_BADGE = {
  active: { label: "Active", class: "border-success/40 bg-success-soft text-success" },
  on_hold: { label: "On hold", class: "border-warning/40 bg-warning-soft text-warning" },
  completed: { label: "Completed", class: "border-primary/30 bg-primary/10 text-primary" },
  discharged: { label: "Discharged", class: "border-border bg-muted text-muted-foreground" },
  cancelled: { label: "Cancelled", class: "border-destructive/30 bg-destructive/10 text-destructive" },
};

const INTERVENTION_KINDS = [
  "adjustment", "modality", "soft_tissue", "exercise", "education", "other",
];
const MEASURE_TYPES = [
  { value: "pain_scale", label: "Pain scale (0–10)" },
  { value: "functional", label: "Functional" },
  { value: "rom", label: "Range of motion" },
  { value: "outcome_score", label: "Outcome score" },
  { value: "custom", label: "Custom" },
];
const GOAL_STATUS = ["active", "met", "modified", "abandoned"];
const NEW_STATUSES = ["on_hold", "active", "completed", "discharged", "cancelled"];

function SectionCard({ title, description, children, testId }) {
  return (
    <section
      data-testid={testId}
      className="rounded-lg border border-border bg-card p-5"
    >
      <div className="mb-4">
        <h3 className="font-display text-lg font-semibold text-foreground">{title}</h3>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

export default function TreatmentPlanEditor() {
  const { pid, tpid } = useParams();
  const { user } = useAuth();
  const { requestReauth } = useReauth();
  const canWrite = ["admin", "doctor"].includes(user?.role);

  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [statusDialog, setStatusDialog] = useState(null);

  const [form, setForm] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get(
        `/patients/${pid}/clinical/treatment-plans/${tpid}`,
      );
      setPlan(data);
      setForm({
        title: data.title || "",
        responsible_provider_id: data.responsible_provider_id || null,
        diagnosis_ids: data.diagnosis_ids || [],
        target_body_regions: data.target_body_regions || [],
        frequency_visits_per_week: data.frequency_visits_per_week ?? null,
        frequency_total_visits: data.frequency_total_visits ?? null,
        expected_duration_weeks: data.expected_duration_weeks ?? null,
        re_exam_date: data.re_exam_date || "",
        planned_interventions: data.planned_interventions || [],
        goals: data.goals || [],
        baselines: data.baselines || {},
        home_care_recommendations: data.home_care_recommendations || "",
        activity_work_recommendations: data.activity_work_recommendations || "",
        discharge_criteria: data.discharge_criteria || "",
        maintenance_transition_notes: data.maintenance_transition_notes || "",
      });
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [pid, tpid]);

  useEffect(() => {
    load();
  }, [load]);

  const dirty = useMemo(() => {
    if (!plan || !form) return false;
    return JSON.stringify(form) !== JSON.stringify({
      title: plan.title || "",
      responsible_provider_id: plan.responsible_provider_id || null,
      diagnosis_ids: plan.diagnosis_ids || [],
      target_body_regions: plan.target_body_regions || [],
      frequency_visits_per_week: plan.frequency_visits_per_week ?? null,
      frequency_total_visits: plan.frequency_total_visits ?? null,
      expected_duration_weeks: plan.expected_duration_weeks ?? null,
      re_exam_date: plan.re_exam_date || "",
      planned_interventions: plan.planned_interventions || [],
      goals: plan.goals || [],
      baselines: plan.baselines || {},
      home_care_recommendations: plan.home_care_recommendations || "",
      activity_work_recommendations: plan.activity_work_recommendations || "",
      discharge_criteria: plan.discharge_criteria || "",
      maintenance_transition_notes: plan.maintenance_transition_notes || "",
    });
  }, [plan, form]);

  const readOnly = !canWrite || ["discharged", "completed", "cancelled"].includes(plan?.plan_status);

  const runReauthAware = async (fn) => {
    try {
      return await fn();
    } catch (e) {
      if (
        e?.response?.status === 401 &&
        /re-auth/i.test(e.response?.data?.detail || "")
      ) {
        requestReauth(async () => {
          try {
            await fn();
            await load();
          } catch (err) {
            toast.error(formatApiError(err));
          }
        });
        return null;
      }
      throw e;
    }
  };

  const save = async () => {
    if (!form || saving || readOnly) return;
    setSaving(true);
    try {
      await runReauthAware(async () => {
        const body = { ...form };
        // Normalize empty strings to null
        Object.keys(body).forEach((k) => {
          if (body[k] === "") body[k] = null;
        });
        const { data } = await api.patch(
          `/patients/${pid}/clinical/treatment-plans/${tpid}`,
          body,
        );
        setPlan(data);
        toast.success("Plan saved");
      });
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setSaving(false);
    }
  };

  const changeStatus = async (next, reason) => {
    try {
      await runReauthAware(async () => {
        const { data } = await api.post(
          `/patients/${pid}/clinical/treatment-plans/${tpid}/set-status`,
          { plan_status: next, reason },
        );
        setPlan(data);
        toast.success(`Plan moved to ${next.replace("_", " ")}`);
        setStatusDialog(null);
      });
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  if (loading || !form) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!plan) {
    return <div className="p-6 text-sm text-muted-foreground">Plan not found.</div>;
  }
  const badge = STATUS_BADGE[plan.plan_status] || STATUS_BADGE.active;
  const pg = plan.progress || {};

  return (
    <>
      <div data-testid="treatment-plan-editor" className="mx-auto max-w-4xl space-y-6 p-4 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <Link
              to={`/patients/${pid}?tab=clinical`}
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              data-testid="plan-back-link"
            >
              <ArrowLeft className="h-3 w-3" /> Back to chart
            </Link>
            <h1 className="mt-1 font-display text-2xl font-semibold text-foreground">
              Treatment Plan
            </h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>Started {formatDateTime(plan.start_date)}</span>
              {plan.responsible_provider_name && (
                <span>Provider · {plan.responsible_provider_name}</span>
              )}
              {plan.episode_title && <span>Episode · {plan.episode_title}</span>}
              <Badge
                variant="outline"
                data-testid="plan-status-badge"
                className={`text-[10px] uppercase tracking-wider ${badge.class}`}
              >
                {badge.label}
              </Badge>
            </div>
            {plan.discharge_reason && (
              <p data-testid="plan-discharge-reason" className="mt-1 text-xs text-muted-foreground">
                Discharge reason · {plan.discharge_reason}
              </p>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {canWrite && !readOnly && (
              <Button
                size="sm"
                onClick={save}
                disabled={saving || !dirty}
                data-testid="plan-save-btn"
                className="rounded-sm"
              >
                <Save className="mr-1.5 h-3.5 w-3.5" />
                {saving ? "Saving…" : dirty ? "Save plan" : "Saved"}
              </Button>
            )}
            {canWrite && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setStatusDialog({ next: null, reason: "" })}
                data-testid="plan-set-status-btn"
                className="rounded-sm"
              >
                Change status
              </Button>
            )}
          </div>
        </div>

        {/* Progress bar */}
        <div
          data-testid="plan-progress"
          className="rounded-lg border border-border bg-card p-4"
        >
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>Visit progress</span>
            <span data-testid="plan-progress-text">
              {pg.visits_completed ?? 0}/{pg.total_visits ?? "—"} · {pg.percent ?? 0}%
            </span>
          </div>
          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all"
              style={{ width: `${pg.percent ?? 0}%` }}
            />
          </div>
        </div>

        {/* Overview */}
        <SectionCard title="Overview" testId="plan-section-overview">
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">Plan title</Label>
            <Input
              value={form.title}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              readOnly={readOnly}
              data-testid="plan-title"
              className="rounded-sm"
            />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Re-exam date
              </Label>
              <Input
                type="date"
                value={form.re_exam_date || ""}
                onChange={(e) => setForm((f) => ({ ...f, re_exam_date: e.target.value }))}
                readOnly={readOnly}
                data-testid="plan-reexam-date"
                className="rounded-sm"
              />
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Visits / week
              </Label>
              <Input
                type="number"
                min={0}
                max={14}
                value={form.frequency_visits_per_week ?? ""}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    frequency_visits_per_week:
                      e.target.value === "" ? null : Number(e.target.value),
                  }))
                }
                readOnly={readOnly}
                data-testid="plan-freq-week"
                className="rounded-sm"
              />
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Total visits
              </Label>
              <Input
                type="number"
                min={0}
                max={500}
                value={form.frequency_total_visits ?? ""}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    frequency_total_visits:
                      e.target.value === "" ? null : Number(e.target.value),
                  }))
                }
                readOnly={readOnly}
                data-testid="plan-total-visits"
                className="rounded-sm"
              />
            </div>
          </div>
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Expected duration (weeks)
            </Label>
            <Input
              type="number"
              min={0}
              max={260}
              value={form.expected_duration_weeks ?? ""}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  expected_duration_weeks:
                    e.target.value === "" ? null : Number(e.target.value),
                }))
              }
              readOnly={readOnly}
              data-testid="plan-duration-weeks"
              className="rounded-sm max-w-[180px]"
            />
          </div>
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Target body regions
            </Label>
            <Input
              placeholder="cervical, lumbar, right shoulder"
              value={(form.target_body_regions || []).join(", ")}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  target_body_regions: e.target.value
                    .split(",").map((s) => s.trim()).filter(Boolean),
                }))
              }
              readOnly={readOnly}
              data-testid="plan-target-regions"
              className="rounded-sm"
            />
          </div>
        </SectionCard>

        {/* Interventions */}
        <SectionCard
          title="Planned interventions"
          description="What care this plan calls for."
          testId="plan-section-interventions"
        >
          <InterventionEditor
            value={form.planned_interventions}
            onChange={(v) => setForm((f) => ({ ...f, planned_interventions: v }))}
            readOnly={readOnly}
          />
        </SectionCard>

        {/* Goals */}
        <SectionCard
          title="Measurable goals"
          description="Each goal becomes a row in the Re-Exam's goal-progress tracker."
          testId="plan-section-goals"
        >
          <GoalEditor
            value={form.goals}
            onChange={(v) => setForm((f) => ({ ...f, goals: v }))}
            readOnly={readOnly}
          />
        </SectionCard>

        {/* Baselines */}
        <SectionCard
          title="Objective baselines"
          description="Captured at plan start; frozen into each Re-Exam for comparison."
          testId="plan-section-baselines"
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Pain scale (0–10)
              </Label>
              <Input
                type="number"
                min={0}
                max={10}
                value={form.baselines?.pain_scale_0_10 ?? ""}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    baselines: {
                      ...(f.baselines || {}),
                      pain_scale_0_10:
                        e.target.value === "" ? null : Number(e.target.value),
                    },
                  }))
                }
                readOnly={readOnly}
                data-testid="plan-baseline-pain"
                className="rounded-sm"
              />
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Key ROM summary
              </Label>
              <Input
                value={form.baselines?.key_rom_summary || ""}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    baselines: { ...(f.baselines || {}), key_rom_summary: e.target.value || null },
                  }))
                }
                readOnly={readOnly}
                data-testid="plan-baseline-rom"
                className="rounded-sm"
              />
            </div>
          </div>
          <FunctionalMeasuresEditor
            value={form.baselines?.functional_measures || []}
            onChange={(v) =>
              setForm((f) => ({
                ...f,
                baselines: { ...(f.baselines || {}), functional_measures: v },
              }))
            }
            readOnly={readOnly}
          />
        </SectionCard>

        {/* Home care, activity, discharge */}
        <SectionCard title="Home-care & activity guidance" testId="plan-section-recommendations">
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Home-care recommendations
            </Label>
            <Textarea
              rows={3}
              value={form.home_care_recommendations}
              onChange={(e) => setForm((f) => ({ ...f, home_care_recommendations: e.target.value }))}
              readOnly={readOnly}
              data-testid="plan-home-care"
              className="rounded-sm"
            />
          </div>
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Activity / work recommendations
            </Label>
            <Textarea
              rows={3}
              value={form.activity_work_recommendations}
              onChange={(e) => setForm((f) => ({ ...f, activity_work_recommendations: e.target.value }))}
              readOnly={readOnly}
              data-testid="plan-activity-work"
              className="rounded-sm"
            />
          </div>
        </SectionCard>

        <SectionCard title="Discharge & maintenance" testId="plan-section-discharge">
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Discharge criteria
            </Label>
            <Textarea
              rows={3}
              value={form.discharge_criteria}
              onChange={(e) => setForm((f) => ({ ...f, discharge_criteria: e.target.value }))}
              readOnly={readOnly}
              data-testid="plan-discharge-criteria"
              className="rounded-sm"
            />
          </div>
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Maintenance / wellness transition notes
            </Label>
            <Textarea
              rows={3}
              value={form.maintenance_transition_notes}
              onChange={(e) => setForm((f) => ({ ...f, maintenance_transition_notes: e.target.value }))}
              readOnly={readOnly}
              data-testid="plan-maintenance-notes"
              className="rounded-sm"
            />
          </div>
        </SectionCard>
      </div>

      <SetStatusDialog
        open={!!statusDialog}
        current={plan.plan_status}
        value={statusDialog || {}}
        onChange={setStatusDialog}
        onSubmit={(next, reason) => changeStatus(next, reason)}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Sub-editors
// ---------------------------------------------------------------------------
function InterventionEditor({ value, onChange, readOnly }) {
  const rows = value || [];
  const update = (i, k, v) => onChange(rows.map((r, idx) => (idx === i ? { ...r, [k]: v || null } : r)));
  const add = () => onChange([...rows, { kind: "adjustment", description: "" }]);
  const remove = (i) => onChange(rows.filter((_, idx) => idx !== i));
  return (
    <div data-testid="plan-interventions" className="space-y-2">
      {rows.map((r, i) => (
        <div
          key={i}
          data-testid={`plan-intervention-row-${i}`}
          className="grid grid-cols-1 gap-2 rounded-sm border border-border bg-muted/20 p-2 sm:grid-cols-[160px_1fr_180px_36px]"
        >
          <Select
            value={r.kind}
            onValueChange={(v) => update(i, "kind", v)}
            disabled={readOnly}
          >
            <SelectTrigger data-testid={`plan-intervention-${i}-kind`} className="rounded-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {INTERVENTION_KINDS.map((k) => (
                <SelectItem key={k} value={k}>{k.replace("_", " ")}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder="Description"
            value={r.description || ""}
            onChange={(e) => update(i, "description", e.target.value)}
            readOnly={readOnly}
            data-testid={`plan-intervention-${i}-desc`}
            className="rounded-sm"
          />
          <Input
            placeholder="Frequency (e.g. 2x/wk x 4 wk)"
            value={r.frequency || ""}
            onChange={(e) => update(i, "frequency", e.target.value)}
            readOnly={readOnly}
            data-testid={`plan-intervention-${i}-freq`}
            className="rounded-sm"
          />
          {!readOnly && (
            <Button
              size="icon"
              variant="ghost"
              onClick={() => remove(i)}
              data-testid={`plan-intervention-${i}-remove`}
              className="h-9 w-9 rounded-sm"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      ))}
      {!readOnly && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={add}
          data-testid="plan-intervention-add"
          className="rounded-sm"
        >
          <PlusCircle className="mr-1.5 h-3.5 w-3.5" /> Add intervention
        </Button>
      )}
    </div>
  );
}

function GoalEditor({ value, onChange, readOnly }) {
  const rows = value || [];
  const update = (i, k, v) =>
    onChange(rows.map((r, idx) => (idx === i ? { ...r, [k]: v === "" ? null : v } : r)));
  const add = () =>
    onChange([
      ...rows,
      { description: "", measure_type: "pain_scale", status: "active" },
    ]);
  const remove = (i) => onChange(rows.filter((_, idx) => idx !== i));
  return (
    <div data-testid="plan-goals" className="space-y-2">
      {rows.map((g, i) => (
        <div
          key={g.id || i}
          data-testid={`plan-goal-row-${i}`}
          className="grid grid-cols-1 gap-2 rounded-sm border border-border bg-muted/20 p-2 sm:grid-cols-[1fr_140px_140px_140px_120px_36px]"
        >
          <Input
            placeholder="Goal description"
            value={g.description || ""}
            onChange={(e) => update(i, "description", e.target.value)}
            readOnly={readOnly}
            data-testid={`plan-goal-${i}-desc`}
            className="rounded-sm"
          />
          <Select
            value={g.measure_type}
            onValueChange={(v) => update(i, "measure_type", v)}
            disabled={readOnly}
          >
            <SelectTrigger data-testid={`plan-goal-${i}-measure`} className="rounded-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {MEASURE_TYPES.map((m) => (
                <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder="Baseline"
            value={g.baseline_value ?? ""}
            onChange={(e) => update(i, "baseline_value", e.target.value)}
            readOnly={readOnly}
            data-testid={`plan-goal-${i}-baseline`}
            className="rounded-sm"
          />
          <Input
            placeholder="Target"
            value={g.target_value ?? ""}
            onChange={(e) => update(i, "target_value", e.target.value)}
            readOnly={readOnly}
            data-testid={`plan-goal-${i}-target`}
            className="rounded-sm"
          />
          <Select
            value={g.status || "active"}
            onValueChange={(v) => update(i, "status", v)}
            disabled={readOnly}
          >
            <SelectTrigger data-testid={`plan-goal-${i}-status`} className="rounded-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {GOAL_STATUS.map((s) => (
                <SelectItem key={s} value={s}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {!readOnly && (
            <Button
              size="icon"
              variant="ghost"
              onClick={() => remove(i)}
              data-testid={`plan-goal-${i}-remove`}
              className="h-9 w-9 rounded-sm"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      ))}
      {!readOnly && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={add}
          data-testid="plan-goal-add"
          className="rounded-sm"
        >
          <PlusCircle className="mr-1.5 h-3.5 w-3.5" /> Add goal
        </Button>
      )}
    </div>
  );
}

function FunctionalMeasuresEditor({ value, onChange, readOnly }) {
  const rows = value || [];
  const update = (i, k, v) => onChange(rows.map((r, idx) => (idx === i ? { ...r, [k]: v || null } : r)));
  const add = () => onChange([...rows, { label: "", value: "", unit: "" }]);
  const remove = (i) => onChange(rows.filter((_, idx) => idx !== i));
  return (
    <div data-testid="plan-functional-measures" className="space-y-2">
      {rows.map((r, i) => (
        <div
          key={i}
          data-testid={`plan-fm-row-${i}`}
          className="grid grid-cols-1 gap-2 rounded-sm border border-border bg-muted/20 p-2 sm:grid-cols-[1fr_160px_120px_36px]"
        >
          <Input
            placeholder="Label (e.g. Oswestry Index)"
            value={r.label || ""}
            onChange={(e) => update(i, "label", e.target.value)}
            readOnly={readOnly}
            data-testid={`plan-fm-${i}-label`}
            className="rounded-sm"
          />
          <Input
            placeholder="Value"
            value={r.value || ""}
            onChange={(e) => update(i, "value", e.target.value)}
            readOnly={readOnly}
            data-testid={`plan-fm-${i}-value`}
            className="rounded-sm"
          />
          <Input
            placeholder="Unit"
            value={r.unit || ""}
            onChange={(e) => update(i, "unit", e.target.value)}
            readOnly={readOnly}
            data-testid={`plan-fm-${i}-unit`}
            className="rounded-sm"
          />
          {!readOnly && (
            <Button
              size="icon"
              variant="ghost"
              onClick={() => remove(i)}
              data-testid={`plan-fm-${i}-remove`}
              className="h-9 w-9 rounded-sm"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      ))}
      {!readOnly && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={add}
          data-testid="plan-fm-add"
          className="rounded-sm"
        >
          <PlusCircle className="mr-1.5 h-3.5 w-3.5" /> Add functional measure
        </Button>
      )}
    </div>
  );
}

function SetStatusDialog({ open, current, value, onChange, onSubmit }) {
  const options = NEW_STATUSES.filter((s) => s !== current);
  const canSubmit = value?.next && (value.reason || "").trim().length >= 3;
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onChange(null)}>
      <DialogContent data-testid="plan-set-status-dialog" className="max-w-md rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Change plan status</DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          Captures a chart-level audit trail. Reason required.
        </p>
        <div className="space-y-3">
          <Label className="text-xs uppercase tracking-wider text-muted-foreground">
            New status
          </Label>
          <Select
            value={value?.next || ""}
            onValueChange={(v) => onChange({ ...value, next: v })}
          >
            <SelectTrigger data-testid="plan-status-select" className="rounded-sm">
              <SelectValue placeholder="—" />
            </SelectTrigger>
            <SelectContent>
              {options.map((s) => (
                <SelectItem key={s} value={s}>{s.replace("_", " ")}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Label className="text-xs uppercase tracking-wider text-muted-foreground">
            Reason
          </Label>
          <Textarea
            rows={3}
            value={value?.reason || ""}
            onChange={(e) => onChange({ ...value, reason: e.target.value })}
            data-testid="plan-status-reason"
            className="rounded-sm"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onChange(null)} className="rounded-sm">Cancel</Button>
          <Button
            disabled={!canSubmit}
            onClick={() => onSubmit(value.next, value.reason)}
            data-testid="plan-status-submit-btn"
            className="rounded-sm"
          >
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
