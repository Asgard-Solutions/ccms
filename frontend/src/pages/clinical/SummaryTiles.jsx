/**
 * SummaryTiles — six interactive count tiles at the top of the
 * Clinical redesign. Each tile is a real <button> that jumps to a
 * mapped section.
 */
import { Skeleton } from "../../components/ui/skeleton";

const TILES = [
  { key: "encounters",      label: "Visits",     jump: "encounters", src: "encounters",      filterHint: null },
  { key: "initial_exams",   label: "Exams",      jump: "encounters", src: "initial_exams",   filterHint: "initial_exams" },
  { key: "treatment_plans", label: "Plans",      jump: "care-plan",  src: "treatment_plans", filterHint: null },
  { key: "re_exams",        label: "Re-exams",   jump: "care-plan",  src: "re_exams",        filterHint: "re_exams" },
  { key: "notes",           label: "Notes",      jump: "encounters", src: "notes",           filterHint: "missing_note" },
  { key: "diagnoses",       label: "Diagnoses",  jump: "diagnoses",  src: "diagnoses",       filterHint: null },
];

export default function SummaryTiles({ summary, onJumpTo }) {
  if (summary === null) {
    return (
      <div
        data-testid="clinical-summary-tiles-loading"
        className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6"
      >
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-20 rounded-lg" />
        ))}
      </div>
    );
  }

  return (
    <div
      data-testid="clinical-summary-tiles"
      className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6"
    >
      {TILES.map((t) => {
        const data = summary?.[t.src] || {};
        const total = data.total ?? 0;
        const open = data.open ?? 0;
        // Item 8: tiles pass a lightweight filter hint into the destination
        // section via `sessionStorage` — the destination card reads and
        // clears the hint on first render. No PHI, key is global.
        const applyFilterHint = () => {
          try {
            if (t.filterHint) {
              window.sessionStorage.setItem(
                `ccms.clinical.filterHint.${t.jump}`,
                JSON.stringify({ from: t.key, hint: t.filterHint, ts: Date.now() }),
              );
            }
          } catch {
            /* ignore quota / private mode */
          }
          onJumpTo(t.jump, { userInitiated: true });
        };
        return (
          <button
            key={t.key}
            type="button"
            onClick={applyFilterHint}
            data-testid={`clinical-tile-${t.key}`}
            aria-label={`${t.label}: ${total} total, ${open} open. Go to section and filter.`}
            className="group min-h-[92px] rounded-lg border border-border bg-card p-4 text-left transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background motion-reduce:transition-none motion-reduce:hover:transform-none"
          >
            <div className="text-sm font-semibold text-muted-foreground group-hover:text-foreground">
              {t.label}
            </div>
            <div className="mt-1 font-display text-2xl font-medium tracking-tight text-foreground">
              {total}
            </div>
            <div className="mt-0.5 text-sm text-muted-foreground">
              {open > 0 ? `${open} open` : "None open"}
            </div>
          </button>
        );
      })}
    </div>
  );
}
