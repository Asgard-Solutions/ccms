/**
 * Patient-facing questionnaire detail + submit.
 *
 * Renders a generic survey form from the template definition. Supports
 * three item types:
 *   - `scale`    — numeric slider input
 *   - `choice`   — radio list (ODI/NDI rows)
 *   - `activity` — text label + 0–10 rating (PSFS)
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { CheckCircle2 } from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { getMyQuestionnaire, submitMyQuestionnaire } from "../api/portal";

function ScaleItem({ item, value, onChange }) {
  return (
    <div className="space-y-2">
      <Label>{item.prompt}</Label>
      <input
        type="range"
        min={item.min ?? 0}
        max={item.max ?? 10}
        step={item.step ?? 1}
        value={value ?? item.min ?? 0}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
        data-testid={`portal-q-scale-${item.id}`}
      />
      <p className="text-sm font-medium">
        {item.prompt.startsWith("Pain") ? `Pain: ${value ?? 0}/10` : `Value: ${value ?? 0}`}
      </p>
    </div>
  );
}

function ChoiceItem({ item, value, onChange }) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">{item.prompt}</legend>
      <ul className="space-y-1.5">
        {item.choices.map((c) => (
          <li key={c.value}>
            <label
              className={`flex items-start gap-2 rounded-sm border p-2.5 cursor-pointer transition
                ${value === c.value ? "border-primary bg-primary/5" : "border-border/60 hover:bg-muted/40"}`}
              data-testid={`portal-q-choice-${item.id}-${c.value}`}
            >
              <input
                type="radio"
                name={item.id}
                value={c.value}
                checked={value === c.value}
                onChange={() => onChange(c.value)}
                className="mt-0.5"
              />
              <span className="text-sm">{c.label}</span>
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}

function ActivityItem({ item, value, onChange }) {
  const v = value || { name: "", rating: 0 };
  return (
    <div className="space-y-2 rounded-sm border border-border/60 p-3">
      <Label>{item.prompt}</Label>
      <Input
        placeholder="Activity (e.g. jogging 1 mile)"
        value={v.name}
        onChange={(e) => onChange({ ...v, name: e.target.value })}
        data-testid={`portal-q-activity-name-${item.id}`}
      />
      <input
        type="range"
        min={0}
        max={10}
        step={1}
        value={v.rating ?? 0}
        onChange={(e) => onChange({ ...v, rating: Number(e.target.value) })}
        className="w-full"
        data-testid={`portal-q-activity-rating-${item.id}`}
      />
      <p className="text-xs text-muted-foreground">Ability: {v.rating ?? 0}/10</p>
    </div>
  );
}

export default function PortalQuestionnaireDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [answers, setAnswers] = useState({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);

  const load = useCallback(async () => {
    try {
      const r = await getMyQuestionnaire(id);
      setData(r);
      setAnswers(r.assignment?.answers || {});
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const completed = useMemo(
    () => data?.assignment?.status === "completed",
    [data]
  );

  // Answer validation — must have a non-null response for every
  // non-optional item before submit is enabled.
  const canSubmit = useMemo(() => {
    const tpl = data?.template;
    if (!tpl) return false;
    for (const item of tpl.items) {
      if (item.optional) continue;
      const v = answers[item.id];
      if (item.type === "scale") {
        if (v === undefined || v === null) return false;
      } else if (item.type === "choice") {
        if (v === undefined || v === null) return false;
      } else if (item.type === "activity") {
        if (!v || !v.name || (v.rating ?? null) === null) return false;
      }
    }
    return true;
  }, [answers, data]);

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const res = await submitMyQuestionnaire(id, answers);
      setResult(res);
      toast.success(`Submitted. Score: ${res.score}`);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to submit");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (!data) return null;
  const tpl = data.template;

  return (
    <div data-testid="portal-questionnaire-detail" className="max-w-2xl space-y-6">
      <header>
        <h1 className="text-2xl font-display tracking-tight">{tpl.title}</h1>
        <p className="text-sm text-muted-foreground">{tpl.description}</p>
      </header>

      {completed || result ? (
        <div
          data-testid="portal-q-completed"
          className="rounded-md border border-green-600/40 bg-green-50 p-6 text-center"
        >
          <CheckCircle2 className="mx-auto h-8 w-8 text-green-600" />
          <p className="mt-2 font-medium">Thank you — your answers are in.</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Score: {result?.score ?? data.assignment.score} ·{" "}
            {result?.interpretation ?? data.assignment.interpretation}
          </p>
          <Button
            onClick={() => navigate("/portal")}
            className="mt-4"
            data-testid="portal-q-back-btn"
          >
            Back to overview
          </Button>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-5" data-testid="portal-q-form">
          {tpl.items.map((item) => {
            const v = answers[item.id];
            const set = (val) =>
              setAnswers((a) => ({ ...a, [item.id]: val }));
            if (item.type === "scale")
              return <ScaleItem key={item.id} item={item} value={v} onChange={set} />;
            if (item.type === "choice")
              return <ChoiceItem key={item.id} item={item} value={v} onChange={set} />;
            if (item.type === "activity")
              return <ActivityItem key={item.id} item={item} value={v} onChange={set} />;
            return null;
          })}
          <div className="flex justify-end">
            <Button
              type="submit"
              disabled={submitting || !canSubmit}
              data-testid="portal-q-submit-btn"
            >
              {submitting ? "Submitting…" : "Submit answers"}
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
