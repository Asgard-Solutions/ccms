/**
 * SummaryTiles — six interactive count tiles at the top of the
 * Clinical redesign. Each tile is a real <button> that jumps to a
 * mapped section.
 */
import { Skeleton } from "../../components/ui/skeleton";

const TILES = [
  { key: "encounters",      label: "Visits",     jump: "encounters", src: "encounters" },
  { key: "initial_exams",   label: "Exams",      jump: "encounters", src: "initial_exams" },
  { key: "treatment_plans", label: "Plans",      jump: "care-plan",  src: "treatment_plans" },
  { key: "re_exams",        label: "Re-exams",   jump: "care-plan",  src: "re_exams" },
  { key: "notes",           label: "Notes",      jump: "encounters", src: "notes" },
  { key: "diagnoses",       label: "Diagnoses",  jump: "diagnoses",  src: "diagnoses" },
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
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onJumpTo(t.jump, { userInitiated: true })}
            data-testid={`clinical-tile-${t.key}`}
            aria-label={`${t.label}: ${total} total, ${open} open. Go to section.`}
            className="group rounded-lg border border-border bg-card p-4 text-left transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background motion-reduce:transition-none motion-reduce:hover:transform-none"
          >
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground group-hover:text-foreground">
              {t.label}
            </div>
            <div className="mt-1 font-display text-2xl font-medium tracking-tight text-foreground">
              {total}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {open > 0 ? `${open} open` : "None open"}
            </div>
          </button>
        );
      })}
    </div>
  );
}
