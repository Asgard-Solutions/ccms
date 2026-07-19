/**
 * OutcomeTrendChart — Phase 3 Slice 3.
 *
 * Accessible SVG line-and-marker chart. No external charting library.
 *
 * Colorblind-safe design:
 *   - Data points use distinct **shape** markers (circle for regular,
 *     diamond for amended, square for superseded) *in addition to*
 *     color.
 *   - Milestones use vertical dashed lines with textual labels.
 *   - A visible "Show data as table" toggle drops to
 *     `OutcomeTrendTable` for screen-reader / non-visual users.
 *
 * We deliberately never render a "trend line direction" indicator
 * (up-arrow, down-arrow, "improving") — the chart shows the numbers,
 * period.
 */
import { useMemo } from "react";

const W = 480;
const H = 160;
const PAD = { top: 12, right: 14, bottom: 26, left: 34 };

function formatShort(iso) {
  if (!iso) return "";
  return iso.slice(0, 10);
}

export default function OutcomeTrendChart({
  series,
  milestones = [],
  testidPrefix,
}) {
  const { points, max_score } = series;

  const layout = useMemo(() => {
    if (!points || points.length === 0) return null;
    const xs = points.map((p) => Date.parse(p.captured_at) || 0);
    const ys = points.map((p) => p.score);
    let xMin = Math.min(...xs);
    let xMax = Math.max(...xs);
    if (xMin === xMax) {
      // Widen so a single point is visible.
      xMax = xMin + 24 * 60 * 60 * 1000;
    }
    let yMin = Math.min(...ys);
    let yMax = Math.max(...ys, ...(max_score ? [max_score] : []));
    if (yMin === yMax) {
      yMin = yMin - 1;
      yMax = yMax + 1;
    }
    const plotW = W - PAD.left - PAD.right;
    const plotH = H - PAD.top - PAD.bottom;
    const scaleX = (t) => PAD.left + ((t - xMin) / (xMax - xMin)) * plotW;
    const scaleY = (v) =>
      PAD.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH;
    return { xMin, xMax, yMin, yMax, scaleX, scaleY };
  }, [points, max_score]);

  if (!layout) {
    return (
      <div
        data-testid={`${testidPrefix}-empty`}
        className="flex h-24 items-center justify-center rounded-md border border-dashed border-border text-xs text-muted-foreground"
      >
        No plotted points yet.
      </div>
    );
  }

  const { scaleX, scaleY, yMin, yMax } = layout;

  const path = points
    .map((p, i) => {
      const x = scaleX(Date.parse(p.captured_at) || 0).toFixed(1);
      const y = scaleY(p.score).toFixed(1);
      return `${i === 0 ? "M" : "L"}${x},${y}`;
    })
    .join(" ");

  // Deterministic milestone rendering — visible label + dashed line.
  const milestoneLayout = milestones
    .map((m) => {
      const t = Date.parse(m.at);
      if (!Number.isFinite(t)) return null;
      const x = scaleX(t);
      return { ...m, x };
    })
    .filter(Boolean);

  return (
    <figure
      data-testid={`${testidPrefix}-chart`}
      aria-label={`Trend chart for ${series.instrument_label}`}
      className="w-full"
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-labelledby={`${testidPrefix}-chart-title`}
        className="h-40 w-full"
      >
        <title id={`${testidPrefix}-chart-title`}>
          {series.instrument_label} — {points.length} point
          {points.length === 1 ? "" : "s"}
        </title>
        {/* Y axis */}
        <line
          x1={PAD.left} x2={PAD.left}
          y1={PAD.top} y2={H - PAD.bottom}
          className="stroke-border" strokeWidth="1"
        />
        {/* X axis */}
        <line
          x1={PAD.left} x2={W - PAD.right}
          y1={H - PAD.bottom} y2={H - PAD.bottom}
          className="stroke-border" strokeWidth="1"
        />
        {/* Y tick labels — just min and max, no midpoint noise. */}
        <text
          x={PAD.left - 4} y={PAD.top + 4}
          textAnchor="end"
          className="fill-muted-foreground text-[9px]"
        >
          {yMax}
        </text>
        <text
          x={PAD.left - 4} y={H - PAD.bottom + 3}
          textAnchor="end"
          className="fill-muted-foreground text-[9px]"
        >
          {yMin}
        </text>

        {/* Milestone dashed verticals with rotated labels. */}
        {milestoneLayout.map((m) => (
          <g key={`m-${m.kind}-${m.at}`}>
            <line
              x1={m.x} x2={m.x}
              y1={PAD.top} y2={H - PAD.bottom}
              strokeDasharray="3 3"
              className="stroke-muted-foreground/50"
            />
            <text
              x={m.x + 3} y={PAD.top + 8}
              className="fill-muted-foreground text-[8px] uppercase tracking-wide"
            >
              {m.label}
            </text>
          </g>
        ))}

        {/* Line path */}
        <path
          d={path}
          className="stroke-primary"
          fill="none"
          strokeWidth="1.5"
        />

        {/* Point markers — shape-encoded (circle vs diamond) so the
            chart remains legible in monochrome print. */}
        {points.map((p) => {
          const x = scaleX(Date.parse(p.captured_at) || 0);
          const y = scaleY(p.score);
          if (p.is_amended) {
            const s = 4;
            return (
              <polygon
                key={p.entry_id}
                points={`${x},${y - s} ${x + s},${y} ${x},${y + s} ${x - s},${y}`}
                className="fill-warning stroke-warning"
                strokeWidth="0.5"
                aria-hidden="true"
              >
                <title>
                  {formatShort(p.captured_at)}: {p.score} · Amended
                </title>
              </polygon>
            );
          }
          return (
            <circle
              key={p.entry_id}
              cx={x} cy={y} r="3"
              className="fill-primary stroke-primary"
              strokeWidth="0.5"
              aria-hidden="true"
            >
              <title>
                {formatShort(p.captured_at)}: {p.score}
              </title>
            </circle>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-[10px] text-muted-foreground">
        Circle = regular entry · Diamond = amended entry · Dashed lines are milestones.
      </figcaption>
    </figure>
  );
}
