/**
 * OutcomeTrendTable — accessible data-table equivalent of
 * `OutcomeTrendChart`. Screen-reader users and print users get every
 * numeric value the chart depicts, plus the superseded entries the
 * chart hides. Never inferences.
 */
import { formatDate } from "../../utils/time";

export default function OutcomeTrendTable({
  series,
  milestones = [],
  testidPrefix,
}) {
  const { points, superseded, unit, max_score } = series;

  const rows = [
    ...points.map((p) => ({ ...p, kind: "point" })),
    ...superseded.map((p) => ({ ...p, kind: "superseded" })),
  ].sort((a, b) => (a.captured_at || "").localeCompare(b.captured_at || ""));

  return (
    <div className="overflow-x-auto" data-testid={`${testidPrefix}-table-wrap`}>
      <table
        className="min-w-full text-left text-xs"
        data-testid={`${testidPrefix}-table`}
      >
        <caption className="sr-only">
          Trend data table for {series.instrument_label}
        </caption>
        <thead className="text-[10px] uppercase tracking-wider text-muted-foreground">
          <tr>
            <th scope="col" className="px-2 py-1.5">Date</th>
            <th scope="col" className="px-2 py-1.5">Score</th>
            <th scope="col" className="px-2 py-1.5">Max</th>
            <th scope="col" className="px-2 py-1.5">Source</th>
            <th scope="col" className="px-2 py-1.5">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={5}
                className="px-2 py-2 text-center text-muted-foreground"
              >
                No entries.
              </td>
            </tr>
          ) : (
            rows.map((r) => (
              <tr
                key={r.entry_id}
                data-testid={`${testidPrefix}-row-${r.entry_id}`}
                className={r.superseded ? "text-muted-foreground line-through" : ""}
              >
                <td className="px-2 py-1.5">
                  {formatDate(r.captured_at)}
                </td>
                <td className="px-2 py-1.5 font-medium text-foreground">
                  {r.score}
                  {unit && <span className="text-muted-foreground">{unit}</span>}
                </td>
                <td className="px-2 py-1.5">{r.max_score ?? max_score ?? "—"}</td>
                <td className="px-2 py-1.5">{r.source || "—"}</td>
                <td className="px-2 py-1.5">
                  {r.superseded
                    ? "Superseded (same day, older revision)"
                    : r.is_amended
                    ? "Amended"
                    : "Original"}
                </td>
              </tr>
            ))
          )}
        </tbody>
        {milestones.length > 0 && (
          <tfoot className="text-[10px] text-muted-foreground">
            {milestones.map((m) => (
              <tr key={`${m.kind}-${m.at}`}>
                <td className="px-2 py-1">{formatDate(m.at)}</td>
                <td colSpan={4} className="px-2 py-1 italic">
                  Milestone: {m.label}
                </td>
              </tr>
            ))}
          </tfoot>
        )}
      </table>
    </div>
  );
}
