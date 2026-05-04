/**
 * FollowUpNoteEditor — full-page Follow-up / Daily Visit Note authoring.
 *
 * Route: /patients/:pid/clinical/follow-up/:nid
 *
 * Structured-first editor organised by SOAP:
 *   S — Subjective (interval history, pain, function, adherence)
 *   O — Objective (region findings, reassessment, vitals)
 *   A — Assessment (response to care, clinical impression)
 *   P — Plan (treatments rendered, regions treated, home care, next visit)
 *
 * Copy-forward fields are highlighted so the provider reviews & edits them.
 * Status-aware toolbar: Save · Copy forward · Mark sign-ready · Sign ·
 * View narrative. Signed notes render read-only.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowLeft,
  Check,
  ClipboardCopy,
  Edit3,
  FileCheck2,
  FileText,
  Loader2,
  PlusCircle,
  Save,
  Trash2,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import { Badge } from "../../components/ui/badge";
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

import { useAuth } from "../../contexts/AuthContext";
import { useReauth } from "../../components/ReauthGate";
import AddendumPanel from "./AddendumPanel";
import { formatDateTime } from "../../utils/time";

const PAIN_CHANGE = [
  { value: "better", label: "Better" },
  { value: "worse", label: "Worse" },
  { value: "same", label: "Same" },
  { value: "fluctuating", label: "Fluctuating" },
];
const ADHERENCE = [
  { value: "yes", label: "Yes" },
  { value: "partial", label: "Partial" },
  { value: "no", label: "No" },
];
const RESPONSE = [
  { value: "improving", label: "Improving" },
  { value: "plateau", label: "Plateau" },
  { value: "regressing", label: "Regressing" },
  { value: "new_complaint", label: "New complaint" },
];
const TREATMENT_KINDS = [
  { value: "adjustment", label: "Adjustment" },
  { value: "modality", label: "Modality" },
  { value: "soft_tissue", label: "Soft tissue" },
  { value: "exercise", label: "Exercise" },
  { value: "other", label: "Other" },
];

const STATUS_BADGE = {
  draft: { label: "Draft", class: "border-border bg-card text-muted-foreground" },
  sign_ready: { label: "Sign-ready", class: "border-warning/40 bg-warning-soft text-warning" },
  signed: { label: "Signed", class: "border-success/40 bg-success-soft text-success" },
};

const FIELD_LABEL = {
  "subjective.interval_history": "Interval history",
  "subjective.pain_scale_0_10": "Pain scale",
  "subjective.pain_change": "Pain change",
  "subjective.functional_change": "Functional change",
  "subjective.adherence_home_care": "Home-care adherence",
  "subjective.adherence_notes": "Adherence notes",
  "objective.region_findings": "Region findings",
  "objective.reassessment_summary": "Reassessment summary",
  "objective.vitals": "Vitals",
  "assessment.response_to_care": "Response to care",
  "assessment.clinical_impression": "Clinical impression",
  "plan.treatment_rendered": "Treatment rendered",
  "plan.regions_treated": "Regions treated",
  "plan.home_care_reinforcement": "Home-care reinforcement",
  "plan.next_visit_plan": "Next visit plan",
  "plan.recommended_interval_days": "Next visit interval (days)",
};

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

function FieldShell({ label, copied, testId, children }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </Label>
        {copied && (
          <Badge
            variant="outline"
            data-testid={`note-copied-${testId}`}
            className="border-warning/40 bg-warning-soft text-[10px] text-warning"
          >
            Copied forward
          </Badge>
        )}
      </div>
      {children}
    </div>
  );
}

export default function FollowUpNoteEditor() {
  const { pid, nid } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { requestReauth } = useReauth();
  const canWrite = ["admin", "doctor"].includes(user?.role);

  const [note, setNote] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [acting, setActing] = useState(false);
  const [narrative, setNarrative] = useState(null);
  const [copyPickerOpen, setCopyPickerOpen] = useState(false);
  const [priorNotes, setPriorNotes] = useState([]);

  const [subjective, setSubjective] = useState({});
  const [objective, setObjective] = useState({});
  const [assessment, setAssessment] = useState({});
  const [plan, setPlan] = useState({});

  const readOnly = !canWrite || note?.status === "signed";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get(`/patients/${pid}/clinical/notes/${nid}`);
      setNote(data);
      setSubjective(data.subjective || {});
      setObjective(data.objective || {});
      setAssessment(data.assessment || {});
      setPlan(data.plan || {});
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [pid, nid]);

  useEffect(() => {
    load();
  }, [load]);

  const dirty = useMemo(() => {
    if (!note) return false;
    return (
      JSON.stringify(subjective) !== JSON.stringify(note.subjective || {}) ||
      JSON.stringify(objective) !== JSON.stringify(note.objective || {}) ||
      JSON.stringify(assessment) !== JSON.stringify(note.assessment || {}) ||
      JSON.stringify(plan) !== JSON.stringify(note.plan || {})
    );
  }, [note, subjective, objective, assessment, plan]);

  const copiedSet = useMemo(
    () => new Set(note?.copied_fields || []),
    [note?.copied_fields]
  );

  const save = async () => {
    if (saving || readOnly) return;
    setSaving(true);
    try {
      const { data } = await api.patch(
        `/patients/${pid}/clinical/notes/${nid}`,
        { subjective, objective, assessment, plan },
      );
      setNote(data);
      toast.success("Draft saved");
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setSaving(false);
    }
  };

  const transition = async (action) => {
    setActing(true);
    try {
      const { data } = await api.post(
        `/patients/${pid}/clinical/notes/${nid}/${action}`,
        {},
      );
      setNote(data);
      setSubjective(data.subjective || {});
      setObjective(data.objective || {});
      setAssessment(data.assessment || {});
      setPlan(data.plan || {});
      if (action === "sign") toast.success("Note signed");
      else if (action === "mark-sign-ready") toast.success("Marked sign-ready");
      else if (action === "unmark-sign-ready") toast.success("Reverted to draft");
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
        `/patients/${pid}/clinical/notes/${nid}/narrative`,
      );
      setNarrative(data);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setActing(false);
    }
  };

  const openCopyPicker = async () => {
    try {
      const { data } = await api.get(`/patients/${pid}/clinical/notes`, {
        params: { status_in: "signed" },
      });
      setPriorNotes(data.filter((n) => n.id !== nid));
      setCopyPickerOpen(true);
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  const runCopyForward = async (sourceId, force) => {
    setActing(true);
    try {
      const { data } = await api.post(
        `/patients/${pid}/clinical/notes/${nid}/copy-forward`,
        { source_note_id: sourceId, force },
      );
      setNote(data);
      setSubjective(data.subjective || {});
      setObjective(data.objective || {});
      setAssessment(data.assessment || {});
      setPlan(data.plan || {});
      const copied = (data.copied_fields || []).length;
      toast.success(
        copied > 0
          ? `Copied ${copied} fields forward — review highlighted items`
          : "Nothing to copy — destination already filled",
      );
      setCopyPickerOpen(false);
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
  if (!note) {
    return <div className="p-6 text-sm text-muted-foreground">Note not found.</div>;
  }

  const statusBadge = STATUS_BADGE[note.status] || STATUS_BADGE.draft;
  const completeness = note.completeness || { score: 0, missing_fields: [], filled: 0, total: 5 };

  return (
    <>
      <div data-testid="follow-up-note-editor" className="mx-auto max-w-7xl grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6 p-4 sm:p-6">
        <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <Link
              to={`/patients/${pid}?tab=clinical`}
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              data-testid="note-back-link"
            >
              <ArrowLeft className="h-3 w-3" /> Back to chart
            </Link>
            <h1 className="mt-1 font-display text-2xl font-semibold text-foreground">
              Follow-up / Daily Visit Note
            </h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>DOS · {formatDateTime(note.date_of_service)}</span>
              {note.provider_name && <span>Provider · {note.provider_name}</span>}
              {note.episode_title && <span>Episode · {note.episode_title}</span>}
              {note.visit_number != null && (
                <span data-testid="note-visit-number">Visit #{note.visit_number}</span>
              )}
              <Badge
                variant="outline"
                data-testid="note-status-badge"
                className={`text-[10px] uppercase tracking-wider ${statusBadge.class}`}
              >
                {statusBadge.label}
                {note.status === "signed" && (note.addendum_count || 0) > 0
                  ? ` · +${note.addendum_count} addendum${note.addendum_count === 1 ? "" : "s"}`
                  : ""}
              </Badge>
              {note.signed_at && (
                <span>
                  Signed {formatDateTime(note.signed_at)}
                  {note.signed_by_name ? ` · ${note.signed_by_name}` : ""}
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {canWrite && note.status !== "signed" && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={openCopyPicker}
                  disabled={acting}
                  data-testid="note-copy-forward-btn"
                  className="rounded-sm"
                >
                  <ClipboardCopy className="mr-1.5 h-3.5 w-3.5" />
                  Copy forward
                </Button>
                <Button
                  size="sm"
                  onClick={save}
                  disabled={saving || !dirty}
                  data-testid="note-save-btn"
                  className="rounded-sm"
                >
                  <Save className="mr-1.5 h-3.5 w-3.5" />
                  {saving ? "Saving…" : dirty ? "Save draft" : "Saved"}
                </Button>
                {note.status === "draft" && (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => transition("mark-sign-ready")}
                    disabled={acting || dirty}
                    title={dirty ? "Save first" : undefined}
                    data-testid="note-mark-ready-btn"
                    className="rounded-sm"
                  >
                    <Edit3 className="mr-1.5 h-3.5 w-3.5" />
                    Mark sign-ready
                  </Button>
                )}
                {note.status === "sign_ready" && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => transition("unmark-sign-ready")}
                    disabled={acting}
                    data-testid="note-unmark-ready-btn"
                    className="rounded-sm"
                  >
                    Back to draft
                  </Button>
                )}
                <Button
                  size="sm"
                  onClick={() => transition("sign")}
                  disabled={acting || dirty}
                  title={dirty ? "Save first" : undefined}
                  data-testid="note-sign-btn"
                  className="rounded-sm"
                >
                  <FileCheck2 className="mr-1.5 h-3.5 w-3.5" />
                  Sign
                </Button>
              </>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={viewNarrative}
              disabled={acting}
              data-testid="note-narrative-btn"
              className="rounded-sm"
            >
              <FileText className="mr-1.5 h-3.5 w-3.5" />
              View narrative
            </Button>
          </div>
        </div>

        {/* Completeness + missing fields */}
        <div
          data-testid="note-completeness"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card p-4"
        >
          <div className="flex items-center gap-3">
            <div
              data-testid="note-completeness-score"
              className="font-display text-2xl font-semibold text-foreground"
            >
              {completeness.filled}/{completeness.total}
            </div>
            <div className="text-xs text-muted-foreground">
              <div>Core fields complete ({completeness.score}%)</div>
              <div>
                {completeness.missing_fields.length > 0
                  ? "Missing recommended fields below"
                  : "All recommended fields filled"}
              </div>
            </div>
          </div>
          {completeness.missing_fields.length > 0 && (
            <div
              data-testid="note-missing-list"
              className="flex flex-wrap gap-1.5"
            >
              {completeness.missing_fields.map((f) => (
                <Badge
                  key={f}
                  variant="outline"
                  className="text-[10px]"
                  data-testid={`note-missing-${f}`}
                >
                  {FIELD_LABEL[f] || f}
                </Badge>
              ))}
            </div>
          )}
        </div>

        {/* Phase 6 — Active plan read-only strip */}
        {note.active_plan_summary && (
          <div
            data-testid="note-active-plan-strip"
            className="rounded-lg border border-primary/30 bg-primary/5 p-4"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline" className="border-primary/40 bg-primary/10 text-[10px] text-primary">
                    Active plan
                  </Badge>
                  <span className="font-display text-sm font-semibold text-foreground">
                    {note.active_plan_summary.title}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  {note.active_plan_summary.frequency_visits_per_week && (
                    <span>{note.active_plan_summary.frequency_visits_per_week}x/wk</span>
                  )}
                  {note.active_plan_summary.frequency_total_visits && (
                    <span>{note.active_plan_summary.frequency_total_visits} total visits</span>
                  )}
                  {note.active_plan_summary.expected_duration_weeks && (
                    <span>{note.active_plan_summary.expected_duration_weeks} wks</span>
                  )}
                  {note.active_plan_summary.re_exam_date && (
                    <span>Re-exam · {note.active_plan_summary.re_exam_date}</span>
                  )}
                </div>
                {(note.active_plan_summary.goals || []).length > 0 && (
                  <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
                    {note.active_plan_summary.goals.map((g) => (
                      <li key={g.id} data-testid={`note-plan-goal-${g.id}`}>
                        • {g.description}
                        {g.target_value != null ? ` → target ${g.target_value}` : ""}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div
                data-testid="note-active-plan-progress"
                className="shrink-0 text-right"
              >
                <div className="font-display text-sm font-semibold text-foreground">
                  {note.active_plan_summary.progress?.visits_completed ?? 0}/
                  {note.active_plan_summary.progress?.total_visits ?? "—"}
                </div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  {note.active_plan_summary.progress?.percent ?? 0}% visits
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Subjective */}
        <SectionCard
          title="Subjective (S)"
          description="What the patient reports since the last visit."
          testId="note-section-subjective"
        >
          <FieldShell
            label="Interval history"
            copied={copiedSet.has("subjective.interval_history")}
            testId="subjective-interval-history"
          >
            <Textarea
              rows={3}
              value={subjective.interval_history || ""}
              onChange={(e) =>
                setSubjective((s) => ({ ...s, interval_history: e.target.value || null }))
              }
              readOnly={readOnly}
              data-testid="note-interval-history"
              className="rounded-sm"
            />
          </FieldShell>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <FieldShell
              label="Pain scale (0–10)"
              copied={copiedSet.has("subjective.pain_scale_0_10")}
              testId="subjective-pain-scale"
            >
              <Input
                type="number"
                min={0}
                max={10}
                value={subjective.pain_scale_0_10 ?? ""}
                onChange={(e) =>
                  setSubjective((s) => ({
                    ...s,
                    pain_scale_0_10: e.target.value === "" ? null : Number(e.target.value),
                  }))
                }
                readOnly={readOnly}
                data-testid="note-pain-scale"
                className="rounded-sm"
              />
            </FieldShell>
            <FieldShell
              label="Pain change"
              copied={copiedSet.has("subjective.pain_change")}
              testId="subjective-pain-change"
            >
              <Select
                value={subjective.pain_change || ""}
                onValueChange={(v) =>
                  setSubjective((s) => ({ ...s, pain_change: v || null }))
                }
                disabled={readOnly}
              >
                <SelectTrigger data-testid="note-pain-change" className="rounded-sm">
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  {PAIN_CHANGE.map((p) => (
                    <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FieldShell>
            <FieldShell
              label="Home-care adherence"
              copied={copiedSet.has("subjective.adherence_home_care")}
              testId="subjective-adherence"
            >
              <Select
                value={subjective.adherence_home_care || ""}
                onValueChange={(v) =>
                  setSubjective((s) => ({ ...s, adherence_home_care: v || null }))
                }
                disabled={readOnly}
              >
                <SelectTrigger data-testid="note-adherence" className="rounded-sm">
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  {ADHERENCE.map((p) => (
                    <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FieldShell>
          </div>

          <FieldShell
            label="Functional change"
            copied={copiedSet.has("subjective.functional_change")}
            testId="subjective-functional-change"
          >
            <Textarea
              rows={2}
              value={subjective.functional_change || ""}
              onChange={(e) =>
                setSubjective((s) => ({ ...s, functional_change: e.target.value || null }))
              }
              readOnly={readOnly}
              data-testid="note-functional-change"
              className="rounded-sm"
            />
          </FieldShell>

          <FieldShell
            label="Adherence notes"
            copied={copiedSet.has("subjective.adherence_notes")}
            testId="subjective-adherence-notes"
          >
            <Textarea
              rows={2}
              value={subjective.adherence_notes || ""}
              onChange={(e) =>
                setSubjective((s) => ({ ...s, adherence_notes: e.target.value || null }))
              }
              readOnly={readOnly}
              data-testid="note-adherence-notes"
              className="rounded-sm"
            />
          </FieldShell>
        </SectionCard>

        {/* Objective */}
        <SectionCard
          title="Objective (O)"
          description="Examiner findings on today's visit."
          testId="note-section-objective"
        >
          <RegionFindingsEditor
            value={objective.region_findings || []}
            onChange={(v) => setObjective((o) => ({ ...o, region_findings: v }))}
            readOnly={readOnly}
            copied={copiedSet.has("objective.region_findings")}
          />

          <FieldShell
            label="Reassessment summary"
            copied={copiedSet.has("objective.reassessment_summary")}
            testId="objective-reassessment"
          >
            <Textarea
              rows={3}
              value={objective.reassessment_summary || ""}
              onChange={(e) =>
                setObjective((o) => ({ ...o, reassessment_summary: e.target.value || null }))
              }
              readOnly={readOnly}
              data-testid="note-reassessment"
              className="rounded-sm"
            />
          </FieldShell>

          <VitalsEditor
            value={objective.vitals || {}}
            onChange={(v) => setObjective((o) => ({ ...o, vitals: v }))}
            readOnly={readOnly}
            copied={copiedSet.has("objective.vitals")}
          />
        </SectionCard>

        {/* Assessment */}
        <SectionCard
          title="Assessment (A)"
          description="Clinical impression and care response."
          testId="note-section-assessment"
        >
          <FieldShell
            label="Response to care"
            copied={copiedSet.has("assessment.response_to_care")}
            testId="assessment-response"
          >
            <Select
              value={assessment.response_to_care || ""}
              onValueChange={(v) =>
                setAssessment((a) => ({ ...a, response_to_care: v || null }))
              }
              disabled={readOnly}
            >
              <SelectTrigger data-testid="note-response-to-care" className="rounded-sm">
                <SelectValue placeholder="—" />
              </SelectTrigger>
              <SelectContent>
                {RESPONSE.map((r) => (
                  <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FieldShell>

          <FieldShell
            label="Clinical impression"
            copied={copiedSet.has("assessment.clinical_impression")}
            testId="assessment-impression"
          >
            <Textarea
              rows={3}
              value={assessment.clinical_impression || ""}
              onChange={(e) =>
                setAssessment((a) => ({ ...a, clinical_impression: e.target.value || null }))
              }
              readOnly={readOnly}
              data-testid="note-clinical-impression"
              className="rounded-sm"
            />
          </FieldShell>
        </SectionCard>

        {/* Plan */}
        <SectionCard
          title="Plan (P)"
          description="Treatment rendered today and plan for next visit."
          testId="note-section-plan"
        >
          <TreatmentRenderedEditor
            value={plan.treatment_rendered || []}
            onChange={(v) => setPlan((p) => ({ ...p, treatment_rendered: v }))}
            readOnly={readOnly}
            copied={copiedSet.has("plan.treatment_rendered")}
          />

          <FieldShell
            label="Regions treated"
            copied={copiedSet.has("plan.regions_treated")}
            testId="plan-regions-treated"
          >
            <Input
              placeholder="cervical, lumbar, right shoulder"
              value={(plan.regions_treated || []).join(", ")}
              onChange={(e) => {
                const list = e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean);
                setPlan((p) => ({ ...p, regions_treated: list }));
              }}
              readOnly={readOnly}
              data-testid="note-regions-treated"
              className="rounded-sm"
            />
          </FieldShell>

          <FieldShell
            label="Home-care reinforcement"
            copied={copiedSet.has("plan.home_care_reinforcement")}
            testId="plan-home-care"
          >
            <Textarea
              rows={2}
              value={plan.home_care_reinforcement || ""}
              onChange={(e) =>
                setPlan((p) => ({ ...p, home_care_reinforcement: e.target.value || null }))
              }
              readOnly={readOnly}
              data-testid="note-home-care"
              className="rounded-sm"
            />
          </FieldShell>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_200px]">
            <FieldShell
              label="Plan for next visit"
              copied={copiedSet.has("plan.next_visit_plan")}
              testId="plan-next-visit"
            >
              <Textarea
                rows={2}
                value={plan.next_visit_plan || ""}
                onChange={(e) =>
                  setPlan((p) => ({ ...p, next_visit_plan: e.target.value || null }))
                }
                readOnly={readOnly}
                data-testid="note-next-visit-plan"
                className="rounded-sm"
              />
            </FieldShell>
            <FieldShell
              label="Interval (days)"
              copied={copiedSet.has("plan.recommended_interval_days")}
              testId="plan-interval-days"
            >
              <Input
                type="number"
                min={0}
                max={365}
                value={plan.recommended_interval_days ?? ""}
                onChange={(e) =>
                  setPlan((p) => ({
                    ...p,
                    recommended_interval_days:
                      e.target.value === "" ? null : Number(e.target.value),
                  }))
                }
                readOnly={readOnly}
                data-testid="note-interval-days"
                className="rounded-sm"
              />
            </FieldShell>
          </div>
        </SectionCard>

        {note.status === "signed" && (
          <div
            data-testid="note-signed-banner"
            className="rounded-lg border border-success/30 bg-success-soft p-4 text-sm"
          >
            <div className="flex items-center gap-2 font-semibold text-success">
              <Check className="h-4 w-4" />
              Signed
            </div>
            <p className="mt-1 text-xs text-success/80">
              This note is a permanent chart artifact and cannot be edited. New findings
              should be documented in a subsequent follow-up note, or appended
              through a signed addendum below.
            </p>
          </div>
        )}

        <AddendumPanel
          patientId={pid}
          parentType="follow_up_note"
          parentId={nid}
          parentSigned={note.status === "signed"}
          canWrite={canWrite}
          currentUser={user}
          onChanged={load}
          onReauthNeeded={() => requestReauth(async () => load())}
        />
        </div>
        {/* AI assist rail — pulled from prior encounters / outcomes /
            questionnaires. Sticky on lg+ so it stays visible as the
            doctor scrolls through the SOAP sections. */}
        <div className="lg:sticky lg:top-6 lg:self-start">
          <EncounterAssistPanel
            noteId={nid}
            onPullSection={(section, text) => {
              if (section === "subjective") {
                setSubjective((s) => ({
                  ...(s || {}),
                  interval_history: [(s && s.interval_history) || "", text].filter(Boolean).join("\n\n"),
                }));
              } else if (section === "objective") {
                setObjective((s) => ({ ...(s || {}), ai_notes: text }));
              } else if (section === "assessment") {
                setAssessment((s) => ({
                  ...(s || {}),
                  clinical_impression: [(s && s.clinical_impression) || "", text].filter(Boolean).join("\n\n"),
                }));
              } else if (section === "plan") {
                setPlan((s) => ({
                  ...(s || {}),
                  narrative: [(s && s.narrative) || "", text].filter(Boolean).join("\n\n"),
                }));
              }
              toast.success(`Pulled into ${section}.`);
            }}
          />
        </div>
      </div>

      <NarrativeDialog
        open={!!narrative}
        onOpenChange={(v) => !v && setNarrative(null)}
        text={narrative?.narrative || ""}
        generatedAt={narrative?.generated_at}
      />
      <CopyForwardPicker
        open={copyPickerOpen}
        onOpenChange={setCopyPickerOpen}
        notes={priorNotes}
        onPick={runCopyForward}
        submitting={acting}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------
function RegionFindingsEditor({ value, onChange, readOnly, copied }) {
  const rows = value || [];
  const update = (i, key, v) => {
    const next = rows.map((r, idx) => (idx === i ? { ...r, [key]: v || null } : r));
    onChange(next);
  };
  const addRow = () =>
    onChange([...rows, { body_region: "", palpation: "", rom_summary: "", notes: "" }]);
  const removeRow = (i) => onChange(rows.filter((_, idx) => idx !== i));

  return (
    <FieldShell label="Region findings" copied={copied} testId="objective-region-findings">
      <div data-testid="note-region-findings" className="space-y-2">
        {rows.map((r, i) => (
          <div
            key={i}
            data-testid={`note-region-row-${i}`}
            className="grid grid-cols-1 gap-2 rounded-sm border border-border bg-muted/20 p-2 sm:grid-cols-4"
          >
            <Input
              placeholder="Region (e.g. cervical)"
              value={r.body_region || ""}
              onChange={(e) => update(i, "body_region", e.target.value)}
              readOnly={readOnly}
              data-testid={`note-region-${i}-body`}
              className="rounded-sm"
            />
            <Input
              placeholder="Palpation"
              value={r.palpation || ""}
              onChange={(e) => update(i, "palpation", e.target.value)}
              readOnly={readOnly}
              data-testid={`note-region-${i}-palpation`}
              className="rounded-sm"
            />
            <Input
              placeholder="ROM summary"
              value={r.rom_summary || ""}
              onChange={(e) => update(i, "rom_summary", e.target.value)}
              readOnly={readOnly}
              data-testid={`note-region-${i}-rom`}
              className="rounded-sm"
            />
            <div className="flex gap-2">
              <Input
                placeholder="Notes"
                value={r.notes || ""}
                onChange={(e) => update(i, "notes", e.target.value)}
                readOnly={readOnly}
                data-testid={`note-region-${i}-notes`}
                className="rounded-sm"
              />
              {!readOnly && (
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => removeRow(i)}
                  data-testid={`note-region-${i}-remove`}
                  className="h-9 w-9 rounded-sm"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
          </div>
        ))}
        {!readOnly && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={addRow}
            data-testid="note-region-add"
            className="rounded-sm"
          >
            <PlusCircle className="mr-1.5 h-3.5 w-3.5" /> Add region finding
          </Button>
        )}
      </div>
    </FieldShell>
  );
}

function VitalsEditor({ value, onChange, readOnly, copied }) {
  const update = (k, v) => onChange({ ...(value || {}), [k]: v });
  return (
    <FieldShell label="Vitals (optional)" copied={copied} testId="objective-vitals">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Input
          placeholder="BP e.g. 120/80"
          value={value.blood_pressure || ""}
          onChange={(e) => update("blood_pressure", e.target.value || null)}
          readOnly={readOnly}
          data-testid="note-vitals-bp"
          className="rounded-sm"
        />
        <Input
          placeholder="Pulse"
          type="number"
          value={value.pulse_bpm ?? ""}
          onChange={(e) =>
            update("pulse_bpm", e.target.value === "" ? null : Number(e.target.value))
          }
          readOnly={readOnly}
          data-testid="note-vitals-pulse"
          className="rounded-sm"
        />
      </div>
    </FieldShell>
  );
}

function TreatmentRenderedEditor({ value, onChange, readOnly, copied }) {
  const rows = value || [];
  const update = (i, key, v) => {
    const next = rows.map((r, idx) => (idx === i ? { ...r, [key]: v } : r));
    onChange(next);
  };
  const addRow = () =>
    onChange([...rows, { kind: "adjustment", segments: [], notes: "" }]);
  const removeRow = (i) => onChange(rows.filter((_, idx) => idx !== i));

  return (
    <FieldShell label="Treatment rendered" copied={copied} testId="plan-treatment">
      <div data-testid="note-treatment-list" className="space-y-2">
        {rows.map((r, i) => (
          <div
            key={i}
            data-testid={`note-treatment-row-${i}`}
            className="grid grid-cols-1 gap-2 rounded-sm border border-border bg-muted/20 p-2 sm:grid-cols-[160px_1fr_1fr_80px_36px]"
          >
            <Select
              value={r.kind}
              onValueChange={(v) => update(i, "kind", v)}
              disabled={readOnly}
            >
              <SelectTrigger
                data-testid={`note-treatment-${i}-kind`}
                className="rounded-sm"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TREATMENT_KINDS.map((k) => (
                  <SelectItem key={k.value} value={k.value}>{k.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            {r.kind === "adjustment" ? (
              <>
                <Input
                  placeholder="Segments (comma-sep, e.g. C5, T4)"
                  value={(r.segments || []).join(", ")}
                  onChange={(e) => {
                    const list = e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean);
                    update(i, "segments", list);
                  }}
                  readOnly={readOnly}
                  data-testid={`note-treatment-${i}-segments`}
                  className="rounded-sm"
                />
                <Input
                  placeholder="Technique (e.g. Diversified)"
                  value={r.technique || ""}
                  onChange={(e) => update(i, "technique", e.target.value || null)}
                  readOnly={readOnly}
                  data-testid={`note-treatment-${i}-technique`}
                  className="rounded-sm"
                />
                <Input
                  placeholder="Min"
                  type="number"
                  value={r.duration_min ?? ""}
                  onChange={(e) =>
                    update(
                      i,
                      "duration_min",
                      e.target.value === "" ? null : Number(e.target.value),
                    )
                  }
                  readOnly={readOnly}
                  data-testid={`note-treatment-${i}-duration`}
                  className="rounded-sm"
                />
              </>
            ) : (
              <>
                <Input
                  placeholder="Modality / description"
                  value={r.modality || r.description || ""}
                  onChange={(e) =>
                    update(i, r.kind === "modality" ? "modality" : "description", e.target.value)
                  }
                  readOnly={readOnly}
                  data-testid={`note-treatment-${i}-modality`}
                  className="rounded-sm"
                />
                <Input
                  placeholder="Region"
                  value={r.region || ""}
                  onChange={(e) => update(i, "region", e.target.value || null)}
                  readOnly={readOnly}
                  data-testid={`note-treatment-${i}-region`}
                  className="rounded-sm"
                />
                <Input
                  placeholder="Min"
                  type="number"
                  value={r.duration_min ?? ""}
                  onChange={(e) =>
                    update(
                      i,
                      "duration_min",
                      e.target.value === "" ? null : Number(e.target.value),
                    )
                  }
                  readOnly={readOnly}
                  data-testid={`note-treatment-${i}-duration`}
                  className="rounded-sm"
                />
              </>
            )}
            {!readOnly && (
              <Button
                size="icon"
                variant="ghost"
                onClick={() => removeRow(i)}
                data-testid={`note-treatment-${i}-remove`}
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
            variant="outline"
            size="sm"
            onClick={addRow}
            data-testid="note-treatment-add"
            className="rounded-sm"
          >
            <PlusCircle className="mr-1.5 h-3.5 w-3.5" /> Add treatment entry
          </Button>
        )}
      </div>
    </FieldShell>
  );
}

function NarrativeDialog({ open, onOpenChange, text, generatedAt }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="note-narrative-dialog" className="max-w-3xl rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Follow-up note — SOAP narrative</DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          Server-rendered SOAP summary. {generatedAt ? `Generated ${formatDateTime(generatedAt)}.` : ""}
        </p>
        <pre
          data-testid="note-narrative-text"
          className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-sm border border-border bg-muted/30 p-4 text-xs leading-relaxed"
        >
          {text}
        </pre>
      </DialogContent>
    </Dialog>
  );
}

function CopyForwardPicker({ open, onOpenChange, notes, onPick, submitting }) {
  const [selectedId, setSelectedId] = useState(null);
  const [force, setForce] = useState(false);
  useEffect(() => {
    if (!open) {
      setSelectedId(null);
      setForce(false);
    }
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="note-copy-forward-dialog" className="max-w-xl rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Copy forward from a signed note</DialogTitle>
        </DialogHeader>
        {notes.length === 0 ? (
          <p data-testid="copy-forward-empty" className="text-sm text-muted-foreground">
            No signed follow-up notes available for this patient yet.
          </p>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              Select a prior signed note to pull its structured content into this note. Non-destructive
              by default — only empty fields will be filled. Enable "overwrite" to replace non-empty
              fields.
            </p>
            <div className="max-h-64 space-y-1 overflow-auto">
              {notes.map((n) => (
                <button
                  key={n.id}
                  type="button"
                  onClick={() => setSelectedId(n.id)}
                  data-testid={`copy-forward-source-${n.id}`}
                  className={`w-full rounded-sm border p-2 text-left text-sm transition-colors ${
                    selectedId === n.id
                      ? "border-primary/60 bg-primary/10"
                      : "border-border hover:bg-muted/40"
                  }`}
                >
                  <div className="font-medium">
                    {n.visit_number != null ? `Visit #${n.visit_number}` : "Signed note"}{" "}
                    · {formatDateTime(n.date_of_service)}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {n.provider_name || "Unknown provider"}
                    {n.assessment?.response_to_care
                      ? ` · ${n.assessment.response_to_care.replace("_", " ")}`
                      : ""}
                  </div>
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={force}
                onChange={(e) => setForce(e.target.checked)}
                data-testid="copy-forward-force"
              />
              Overwrite non-empty fields
            </label>
          </>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} className="rounded-sm">
            Cancel
          </Button>
          <Button
            disabled={!selectedId || submitting}
            onClick={() => selectedId && onPick(selectedId, force)}
            data-testid="copy-forward-submit-btn"
            className="rounded-sm"
          >
            <ClipboardCopy className="mr-1.5 h-3.5 w-3.5" />
            Copy forward
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
