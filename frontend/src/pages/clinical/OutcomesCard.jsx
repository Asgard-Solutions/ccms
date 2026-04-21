/**
 * OutcomesCard — longitudinal functional measures with trend plotting.
 *
 * Two modes:
 *   - Latest snapshot (default): one chip per measure_type/label with
 *     score + delta vs prior.
 *   - Trend: compact per-measure SVG line chart, no external charting
 *     library.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Activity, PlusCircle } from "lucide-react";
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
import { Skeleton } from "../../components/ui/skeleton";
import { formatDateTime } from "../../utils/time";

const MEASURES = [
  { value: "ndi", label: "NDI", unit: "%", max: 100 },
  { value: "oswestry", label: "Oswestry", unit: "%", max: 100 },
  { value: "pain_vas", label: "Pain VAS", unit: "", max: 10 },
  { value: "pain_scale", label: "Pain scale", unit: "", max: 10 },
  { value: "functional_index", label: "Functional Index", unit: "", max: null },
  { value: "custom", label: "Custom", unit: "", max: null },
];

export default function OutcomesCard({ patientId, canWrite, onReauthNeeded }) {
  const [mode, setMode] = useState("snapshot");
  const [trends, setTrends] = useState(null);
  const [recordOpen, setRecordOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/patients/${patientId}/clinical/outcomes/trends`);
      setTrends(data.trends || []);
    } catch (e) {
      toast.error(formatApiError(e));
      setTrends([]);
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  const snapshot = useMemo(() => {
    if (!trends) return null;
    return trends.map((t) => {
      const series = t.series || [];
      const latest = series[series.length - 1];
      const prior = series[series.length - 2];
      return {
        key: `${t.measure_type}-${t.label}`,
        measure_type: t.measure_type,
        label: t.label,
        unit: t.unit,
        max_score: t.max_score,
        latest,
        delta: latest && prior ? latest.score - prior.score : null,
        count: series.length,
      };
    });
  }, [trends]);

  return (
    <section data-testid="clinical-outcomes-card" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-lg font-semibold text-foreground">
            Outcomes &amp; Functional Measures
          </h3>
          <p className="text-sm text-muted-foreground">
            Structured patient-reported outcomes trended across the episode.
            Re-Exam sign automatically captures entries here.
          </p>
        </div>
        <div className="flex gap-2">
          <div data-testid="outcomes-mode-tabs" className="flex rounded-sm border border-border p-0.5">
            <button
              type="button"
              onClick={() => setMode("snapshot")}
              data-testid="outcomes-mode-snapshot"
              className={`rounded-sm px-2.5 py-1 text-xs ${
                mode === "snapshot" ? "bg-primary text-primary-foreground" : "text-muted-foreground"
              }`}
            >
              Snapshot
            </button>
            <button
              type="button"
              onClick={() => setMode("trend")}
              data-testid="outcomes-mode-trend"
              className={`rounded-sm px-2.5 py-1 text-xs ${
                mode === "trend" ? "bg-primary text-primary-foreground" : "text-muted-foreground"
              }`}
            >
              Trend
            </button>
          </div>
          {canWrite && (
            <Button
              size="sm"
              onClick={() => setRecordOpen(true)}
              data-testid="outcomes-record-btn"
              className="rounded-sm"
            >
              <PlusCircle className="mr-1.5 h-3.5 w-3.5" />
              Record outcome
            </Button>
          )}
        </div>
      </div>

      {trends === null ? (
        <Skeleton className="h-24 rounded-lg" />
      ) : trends.length === 0 ? (
        <div
          data-testid="outcomes-empty"
          className="rounded-lg border border-dashed border-border bg-card p-8 text-center"
        >
          <Activity className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 font-display text-base font-semibold text-foreground">
            No outcomes recorded
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            Record functional measures (NDI, Oswestry, VAS, etc.) to trend
            patient progress.
          </p>
        </div>
      ) : mode === "snapshot" ? (
        <div data-testid="outcomes-snapshot-grid" className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {snapshot.map((s) => {
            const deltaTone = s.delta == null ? "text-muted-foreground"
              : s.delta < 0 ? "text-success" : s.delta > 0 ? "text-destructive"
              : "text-muted-foreground";
            return (
              <div
                key={s.key}
                data-testid={`outcomes-snapshot-${s.measure_type}`}
                className="rounded-lg border border-border bg-card p-3"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <div className="font-display text-xl font-semibold text-foreground">
                      {s.latest?.score}
                      {s.max_score != null && (
                        <span className="text-sm text-muted-foreground">/{s.max_score}</span>
                      )}
                    </div>
                    <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      {s.label}
                    </div>
                  </div>
                  <Badge
                    variant="outline"
                    data-testid={`outcomes-delta-${s.measure_type}`}
                    className={`text-[10px] ${deltaTone}`}
                  >
                    {s.delta == null
                      ? `${s.count} entry`
                      : s.delta < 0 ? `▼ ${Math.abs(s.delta)}`
                      : s.delta > 0 ? `▲ ${s.delta}` : "·"}
                  </Badge>
                </div>
                <div className="mt-1 text-[10px] text-muted-foreground">
                  {s.latest && `Last: ${formatDateTime(s.latest.captured_at)}`}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div data-testid="outcomes-trend-list" className="space-y-4">
          {trends.map((t) => (
            <TrendChart key={`${t.measure_type}-${t.label}`} trend={t} />
          ))}
        </div>
      )}

      <RecordDialog
        open={recordOpen}
        onOpenChange={setRecordOpen}
        patientId={patientId}
        onSaved={() => {
          setRecordOpen(false);
          load();
        }}
        onReauthNeeded={onReauthNeeded}
      />
    </section>
  );
}

function TrendChart({ trend }) {
  const series = trend.series || [];
  if (series.length === 0) return null;

  const width = 620;
  const height = 160;
  const padding = { top: 18, right: 20, bottom: 28, left: 40 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const max = trend.max_score ?? Math.max(...series.map((s) => s.score), 1);
  const min = 0;
  const dates = series.map((s) => new Date(s.captured_at).getTime());
  const dMin = Math.min(...dates);
  const dMax = Math.max(...dates);
  const span = Math.max(dMax - dMin, 1);

  const points = series.map((s, i) => {
    const x = series.length === 1 ? innerW / 2
      : ((new Date(s.captured_at).getTime() - dMin) / span) * innerW;
    const y = innerH - ((s.score - min) / (max - min || 1)) * innerH;
    return { x: x + padding.left, y: y + padding.top, ...s };
  });

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");

  return (
    <div
      data-testid={`outcomes-trend-${trend.measure_type}`}
      className="rounded-lg border border-border bg-card p-3"
    >
      <div className="mb-2 flex items-center justify-between">
        <div className="font-display text-sm font-semibold text-foreground">
          {trend.label}
        </div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {series.length} entries · range 0–{max}
        </div>
      </div>
      <svg
        data-testid={`outcomes-svg-${trend.measure_type}`}
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
      >
        {/* Axes */}
        <line
          x1={padding.left} y1={padding.top}
          x2={padding.left} y2={height - padding.bottom}
          stroke="currentColor" strokeOpacity="0.2"
        />
        <line
          x1={padding.left} y1={height - padding.bottom}
          x2={width - padding.right} y2={height - padding.bottom}
          stroke="currentColor" strokeOpacity="0.2"
        />
        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const yv = Math.round(min + t * (max - min));
          const y = padding.top + innerH - t * innerH;
          return (
            <g key={t}>
              <line
                x1={padding.left - 4} y1={y}
                x2={padding.left} y2={y}
                stroke="currentColor" strokeOpacity="0.3"
              />
              <text
                x={padding.left - 6} y={y + 3}
                textAnchor="end"
                className="fill-muted-foreground text-[9px]"
              >
                {yv}
              </text>
            </g>
          );
        })}
        {/* Line path */}
        {points.length > 1 && (
          <path d={path} fill="none" className="stroke-primary" strokeWidth="2" />
        )}
        {/* Points */}
        {points.map((p) => (
          <g key={p.entry_id}>
            <circle cx={p.x} cy={p.y} r="3" className="fill-primary" />
            <text
              x={p.x} y={p.y - 8}
              textAnchor="middle"
              className="fill-foreground text-[9px]"
            >
              {p.score}
            </text>
          </g>
        ))}
        {/* X-axis labels */}
        {points.map((p, i) => (
          i === 0 || i === points.length - 1 ? (
            <text
              key={`${p.entry_id}-lbl`}
              x={p.x}
              y={height - padding.bottom + 14}
              textAnchor="middle"
              className="fill-muted-foreground text-[9px]"
            >
              {(p.captured_at || "").slice(0, 10)}
            </text>
          ) : null
        ))}
      </svg>
    </div>
  );
}

function RecordDialog({ open, onOpenChange, patientId, onSaved, onReauthNeeded }) {
  const [form, setForm] = useState({
    measure_type: "pain_vas", label: "Pain VAS",
    score: "", max_score: 10, unit: "", note: "", captured_at: "",
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) {
      setForm({ measure_type: "pain_vas", label: "Pain VAS", score: "", max_score: 10, unit: "", note: "", captured_at: "" });
    }
  }, [open]);

  const onMeasureChange = (v) => {
    const meta = MEASURES.find((m) => m.value === v);
    setForm((f) => ({
      ...f,
      measure_type: v,
      label: meta?.label || f.label,
      unit: meta?.unit || "",
      max_score: meta?.max ?? "",
    }));
  };

  const submit = async () => {
    if (form.score === "") {
      toast.error("Score is required");
      return;
    }
    setSaving(true);
    try {
      const body = {
        measure_type: form.measure_type,
        label: form.label,
        score: Number(form.score),
        max_score: form.max_score === "" ? null : Number(form.max_score),
        unit: form.unit || null,
        captured_at: form.captured_at || null,
        note: form.note || null,
        source: "provider_charted",
      };
      await api.post(`/patients/${patientId}/clinical/outcomes`, body);
      toast.success("Outcome recorded");
      onSaved();
    } catch (e) {
      if (e?.response?.status === 401 && /re-auth/i.test(e.response?.data?.detail || "")) {
        onReauthNeeded?.();
      } else {
        toast.error(formatApiError(e));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="outcomes-record-dialog" className="max-w-md rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Record outcome</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">Measure</Label>
            <Select value={form.measure_type} onValueChange={onMeasureChange}>
              <SelectTrigger data-testid="outcomes-measure-select" className="rounded-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MEASURES.map((m) => (
                  <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">Label</Label>
            <Input
              value={form.label}
              onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
              data-testid="outcomes-record-label"
              className="rounded-sm"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">Score</Label>
              <Input
                type="number"
                value={form.score}
                onChange={(e) => setForm((f) => ({ ...f, score: e.target.value }))}
                data-testid="outcomes-record-score"
                className="rounded-sm"
              />
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">Max</Label>
              <Input
                type="number"
                value={form.max_score ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, max_score: e.target.value }))}
                data-testid="outcomes-record-max"
                className="rounded-sm"
              />
            </div>
          </div>
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Captured at (optional)
            </Label>
            <Input
              type="datetime-local"
              value={form.captured_at}
              onChange={(e) => setForm((f) => ({ ...f, captured_at: e.target.value }))}
              data-testid="outcomes-record-captured"
              className="rounded-sm"
            />
          </div>
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">Note</Label>
            <Textarea
              rows={2}
              value={form.note}
              onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
              data-testid="outcomes-record-note"
              className="rounded-sm"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} className="rounded-sm">Cancel</Button>
          <Button
            onClick={submit}
            disabled={saving}
            data-testid="outcomes-record-submit-btn"
            className="rounded-sm"
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
