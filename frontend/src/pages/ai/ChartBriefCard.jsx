/**
 * Chart-prep brief card — shown on the patient chart.
 * Calls /api/ai/chart-brief/{patient_id} which serves the cached
 * version when context_hash matches, regenerates otherwise.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2, RefreshCcw, Sparkles } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { fetchChartBrief, regenerateChartBrief } from "../../api/ai";
import { formatDateTime } from "../../utils/time";

export default function ChartBriefCard({ patientId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);

  const load = useCallback(async () => {
    if (!patientId) return;
    setLoading(true);
    try {
      setData(await fetchChartBrief(patientId));
    } catch (err) {
      const msg = err?.response?.data?.detail || "Chart brief unavailable";
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [patientId]);

  useEffect(() => { load(); }, [load]);

  async function regenerate() {
    setRegenerating(true);
    try {
      setData(await regenerateChartBrief(patientId));
      toast.success("Brief regenerated.");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to regenerate");
    } finally {
      setRegenerating(false);
    }
  }

  return (
    <section
      data-testid="chart-brief-card"
      className="rounded-md border border-border bg-card p-5"
    >
      <header className="flex items-center justify-between gap-4 mb-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" />
          <h3 className="font-medium">AI chart prep</h3>
          {data?.cached && (
            <span
              data-testid="chart-brief-cached-badge"
              className="text-[10px] rounded-sm border border-border/60 px-1.5 py-0.5 text-muted-foreground uppercase tracking-wider"
            >
              cached
            </span>
          )}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={regenerate}
          disabled={regenerating}
          data-testid="chart-brief-regenerate-btn"
          className="h-7 rounded-sm"
        >
          {regenerating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCcw className="h-3.5 w-3.5" />
          )}
          <span className="ml-1 text-xs">Regenerate</span>
        </Button>
      </header>

      {loading ? (
        <div className="space-y-2" data-testid="chart-brief-loading">
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-[92%]" />
          <Skeleton className="h-3 w-[88%]" />
          <Skeleton className="h-3 w-[85%]" />
        </div>
      ) : !data?.brief ? (
        <p className="text-sm text-muted-foreground">
          No brief available — try regenerating.
        </p>
      ) : (
        <>
          <div
            data-testid="chart-brief-body"
            className="text-sm whitespace-pre-wrap leading-relaxed text-foreground/90"
          >
            {data.brief}
          </div>
          <p className="mt-3 text-[11px] text-muted-foreground">
            {data.model}
            {data.generated_at ? ` · ${formatDateTime(data.generated_at)}` : ""}
          </p>
        </>
      )}
    </section>
  );
}
