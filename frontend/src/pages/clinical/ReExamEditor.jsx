/**
 * ReExamEditor — progress reassessment authoring.
 *
 * Route: /patients/:pid/clinical/re-exams/:rid
 *
 * Side-by-side comparison: Baseline (frozen) vs Now. Goal progress
 * tracker per plan goal. Structured outcome measures. Recommendation
 * decision radio + reason. Narrative dialog.
 *
 * Signed re-exams are read-only. Signing with decision=modify_plan
 * emits a `treatment_plan.revised_recommended` audit event only — the
 * plan is not mutated automatically.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowLeft, Check, Edit3, FileCheck2, FileText, Loader2, PlusCircle,
  Save, Trash2,
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
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "../../components/ui/dialog";

import { useAuth } from "../../contexts/AuthContext";
import { useReauth } from "../../components/ReauthGate";
import AddendumPanel from "./AddendumPanel";
import { formatDateTime } from "../../utils/time";

const STATUS_BADGE = {
  draft: { label: "Draft", class: "border-border bg-card text-muted-foreground" },
  sign_ready: { label: "Sign-ready", class: "border-warning/40 bg-warning-soft text-warning" },
  signed: { label: "Signed", class: "border-success/40 bg-success-soft text-success" },
};

const GOAL_STATUS = [
  { value: "on_track", label: "On track" },
  { value: "improved", label: "Improved" },
  { value: "plateau", label: "Plateau" },
  { value: "regressed", label: "Regressed" },
  { value: "met", label: "Met" },
];
const OUTCOME_TYPES = [
  { value: "ndi", label: "NDI" },
  { value: "oswestry", label: "Oswestry" },
  { value: "pain_vas", label: "Pain VAS" },
  { value: "functional_index", label: "Functional Index" },
  { value: "custom", label: "Custom" },
];
const DECISIONS = [
  { value: "continue", label: "Continue plan" },
  { value: "modify_plan", label: "Modify plan" },
  { value: "discharge", label: "Discharge" },
  { value: "transition_maintenance", label: "Transition to maintenance" },
];

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

export default function ReExamEditor() {
  const { pid, rid } = useParams();
  const { user } = useAuth();
  const { requestReauth } = useReauth();
  const canWrite = ["admin", "doctor"].includes(user?.role);

  const [rx, setRx] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [acting, setActing] = useState(false);
  const [narrative, setNarrative] = useState(null);

  const [currentFindings, setCurrentFindings] = useState({});
  const [goalProgress, setGoalProgress] = useState([]);
  const [outcomeUpdates, setOutcomeUpdates] = useState([]);
  const [reco, setReco] = useState({ decision: "", reason: "", revised_plan_summary: "" });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get(`/patients/${pid}/clinical/re-exams/${rid}`);
      setRx(data);
      setCurrentFindings(data.current_findings || {});
      setGoalProgress(data.goal_progress || []);
      setOutcomeUpdates(data.outcome_updates || []);
      setReco({
        decision: data.recommendation_decision || "",
        reason: data.recommendation_reason || "",
        revised_plan_summary: data.revised_plan_summary || "",
      });
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [pid, rid]);

  useEffect(() => {
    load();
  }, [load]);

  const baseline = rx?.baseline_snapshot || {};
  const planSnap = baseline.plan || null;
  const planGoals = planSnap?.goals || [];
  const initialExam = baseline.initial_exam || null;

  const dirty = useMemo(() => {
    if (!rx) return false;
    return (
      JSON.stringify(currentFindings) !== JSON.stringify(rx.current_findings || {}) ||
      JSON.stringify(goalProgress) !== JSON.stringify(rx.goal_progress || []) ||
      JSON.stringify(outcomeUpdates) !== JSON.stringify(rx.outcome_updates || []) ||
      reco.decision !== (rx.recommendation_decision || "") ||
      reco.reason !== (rx.recommendation_reason || "") ||
      reco.revised_plan_summary !== (rx.revised_plan_summary || "")
    );
  }, [rx, currentFindings, goalProgress, outcomeUpdates, reco]);

  const readOnly = !canWrite || rx?.status === "signed";

  // Seed goal_progress rows with one entry per plan goal on first load
  useEffect(() => {
    if (!rx) return;
    if ((rx.goal_progress || []).length > 0) return;
    if (planGoals.length === 0) return;
    setGoalProgress(planGoals.map((g) => ({
      goal_id: g.id,
      current_value: "",
      status: "on_track",
      note: "",
    })));
  }, [rx, planGoals]);

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
    if (saving || readOnly) return;
    setSaving(true);
    try {
      await runReauthAware(async () => {
        const body = {
          current_findings: currentFindings,
          goal_progress: goalProgress.map((g) => ({
            goal_id: g.goal_id,
            status: g.status,
            current_value:
              g.current_value === "" ? null : g.current_value,
            note: g.note || null,
          })),
          outcome_updates: outcomeUpdates.map((o) => ({
            measure_type: o.measure_type,
            label: o.label,
            score: o.score === "" || o.score == null ? null : Number(o.score),
            max_score:
              o.max_score === "" || o.max_score == null ? null : Number(o.max_score),
            note: o.note || null,
          })),
          recommendation_decision: reco.decision || null,
          recommendation_reason: reco.reason || null,
          revised_plan_summary: reco.revised_plan_summary || null,
        };
        const { data } = await api.patch(
          `/patients/${pid}/clinical/re-exams/${rid}`,
          body,
        );
        setRx(data);
        toast.success("Re-exam saved");
      });
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setSaving(false);
    }
  };

  const transition = async (action) => {
    setActing(true);
    try {
      await runReauthAware(async () => {
        const { data } = await api.post(
          `/patients/${pid}/clinical/re-exams/${rid}/${action}`,
          {},
        );
        setRx(data);
        if (action === "sign") toast.success("Re-exam signed");
        else if (action === "mark-sign-ready") toast.success("Marked sign-ready");
        else if (action === "unmark-sign-ready") toast.success("Reverted to draft");
      });
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setActing(false);
    }
  };

  const viewNarrative = async () => {
    setActing(true);
    try {
      const { data } = await api.get(
        `/patients/${pid}/clinical/re-exams/${rid}/narrative`,
      );
      setNarrative(data);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setActing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!rx) {
    return <div className="p-6 text-sm text-muted-foreground">Re-exam not found.</div>;
  }

  const badge = STATUS_BADGE[rx.status] || STATUS_BADGE.draft;

  return (
    <>
      <div data-testid="reexam-editor" className="mx-auto max-w-5xl space-y-6 p-4 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <Link
              to={`/patients/${pid}?tab=clinical`}
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              data-testid="reexam-back-link"
            >
              <ArrowLeft className="h-3 w-3" /> Back to chart
            </Link>
            <h1 className="mt-1 font-display text-2xl font-semibold text-foreground">
              Re-Exam
            </h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>DOS · {formatDateTime(rx.date_of_service)}</span>
              {rx.provider_name && <span>Provider · {rx.provider_name}</span>}
              {rx.episode_title && <span>Episode · {rx.episode_title}</span>}
              {rx.visit_number_at_reexam != null && (
                <span data-testid="reexam-visit-number">After {rx.visit_number_at_reexam} visits</span>
              )}
              <Badge
                variant="outline"
                data-testid="reexam-status-badge"
                className={`text-[10px] uppercase tracking-wider ${badge.class}`}
              >
                {badge.label}
                {rx.status === "signed" && (rx.addendum_count || 0) > 0
                  ? ` · +${rx.addendum_count} addendum${rx.addendum_count === 1 ? "" : "s"}`
                  : ""}
              </Badge>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {canWrite && rx.status !== "signed" && (
              <>
                <Button
                  size="sm"
                  onClick={save}
                  disabled={saving || !dirty}
                  data-testid="reexam-save-btn"
                  className="rounded-sm"
                >
                  <Save className="mr-1.5 h-3.5 w-3.5" />
                  {saving ? "Saving…" : dirty ? "Save" : "Saved"}
                </Button>
                {rx.status === "draft" && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => transition("mark-sign-ready")}
                    disabled={acting || dirty}
                    title={dirty ? "Save first" : undefined}
                    data-testid="reexam-mark-ready-btn"
                    className="rounded-sm"
                  >
                    <Edit3 className="mr-1.5 h-3.5 w-3.5" />
                    Mark sign-ready
                  </Button>
                )}
                {rx.status === "sign_ready" && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => transition("unmark-sign-ready")}
                    disabled={acting}
                    data-testid="reexam-unmark-ready-btn"
                    className="rounded-sm"
                  >
                    Back to draft
                  </Button>
                )}
                <Button
                  size="sm"
                  onClick={() => transition("sign")}
                  disabled={acting || dirty || !reco.decision}
                  title={
                    !reco.decision
                      ? "Select a recommendation decision"
                      : dirty
                        ? "Save first"
                        : undefined
                  }
                  data-testid="reexam-sign-btn"
                  className="rounded-sm"
                >
                  <FileCheck2 className="mr-1.5 h-3.5 w-3.5" />
                  Sign
                </Button>
              </>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={viewNarrative}
              disabled={acting}
              data-testid="reexam-narrative-btn"
              className="rounded-sm"
            >
              <FileText className="mr-1.5 h-3.5 w-3.5" />
              View narrative
            </Button>
          </div>
        </div>

        {/* Baseline snapshot context */}
        <SectionCard
          title="Baseline (frozen at re-exam start)"
          description={
            initialExam
              ? `Last initial exam · ${formatDateTime(initialExam.date_of_service)}`
              : "No baseline initial exam linked."
          }
          testId="reexam-section-baseline"
        >
          {planSnap ? (
            <div data-testid="reexam-plan-snapshot" className="space-y-2 text-sm">
              <div className="font-semibold text-foreground">Plan · {planSnap.title}</div>
              <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                {planSnap.frequency_visits_per_week && (
                  <span>{planSnap.frequency_visits_per_week}x/wk</span>
                )}
                {planSnap.frequency_total_visits && (
                  <span>{planSnap.frequency_total_visits} total visits</span>
                )}
                {planSnap.expected_duration_weeks && (
                  <span>{planSnap.expected_duration_weeks} wks</span>
                )}
                {planSnap.re_exam_date && (
                  <span>Re-exam target · {planSnap.re_exam_date}</span>
                )}
              </div>
              {planSnap.baselines && (
                <div className="text-xs text-muted-foreground">
                  {planSnap.baselines.pain_scale_0_10 != null && (
                    <div>Pain baseline · {planSnap.baselines.pain_scale_0_10}/10</div>
                  )}
                  {planSnap.baselines.key_rom_summary && (
                    <div>ROM · {planSnap.baselines.key_rom_summary}</div>
                  )}
                  {(planSnap.baselines.functional_measures || []).map((fm, i) => (
                    <div key={i}>
                      {fm.label} · {fm.value ?? "—"}{fm.unit ? ` ${fm.unit}` : ""}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              No active treatment plan was linked at re-exam creation.
            </p>
          )}
        </SectionCard>

        {/* Goal progress */}
        <SectionCard
          title="Goal progress"
          description="Track progress against each plan goal."
          testId="reexam-section-goals"
        >
          {planGoals.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No plan goals are linked — add a treatment plan with goals before running a re-exam
              for structured tracking.
            </p>
          ) : (
            <div data-testid="reexam-goal-list" className="space-y-2">
              {goalProgress.map((gp, i) => {
                const g = planGoals.find((pg) => pg.id === gp.goal_id) || {};
                return (
                  <div
                    key={gp.goal_id || i}
                    data-testid={`reexam-goal-row-${i}`}
                    className="grid grid-cols-1 gap-2 rounded-sm border border-border bg-muted/20 p-3 sm:grid-cols-[1fr_180px_180px_180px_1fr]"
                  >
                    <div>
                      <div className="text-xs font-semibold text-foreground">
                        {g.description || gp.goal_id}
                      </div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {g.measure_type}
                      </div>
                    </div>
                    <div>
                      <Label className="text-[10px] uppercase text-muted-foreground">Baseline</Label>
                      <div data-testid={`reexam-goal-${i}-baseline`} className="text-sm">
                        {g.baseline_value ?? "—"}
                        {g.unit ? ` ${g.unit}` : ""}
                      </div>
                    </div>
                    <div>
                      <Label className="text-[10px] uppercase text-muted-foreground">Current</Label>
                      <Input
                        value={gp.current_value ?? ""}
                        onChange={(e) =>
                          setGoalProgress((p) =>
                            p.map((r, idx) => (idx === i ? { ...r, current_value: e.target.value } : r))
                          )
                        }
                        readOnly={readOnly}
                        data-testid={`reexam-goal-${i}-current`}
                        className="rounded-sm"
                      />
                    </div>
                    <div>
                      <Label className="text-[10px] uppercase text-muted-foreground">Status</Label>
                      <Select
                        value={gp.status || "on_track"}
                        onValueChange={(v) =>
                          setGoalProgress((p) =>
                            p.map((r, idx) => (idx === i ? { ...r, status: v } : r))
                          )
                        }
                        disabled={readOnly}
                      >
                        <SelectTrigger
                          data-testid={`reexam-goal-${i}-status`}
                          className="rounded-sm"
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {GOAL_STATUS.map((s) => (
                            <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <Label className="text-[10px] uppercase text-muted-foreground">Note</Label>
                      <Input
                        value={gp.note || ""}
                        onChange={(e) =>
                          setGoalProgress((p) =>
                            p.map((r, idx) => (idx === i ? { ...r, note: e.target.value } : r))
                          )
                        }
                        readOnly={readOnly}
                        data-testid={`reexam-goal-${i}-note`}
                        className="rounded-sm"
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </SectionCard>

        {/* Current objective findings */}
        <SectionCard
          title="Updated objective findings"
          description="Optional — capture current exam bits relevant to the comparison."
          testId="reexam-section-findings"
        >
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {[
              ["observation_inspection", "Observation / inspection"],
              ["posture", "Posture"],
              ["gait", "Gait"],
              ["palpation_findings", "Palpation"],
              ["segmental_spinal_findings", "Segmental spinal"],
              ["neurologic_findings", "Neurologic"],
              ["sensory_reflex_findings", "Sensory / reflex"],
            ].map(([key, label]) => (
              <div key={key}>
                <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                  {label}
                </Label>
                <Textarea
                  rows={2}
                  value={currentFindings[key] || ""}
                  onChange={(e) =>
                    setCurrentFindings((s) => ({ ...s, [key]: e.target.value || null }))
                  }
                  readOnly={readOnly}
                  data-testid={`reexam-findings-${key}`}
                  className="rounded-sm"
                />
              </div>
            ))}
          </div>
        </SectionCard>

        {/* Outcome measures */}
        <SectionCard
          title="Outcome measures"
          description="Typed, score-based outcome captures for trending."
          testId="reexam-section-outcomes"
        >
          <OutcomeEditor
            value={outcomeUpdates}
            onChange={setOutcomeUpdates}
            readOnly={readOnly}
          />
        </SectionCard>

        {/* Recommendation */}
        <SectionCard
          title="Recommendation"
          description="What should happen next. Required before signing."
          testId="reexam-section-recommendation"
        >
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Decision
            </Label>
            <div data-testid="reexam-decision-group" className="mt-1 flex flex-wrap gap-2">
              {DECISIONS.map((d) => (
                <button
                  key={d.value}
                  type="button"
                  disabled={readOnly}
                  onClick={() => setReco((r) => ({ ...r, decision: d.value }))}
                  data-testid={`reexam-decision-${d.value}`}
                  className={`rounded-sm border px-3 py-1 text-xs transition-colors ${
                    reco.decision === d.value
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:bg-muted/40"
                  } ${readOnly ? "opacity-60" : ""}`}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Reason
            </Label>
            <Textarea
              rows={2}
              value={reco.reason}
              onChange={(e) => setReco((r) => ({ ...r, reason: e.target.value }))}
              readOnly={readOnly}
              data-testid="reexam-decision-reason"
              className="rounded-sm"
            />
          </div>
          {reco.decision === "modify_plan" && (
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Revised plan summary (recorded on this re-exam; does NOT auto-apply to the plan)
              </Label>
              <Textarea
                rows={3}
                value={reco.revised_plan_summary}
                onChange={(e) => setReco((r) => ({ ...r, revised_plan_summary: e.target.value }))}
                readOnly={readOnly}
                data-testid="reexam-revised-summary"
                className="rounded-sm"
              />
            </div>
          )}
        </SectionCard>

        {rx.status === "signed" && (
          <div
            data-testid="reexam-signed-banner"
            className="rounded-lg border border-success/30 bg-success-soft p-4 text-sm"
          >
            <div className="flex items-center gap-2 font-semibold text-success">
              <Check className="h-4 w-4" />
              Signed
            </div>
            <p className="mt-1 text-xs text-success/80">
              This re-exam is a permanent chart artifact and cannot be edited. Plan changes are
              captured separately on the Treatment Plan record. Additional
              clarifications may be appended through a signed addendum below.
            </p>
          </div>
        )}

        <AddendumPanel
          patientId={pid}
          parentType="re_exam"
          parentId={rid}
          parentSigned={rx.status === "signed"}
          canWrite={canWrite}
          currentUser={user}
          onReauthNeeded={() => requestReauth(async () => load())}
        />
      </div>

      <NarrativeDialog
        open={!!narrative}
        onOpenChange={(v) => !v && setNarrative(null)}
        text={narrative?.narrative || ""}
        generatedAt={narrative?.generated_at}
      />
    </>
  );
}

function OutcomeEditor({ value, onChange, readOnly }) {
  const rows = value || [];
  const update = (i, k, v) =>
    onChange(rows.map((r, idx) => (idx === i ? { ...r, [k]: v === "" ? null : v } : r)));
  const add = () =>
    onChange([...rows, { measure_type: "pain_vas", label: "", score: null, max_score: 10 }]);
  const remove = (i) => onChange(rows.filter((_, idx) => idx !== i));
  return (
    <div data-testid="reexam-outcome-list" className="space-y-2">
      {rows.map((o, i) => (
        <div
          key={i}
          data-testid={`reexam-outcome-row-${i}`}
          className="grid grid-cols-1 gap-2 rounded-sm border border-border bg-muted/20 p-2 sm:grid-cols-[160px_1fr_120px_120px_1fr_36px]"
        >
          <Select
            value={o.measure_type}
            onValueChange={(v) => update(i, "measure_type", v)}
            disabled={readOnly}
          >
            <SelectTrigger data-testid={`reexam-outcome-${i}-type`} className="rounded-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {OUTCOME_TYPES.map((t) => (
                <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder="Label"
            value={o.label || ""}
            onChange={(e) => update(i, "label", e.target.value)}
            readOnly={readOnly}
            data-testid={`reexam-outcome-${i}-label`}
            className="rounded-sm"
          />
          <Input
            type="number"
            placeholder="Score"
            value={o.score ?? ""}
            onChange={(e) => update(i, "score", e.target.value)}
            readOnly={readOnly}
            data-testid={`reexam-outcome-${i}-score`}
            className="rounded-sm"
          />
          <Input
            type="number"
            placeholder="Max"
            value={o.max_score ?? ""}
            onChange={(e) => update(i, "max_score", e.target.value)}
            readOnly={readOnly}
            data-testid={`reexam-outcome-${i}-max`}
            className="rounded-sm"
          />
          <Input
            placeholder="Note"
            value={o.note || ""}
            onChange={(e) => update(i, "note", e.target.value)}
            readOnly={readOnly}
            data-testid={`reexam-outcome-${i}-note`}
            className="rounded-sm"
          />
          {!readOnly && (
            <Button
              size="icon"
              variant="ghost"
              onClick={() => remove(i)}
              data-testid={`reexam-outcome-${i}-remove`}
              className="h-9 w-9 rounded-sm"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      ))}
      {!readOnly && (
        <Button
          size="sm"
          variant="outline"
          onClick={add}
          data-testid="reexam-outcome-add"
          className="rounded-sm"
        >
          <PlusCircle className="mr-1.5 h-3.5 w-3.5" /> Add outcome measure
        </Button>
      )}
    </div>
  );
}

function NarrativeDialog({ open, onOpenChange, text, generatedAt }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="reexam-narrative-dialog" className="max-w-3xl rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Re-exam narrative</DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          Server-rendered. {generatedAt ? `Generated ${formatDateTime(generatedAt)}.` : ""}
        </p>
        <pre
          data-testid="reexam-narrative-text"
          className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-sm border border-border bg-muted/30 p-4 text-xs leading-relaxed"
        >
          {text}
        </pre>
      </DialogContent>
    </Dialog>
  );
}
