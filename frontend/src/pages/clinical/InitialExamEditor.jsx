/**
 * InitialExamEditor — full-page Initial Exam authoring surface.
 *
 * Route: /patients/:pid/clinical/exams/:eid
 *
 * Sectioned editor driven by the server's frozen `template_snapshot`:
 *   - History (prefillable text fields)
 *   - Examination (vitals, text fields, structured ROM/ortho/muscle)
 *   - Assessment & Plan (text fields + diagnoses manager)
 *
 * Status-aware toolbar: Save draft · Prefill from chart · Mark sign-ready ·
 * Sign · View narrative. Signed exams render read-only.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowLeft,
  Check,
  Download,
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
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

import { useAuth } from "../../contexts/AuthContext";
import { useReauth } from "../../components/ReauthGate";
import AddendumPanel from "./AddendumPanel";
import ScribePanel from "../ai/ScribePanel";
import { formatDateTime } from "../../utils/time";

const ROM_REGIONS = ["cervical", "thoracic", "lumbar", "shoulders", "hips"];
const ROM_MOVES = [
  ["flexion", "Flex"],
  ["extension", "Ext"],
  ["left_rotation", "L rot"],
  ["right_rotation", "R rot"],
  ["left_lateral_flexion", "L lat-flex"],
  ["right_lateral_flexion", "R lat-flex"],
];

const STATUS_BADGE = {
  draft: { label: "Draft", class: "border-border bg-card text-muted-foreground" },
  sign_ready: { label: "Sign-ready", class: "border-warning/40 bg-warning-soft text-warning" },
  signed: { label: "Signed", class: "border-success/40 bg-success-soft text-success" },
};

function SectionCard({ title, description, children, testId }) {
  return (
    <section
      data-testid={testId}
      className="rounded-lg border border-border bg-card p-5"
    >
      <div className="mb-4">
        <h3 className="font-display text-lg font-semibold text-foreground">{title}</h3>
        {description && (
          <p className="text-xs text-muted-foreground">{description}</p>
        )}
      </div>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

function TextField({ field, value, onChange, readOnly }) {
  const rows = field.rows || 2;
  return (
    <div className="space-y-1">
      <Label>{field.label}</Label>
      <Textarea
        rows={rows}
        readOnly={readOnly}
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
        data-testid={`exam-history-${field.key}`}
        className="rounded-sm"
      />
    </div>
  );
}

function VitalsField({ value = {}, onChange, readOnly }) {
  const update = (k, v) => onChange({ ...value, [k]: v === "" ? null : v });
  return (
    <div>
      <Label>Vitals</Label>
      <div className="mt-1 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Input
          placeholder="BP 120/80"
          value={value.blood_pressure || ""}
          onChange={(e) => update("blood_pressure", e.target.value)}
          readOnly={readOnly}
          data-testid="exam-vitals-bp"
          className="rounded-sm"
        />
        <Input
          type="number"
          placeholder="Pulse"
          value={value.pulse_bpm ?? ""}
          onChange={(e) => update("pulse_bpm", e.target.value === "" ? null : Number(e.target.value))}
          readOnly={readOnly}
          data-testid="exam-vitals-pulse"
          className="rounded-sm"
        />
        <Input
          type="number"
          placeholder="RR"
          value={value.respiratory_rate ?? ""}
          onChange={(e) => update("respiratory_rate", e.target.value === "" ? null : Number(e.target.value))}
          readOnly={readOnly}
          className="rounded-sm"
        />
        <Input
          type="number"
          step="0.1"
          placeholder="Temp °F"
          value={value.temperature_f ?? ""}
          onChange={(e) => update("temperature_f", e.target.value === "" ? null : Number(e.target.value))}
          readOnly={readOnly}
          className="rounded-sm"
        />
        <Input
          type="number"
          step="0.1"
          placeholder="Ht in"
          value={value.height_in ?? ""}
          onChange={(e) => update("height_in", e.target.value === "" ? null : Number(e.target.value))}
          readOnly={readOnly}
          className="rounded-sm"
        />
        <Input
          type="number"
          step="0.1"
          placeholder="Wt lb"
          value={value.weight_lb ?? ""}
          onChange={(e) => update("weight_lb", e.target.value === "" ? null : Number(e.target.value))}
          readOnly={readOnly}
          className="rounded-sm"
        />
        <Input
          type="number"
          placeholder="O2 sat %"
          value={value.o2_sat_pct ?? ""}
          onChange={(e) => update("o2_sat_pct", e.target.value === "" ? null : Number(e.target.value))}
          readOnly={readOnly}
          className="rounded-sm"
        />
      </div>
    </div>
  );
}

function ROMField({ value = {}, onChange, readOnly }) {
  const update = (region, move, v) => {
    const region_data = { ...(value[region] || {}), [move]: v || null };
    onChange({ ...value, [region]: region_data });
  };
  return (
    <div className="space-y-2">
      <Label>Range of motion</Label>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] uppercase text-muted-foreground">
              <th className="w-24 font-normal">Region</th>
              {ROM_MOVES.map(([k, lbl]) => (
                <th key={k} className="font-normal">{lbl}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ROM_REGIONS.map((region) => (
              <tr key={region}>
                <td className="py-1 font-medium capitalize">{region}</td>
                {ROM_MOVES.map(([k]) => (
                  <td key={k} className="py-1 pr-1">
                    <Input
                      value={value[region]?.[k] || ""}
                      onChange={(e) => update(region, k, e.target.value)}
                      readOnly={readOnly}
                      placeholder="—"
                      className="h-8 rounded-sm"
                      data-testid={`exam-rom-${region}-${k}`}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function OrthoField({ value = [], onChange, readOnly }) {
  const update = (i, patch) => {
    const next = [...value];
    next[i] = { ...next[i], ...patch };
    onChange(next);
  };
  const add = () => onChange([...value, { name: "", region: "", result: null, notes: "" }]);
  const remove = (i) => onChange(value.filter((_, idx) => idx !== i));
  return (
    <div className="space-y-2">
      <Label>Orthopedic tests</Label>
      {value.length === 0 && !readOnly && (
        <p className="text-xs text-muted-foreground">No orthopedic tests recorded yet.</p>
      )}
      {value.map((t, i) => (
        <div
          key={i}
          className="grid grid-cols-[2fr_1fr_1fr_2fr_auto] gap-2 rounded-sm border border-border bg-background p-2"
          data-testid={`exam-ortho-row-${i}`}
        >
          <Input
            placeholder="Test name"
            value={t.name || ""}
            onChange={(e) => update(i, { name: e.target.value })}
            readOnly={readOnly}
            className="rounded-sm"
          />
          <Input
            placeholder="Region"
            value={t.region || ""}
            onChange={(e) => update(i, { region: e.target.value })}
            readOnly={readOnly}
            className="rounded-sm"
          />
          <Select
            value={t.result || "__unset"}
            onValueChange={(v) => update(i, { result: v === "__unset" ? null : v })}
            disabled={readOnly}
          >
            <SelectTrigger className="h-9 rounded-sm">
              <SelectValue placeholder="Result" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__unset">—</SelectItem>
              <SelectItem value="positive">Positive</SelectItem>
              <SelectItem value="negative">Negative</SelectItem>
              <SelectItem value="equivocal">Equivocal</SelectItem>
            </SelectContent>
          </Select>
          <Input
            placeholder="Notes"
            value={t.notes || ""}
            onChange={(e) => update(i, { notes: e.target.value })}
            readOnly={readOnly}
            className="rounded-sm"
          />
          {!readOnly && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => remove(i)}
              className="rounded-sm"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      ))}
      {!readOnly && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={add}
          data-testid="exam-ortho-add"
          className="rounded-sm"
        >
          <PlusCircle className="mr-1 h-3.5 w-3.5" />
          Add test
        </Button>
      )}
    </div>
  );
}

function MuscleStrengthField({ value = [], onChange, readOnly }) {
  const update = (i, patch) => {
    const next = [...value];
    next[i] = { ...next[i], ...patch };
    onChange(next);
  };
  const add = () => onChange([...value, { muscle: "", grade: null, side: null, notes: "" }]);
  const remove = (i) => onChange(value.filter((_, idx) => idx !== i));
  return (
    <div className="space-y-2">
      <Label>Muscle strength</Label>
      {value.map((m, i) => (
        <div
          key={i}
          className="grid grid-cols-[2fr_1fr_1fr_2fr_auto] gap-2 rounded-sm border border-border bg-background p-2"
          data-testid={`exam-ms-row-${i}`}
        >
          <Input
            placeholder="Muscle"
            value={m.muscle || ""}
            onChange={(e) => update(i, { muscle: e.target.value })}
            readOnly={readOnly}
            className="rounded-sm"
          />
          <Select
            value={String(m.grade ?? "__unset")}
            onValueChange={(v) => update(i, { grade: v === "__unset" ? null : Number(v) })}
            disabled={readOnly}
          >
            <SelectTrigger className="h-9 rounded-sm">
              <SelectValue placeholder="Grade" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__unset">—</SelectItem>
              {[0, 1, 2, 3, 4, 5].map((g) => (
                <SelectItem key={g} value={String(g)}>
                  {g}/5
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={m.side || "__unset"}
            onValueChange={(v) => update(i, { side: v === "__unset" ? null : v })}
            disabled={readOnly}
          >
            <SelectTrigger className="h-9 rounded-sm">
              <SelectValue placeholder="Side" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__unset">—</SelectItem>
              <SelectItem value="left">Left</SelectItem>
              <SelectItem value="right">Right</SelectItem>
              <SelectItem value="bilateral">Bilateral</SelectItem>
            </SelectContent>
          </Select>
          <Input
            placeholder="Notes"
            value={m.notes || ""}
            onChange={(e) => update(i, { notes: e.target.value })}
            readOnly={readOnly}
            className="rounded-sm"
          />
          {!readOnly && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => remove(i)}
              className="rounded-sm"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      ))}
      {!readOnly && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={add}
          data-testid="exam-ms-add"
          className="rounded-sm"
        >
          <PlusCircle className="mr-1 h-3.5 w-3.5" />
          Add muscle
        </Button>
      )}
    </div>
  );
}

function DiagnosesField({
  existingDiagnoses,
  selectedIds,
  onSelectedChange,
  newDiagnoses,
  onNewChange,
  readOnly,
}) {
  const toggle = (id) => {
    const set = new Set(selectedIds);
    if (set.has(id)) set.delete(id);
    else set.add(id);
    onSelectedChange(Array.from(set));
  };
  const addNew = () =>
    onNewChange([
      ...newDiagnoses,
      { icd10_code: "", label: "", body_region: "", laterality: null, chronicity: null, is_primary: false },
    ]);
  const updateNew = (i, patch) => {
    const next = [...newDiagnoses];
    next[i] = { ...next[i], ...patch };
    onNewChange(next);
  };
  const removeNew = (i) => onNewChange(newDiagnoses.filter((_, idx) => idx !== i));
  return (
    <div className="space-y-3">
      <Label>Diagnoses</Label>
      {existingDiagnoses.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs uppercase tracking-wider text-muted-foreground">
            From problem list
          </p>
          {existingDiagnoses.map((dx) => (
            <label
              key={dx.id}
              className="flex items-start gap-2 rounded-sm border border-border bg-background p-2 text-sm"
            >
              <input
                type="checkbox"
                checked={selectedIds.includes(dx.id)}
                disabled={readOnly}
                onChange={() => toggle(dx.id)}
                data-testid={`exam-existing-dx-${dx.id}`}
                className="mt-0.5 h-4 w-4"
              />
              <div className="flex-1">
                <span className="font-mono font-semibold">{dx.icd10_code}</span>{" "}
                {dx.label}
                <span className="ml-2 text-xs text-muted-foreground">
                  ({dx.status}
                  {dx.is_primary ? " · primary" : ""})
                </span>
              </div>
            </label>
          ))}
        </div>
      )}
      <div className="space-y-2">
        <p className="text-xs uppercase tracking-wider text-muted-foreground">
          New diagnoses (materialized on sign)
        </p>
        {newDiagnoses.map((d, i) => (
          <div
            key={i}
            className="grid grid-cols-[1fr_2fr_1fr_1fr_1fr_auto] gap-2 rounded-sm border border-border bg-background p-2"
            data-testid={`exam-new-dx-row-${i}`}
          >
            <Input
              placeholder="ICD-10"
              value={d.icd10_code || ""}
              onChange={(e) => updateNew(i, { icd10_code: e.target.value.toUpperCase() })}
              readOnly={readOnly}
              className="rounded-sm font-mono"
            />
            <Input
              placeholder="Label"
              value={d.label || ""}
              onChange={(e) => updateNew(i, { label: e.target.value })}
              readOnly={readOnly}
              className="rounded-sm"
            />
            <Input
              placeholder="Region"
              value={d.body_region || ""}
              onChange={(e) => updateNew(i, { body_region: e.target.value })}
              readOnly={readOnly}
              className="rounded-sm"
            />
            <Select
              value={d.laterality || "__unset"}
              onValueChange={(v) => updateNew(i, { laterality: v === "__unset" ? null : v })}
              disabled={readOnly}
            >
              <SelectTrigger className="h-9 rounded-sm">
                <SelectValue placeholder="Laterality" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__unset">—</SelectItem>
                <SelectItem value="left">Left</SelectItem>
                <SelectItem value="right">Right</SelectItem>
                <SelectItem value="bilateral">Bilateral</SelectItem>
                <SelectItem value="midline">Midline</SelectItem>
              </SelectContent>
            </Select>
            <label className="flex items-center gap-1 text-xs">
              <input
                type="checkbox"
                checked={!!d.is_primary}
                disabled={readOnly}
                onChange={(e) => updateNew(i, { is_primary: e.target.checked })}
              />
              Primary
            </label>
            {!readOnly && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => removeNew(i)}
                className="rounded-sm"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            )}
          </div>
        ))}
        {!readOnly && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={addNew}
            data-testid="exam-new-dx-add"
            className="rounded-sm"
          >
            <PlusCircle className="mr-1 h-3.5 w-3.5" />
            Add new diagnosis
          </Button>
        )}
      </div>
    </div>
  );
}

function NarrativeDialog({ open, onOpenChange, text, generatedAt }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="exam-narrative-dialog" className="max-w-3xl rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Initial Exam — narrative</DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          Server-rendered summary. {generatedAt ? `Generated ${formatDateTime(generatedAt)}.` : ""}
        </p>
        <pre
          data-testid="exam-narrative-text"
          className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-sm border border-border bg-muted/30 p-4 text-xs leading-relaxed"
        >
          {text}
        </pre>
      </DialogContent>
    </Dialog>
  );
}

export default function InitialExamEditor() {
  const { pid, eid } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { requestReauth } = useReauth();
  const canWrite = ["admin", "doctor"].includes(user?.role);

  const [exam, setExam] = useState(null);
  const [existingDx, setExistingDx] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [acting, setActing] = useState(false);
  const [narrative, setNarrative] = useState(null);

  // Section local state mirrors exam's subdocuments so typing is snappy.
  const [history, setHistory] = useState({});
  const [examination, setExamination] = useState({});
  const [assessment, setAssessment] = useState({});
  const [diagnosisIds, setDiagnosisIds] = useState([]);
  const [newDiagnoses, setNewDiagnoses] = useState([]);

  const readOnly = !canWrite || exam?.status === "signed";

  // Map AI/scribe SOAP output into the Initial Exam's sub-document shape.
  // Subjective -> history.history_of_present_illness
  // Objective  -> examination.observation_inspection
  // Assessment -> assessment.initial_clinical_impression
  // Plan       -> assessment.treatment_recommendations
  const applySectionFromAi = useCallback((section, text) => {
    if (!text) return;
    if (section === "subjective") {
      setHistory((s) => ({
        ...(s || {}),
        history_of_present_illness: [
          (s && s.history_of_present_illness) || "", text,
        ].filter(Boolean).join("\n\n"),
      }));
    } else if (section === "objective") {
      setExamination((s) => ({
        ...(s || {}),
        observation_inspection: [
          (s && s.observation_inspection) || "", text,
        ].filter(Boolean).join("\n\n"),
      }));
    } else if (section === "assessment") {
      setAssessment((s) => ({
        ...(s || {}),
        initial_clinical_impression: [
          (s && s.initial_clinical_impression) || "", text,
        ].filter(Boolean).join("\n\n"),
      }));
    } else if (section === "plan") {
      setAssessment((s) => ({
        ...(s || {}),
        treatment_recommendations: [
          (s && s.treatment_recommendations) || "", text,
        ].filter(Boolean).join("\n\n"),
      }));
    }
    toast.success(`Pulled into ${section}.`);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [examRes, dxRes] = await Promise.all([
        api.get(`/patients/${pid}/clinical/exams/${eid}`),
        api.get(`/patients/${pid}/clinical/diagnoses`, { params: { status_in: "active,resolved" } }),
      ]);
      setExam(examRes.data);
      setHistory(examRes.data.history || {});
      setExamination(examRes.data.examination || {});
      setAssessment(examRes.data.assessment || {});
      setDiagnosisIds(examRes.data.diagnosis_ids || []);
      setNewDiagnoses(examRes.data.new_diagnoses || []);
      setExistingDx(dxRes.data || []);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [pid, eid]);

  useEffect(() => {
    load();
  }, [load]);

  const dirty = useMemo(() => {
    if (!exam) return false;
    return (
      JSON.stringify(history) !== JSON.stringify(exam.history || {}) ||
      JSON.stringify(examination) !== JSON.stringify(exam.examination || {}) ||
      JSON.stringify(assessment) !== JSON.stringify(exam.assessment || {}) ||
      JSON.stringify(diagnosisIds) !== JSON.stringify(exam.diagnosis_ids || []) ||
      JSON.stringify(newDiagnoses) !== JSON.stringify(exam.new_diagnoses || [])
    );
  }, [exam, history, examination, assessment, diagnosisIds, newDiagnoses]);

  const save = async () => {
    if (saving || readOnly) return;
    setSaving(true);
    try {
      const { data } = await api.patch(
        `/patients/${pid}/clinical/exams/${eid}`,
        {
          history,
          examination,
          assessment,
          diagnosis_ids: diagnosisIds,
          new_diagnoses: newDiagnoses,
        },
      );
      setExam(data);
      toast.success("Draft saved");
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setSaving(false);
    }
  };

  const prefill = async () => {
    setActing(true);
    try {
      const { data } = await api.post(
        `/patients/${pid}/clinical/exams/${eid}/prefill`,
        {},
      );
      setExam(data);
      setHistory(data.history || {});
      setDiagnosisIds(data.diagnosis_ids || []);
      toast.success("Prefilled empty fields from chart");
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setActing(false);
    }
  };

  const transition = async (action) => {
    setActing(true);
    try {
      const { data } = await api.post(
        `/patients/${pid}/clinical/exams/${eid}/${action}`,
        {},
      );
      setExam(data);
      setHistory(data.history || {});
      setDiagnosisIds(data.diagnosis_ids || []);
      setNewDiagnoses(data.new_diagnoses || []);
      // After sign, reload diagnoses list (materialized rows may exist).
      if (action === "sign") {
        const dxRes = await api.get(`/patients/${pid}/clinical/diagnoses`, {
          params: { status_in: "active,resolved" },
        });
        setExistingDx(dxRes.data || []);
        toast.success("Exam signed");
      } else if (action === "mark-sign-ready") {
        toast.success("Marked sign-ready");
      } else if (action === "unmark-sign-ready") {
        toast.success("Reverted to draft");
      }
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
        `/patients/${pid}/clinical/exams/${eid}/narrative`,
      );
      setNarrative(data);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setActing(false);
    }
  };

  const downloadText = () => {
    if (!narrative?.narrative) return;
    const blob = new Blob([narrative.narrative], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `initial-exam-${eid}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const sections = exam?.template_snapshot?.sections || [];

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!exam) {
    return (
      <div className="p-6 text-sm text-muted-foreground">Exam not found.</div>
    );
  }

  const statusBadge = STATUS_BADGE[exam.status] || STATUS_BADGE.draft;

  return (
    <>
      <div
        data-testid="initial-exam-editor"
        className="mx-auto max-w-4xl space-y-6 p-4 sm:p-6"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <Link
              to={`/patients/${pid}?tab=clinical`}
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              data-testid="exam-back-link"
            >
              <ArrowLeft className="h-3 w-3" /> Back to chart
            </Link>
            <h1 className="mt-1 font-display text-2xl font-semibold text-foreground">
              Initial Exam
            </h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>DOS · {formatDateTime(exam.date_of_service)}</span>
              {exam.provider_name && <span>Provider · {exam.provider_name}</span>}
              {exam.episode_title && <span>Episode · {exam.episode_title}</span>}
              <Badge
                variant="outline"
                data-testid="exam-status-badge"
                className={`text-[10px] uppercase tracking-wider ${statusBadge.class}`}
              >
                {statusBadge.label}
                {exam.status === "signed" && (exam.addendum_count || 0) > 0
                  ? ` · +${exam.addendum_count} addendum${exam.addendum_count === 1 ? "" : "s"}`
                  : ""}
              </Badge>
              {exam.signed_at && (
                <span>
                  Signed {formatDateTime(exam.signed_at)}
                  {exam.signed_by_name ? ` · ${exam.signed_by_name}` : ""}
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {canWrite && exam.status !== "signed" && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={prefill}
                  disabled={acting}
                  data-testid="exam-prefill-btn"
                  className="rounded-sm"
                >
                  <Download className="mr-1.5 h-3.5 w-3.5" />
                  Prefill from chart
                </Button>
                <Button
                  size="sm"
                  onClick={save}
                  disabled={saving || !dirty}
                  data-testid="exam-save-btn"
                  className="rounded-sm"
                >
                  <Save className="mr-1.5 h-3.5 w-3.5" />
                  {saving ? "Saving…" : dirty ? "Save draft" : "Saved"}
                </Button>
                {exam.status === "draft" && (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => transition("mark-sign-ready")}
                    disabled={acting || dirty}
                    title={dirty ? "Save first" : undefined}
                    data-testid="exam-mark-ready-btn"
                    className="rounded-sm"
                  >
                    <Edit3 className="mr-1.5 h-3.5 w-3.5" />
                    Mark sign-ready
                  </Button>
                )}
                {exam.status === "sign_ready" && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => transition("unmark-sign-ready")}
                    disabled={acting}
                    data-testid="exam-unmark-ready-btn"
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
                  data-testid="exam-sign-btn"
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
              data-testid="exam-narrative-btn"
              className="rounded-sm"
            >
              <FileText className="mr-1.5 h-3.5 w-3.5" />
              View narrative
            </Button>
          </div>
        </div>

        {/* AI scribe — doctor-only, hidden on signed exams. */}
        {user?.role === "doctor" && exam.status !== "signed" && (
          <ScribePanel
            noteId={eid}
            noteType="initial_exam"
            disabled={!canWrite}
            onApplySection={applySectionFromAi}
          />
        )}

        {sections.map((section) => (
          <SectionCard
            key={section.id}
            title={section.title}
            description={section.description}
            testId={`exam-section-${section.id}`}
          >
            {section.fields.map((field) => {
              if (field.type === "textarea") {
                const source = section.id === "history" ? history :
                  section.id === "assessment" ? assessment : examination;
                const set = section.id === "history" ? setHistory :
                  section.id === "assessment" ? setAssessment : setExamination;
                return (
                  <TextField
                    key={field.key}
                    field={field}
                    value={source[field.key]}
                    onChange={(v) => set((s) => ({ ...s, [field.key]: v || null }))}
                    readOnly={readOnly}
                  />
                );
              }
              if (field.type === "vitals") {
                return (
                  <VitalsField
                    key={field.key}
                    value={examination.vitals || {}}
                    onChange={(v) => setExamination((e) => ({ ...e, vitals: v }))}
                    readOnly={readOnly}
                  />
                );
              }
              if (field.type === "rom") {
                return (
                  <ROMField
                    key={field.key}
                    value={examination.range_of_motion || {}}
                    onChange={(v) =>
                      setExamination((e) => ({ ...e, range_of_motion: v }))
                    }
                    readOnly={readOnly}
                  />
                );
              }
              if (field.type === "orthopedic_tests") {
                return (
                  <OrthoField
                    key={field.key}
                    value={examination.orthopedic_tests || []}
                    onChange={(v) =>
                      setExamination((e) => ({ ...e, orthopedic_tests: v }))
                    }
                    readOnly={readOnly}
                  />
                );
              }
              if (field.type === "muscle_strength") {
                return (
                  <MuscleStrengthField
                    key={field.key}
                    value={examination.muscle_strength || []}
                    onChange={(v) =>
                      setExamination((e) => ({ ...e, muscle_strength: v }))
                    }
                    readOnly={readOnly}
                  />
                );
              }
              if (field.type === "diagnoses") {
                return (
                  <DiagnosesField
                    key={field.key}
                    existingDiagnoses={existingDx}
                    selectedIds={diagnosisIds}
                    onSelectedChange={setDiagnosisIds}
                    newDiagnoses={newDiagnoses}
                    onNewChange={setNewDiagnoses}
                    readOnly={readOnly}
                  />
                );
              }
              return null;
            })}
          </SectionCard>
        ))}

        {exam.status === "signed" && (
          <div
            data-testid="exam-signed-banner"
            className="rounded-lg border border-success/30 bg-success-soft p-4 text-sm"
          >
            <div className="flex items-center gap-2 font-semibold text-success">
              <Check className="h-4 w-4" />
              Signed
            </div>
            <p className="mt-1 text-xs text-success/80">
              This exam is a permanent chart artifact and cannot be edited.
              New findings should be documented in a follow-up note or re-exam,
              or appended through a signed addendum below.
            </p>
          </div>
        )}

        <AddendumPanel
          patientId={pid}
          parentType="initial_exam"
          parentId={eid}
          parentSigned={exam.status === "signed"}
          canWrite={canWrite}
          currentUser={user}
          onChanged={load}
          onReauthNeeded={() => requestReauth(async () => load())}
        />

        <div className="flex justify-end">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate(`/patients/${pid}?tab=clinical`)}
            className="rounded-sm"
          >
            <ArrowLeft className="mr-1 h-3.5 w-3.5" />
            Back to chart
          </Button>
        </div>
      </div>

      <NarrativeDialog
        open={!!narrative}
        onOpenChange={(v) => !v && setNarrative(null)}
        text={narrative?.narrative || ""}
        generatedAt={narrative?.generated_at}
      />
      {narrative && (
        <button
          onClick={downloadText}
          data-testid="exam-narrative-download-btn"
          className="hidden"
        />
      )}
    </>
  );
}
