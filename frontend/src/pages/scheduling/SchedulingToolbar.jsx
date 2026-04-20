import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Switch } from "../../components/ui/switch";
import { Label } from "../../components/ui/label";
import { rangeLabel, VIEWS } from "./dateHelpers";
import ProviderFilter from "./ProviderFilter";

const VIEW_LABEL = {
  day: "Day",
  week: "Week",
  month: "Month",
  year: "Year",
};

export default function SchedulingToolbar({
  view,
  date,
  providerId,
  onProviderChange,
  includeCancelled,
  onIncludeCancelledChange,
  onViewChange,
  onPrev,
  onNext,
  onToday,
  onNew,
  canBook,
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Scheduling
        </span>
        <h1
          data-testid="scheduling-range-label"
          className="mt-2 font-display text-4xl font-medium tracking-tight"
        >
          {rangeLabel(view, date)}
        </h1>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {(view === "day" || view === "week") && (
          <div className="flex items-center gap-2 rounded-sm border border-border bg-card px-2.5 py-1.5">
            <Switch
              id="show-cancelled"
              data-testid="scheduling-show-cancelled-toggle"
              checked={!!includeCancelled}
              onCheckedChange={onIncludeCancelledChange}
            />
            <Label
              htmlFor="show-cancelled"
              className="cursor-pointer text-xs font-semibold uppercase tracking-wider text-muted-foreground"
            >
              Show canceled
            </Label>
          </div>
        )}
        <ProviderFilter value={providerId} onChange={onProviderChange} />

        <div
          data-testid="scheduling-view-toggle"
          className="inline-flex rounded-sm border border-border bg-card p-0.5"
          role="tablist"
          aria-label="Calendar view"
        >
          {VIEWS.map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              data-testid={`scheduling-view-${v}`}
              onClick={() => onViewChange(v)}
              className={`rounded-sm px-3 py-1.5 text-sm font-medium transition-colors ${
                view === v
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
            >
              {VIEW_LABEL[v]}
            </button>
          ))}
        </div>

        <div className="inline-flex items-center gap-2">
          <Button
            variant="outline"
            size="icon"
            data-testid="scheduling-prev"
            onClick={onPrev}
            aria-label="Previous"
            className="rounded-sm"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            data-testid="scheduling-today"
            onClick={onToday}
            className="rounded-sm"
          >
            Today
          </Button>
          <Button
            variant="outline"
            size="icon"
            data-testid="scheduling-next"
            onClick={onNext}
            aria-label="Next"
            className="rounded-sm"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>

        {canBook && (
          <Button
            data-testid="scheduling-new-btn"
            onClick={onNew}
            className="h-10 rounded-sm bg-primary px-4 hover:bg-[var(--primary-hover)]"
          >
            <Plus className="mr-2 h-4 w-4" /> New appointment
          </Button>
        )}
      </div>
    </header>
  );
}
