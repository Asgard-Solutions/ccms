/**
 * Patient-facing visit-brief card.
 *
 * Shown above the Upcoming Appointments card in the portal overview
 * whenever the patient has at least one upcoming visit. Calls
 * `/api/portal/visit-brief` (cached server-side via the smart cache).
 *
 * The brief is intentionally NOT a clinical document — it's a friendly
 * second-person preview that translates outcome jargon (NPRS / ODI)
 * into plain English. It never contains medication names, ICD codes,
 * or imaging — those are stripped at the prompt level in
 * `services/ai/prompts.py::PATIENT_VISIT_BRIEF_SYSTEM`.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2, RefreshCcw, Sparkles, MessageCircle, Bell } from "lucide-react";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import {
  fetchPortalVisitBrief, regeneratePortalVisitBrief,
} from "../api/portal";

function isNonEmpty(v) {
  return typeof v === "string" && v.trim().length > 0;
}

export default function PortalVisitBriefCard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fetchPortalVisitBrief());
    } catch (err) {
      // Soft-fail: the portal overview should still render even if the
      // brief is down. We just hide the card.
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function regenerate() {
    setRegenerating(true);
    try {
      setData(await regeneratePortalVisitBrief());
      toast.success("Refreshed.");
    } catch {
      toast.error("Couldn't refresh — please try again later.");
    } finally {
      setRegenerating(false);
    }
  }

  if (loading) {
    return (
      <section
        data-testid="portal-visit-brief-loading"
        className="rounded-md border border-border bg-card p-5"
      >
        <Skeleton className="h-4 w-1/2 mb-3" />
        <Skeleton className="h-3 w-full mb-2" />
        <Skeleton className="h-3 w-[88%]" />
      </section>
    );
  }

  const brief = data?.brief;
  if (!brief || (!isNonEmpty(brief.headline) && !isNonEmpty(brief.this_visit))) {
    return null;
  }

  return (
    <section
      data-testid="portal-visit-brief-card"
      className="relative overflow-hidden rounded-md border border-border bg-gradient-to-br from-primary/5 via-card to-card p-5"
    >
      <header className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-start gap-2">
          <Sparkles className="mt-0.5 h-4 w-4 text-primary" />
          <h2
            data-testid="portal-visit-brief-headline"
            className="text-base font-medium leading-snug"
          >
            {brief.headline}
          </h2>
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={regenerate}
          disabled={regenerating}
          data-testid="portal-visit-brief-refresh-btn"
          className="h-7 shrink-0 rounded-sm"
          aria-label="Refresh visit brief"
        >
          {regenerating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCcw className="h-3.5 w-3.5" />
          )}
        </Button>
      </header>

      <div className="space-y-3 text-sm leading-relaxed text-foreground/90">
        {isNonEmpty(brief.last_visit) && (
          <p data-testid="portal-visit-brief-last-visit">
            {brief.last_visit}
          </p>
        )}
        {isNonEmpty(brief.your_progress) && (
          <p
            data-testid="portal-visit-brief-progress"
            className="rounded-sm border border-border/50 bg-background/60 px-3 py-2"
          >
            {brief.your_progress}
          </p>
        )}
        {isNonEmpty(brief.this_visit) && (
          <p data-testid="portal-visit-brief-this-visit">
            {brief.this_visit}
          </p>
        )}
      </div>

      {Array.isArray(brief.ask_about) && brief.ask_about.length > 0 && (
        <div className="mt-4" data-testid="portal-visit-brief-ask-about">
          <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            <MessageCircle className="h-3 w-3" />
            You might want to ask
          </h3>
          <ul className="space-y-1">
            {brief.ask_about.slice(0, 3).map((q, i) => (
              <li
                key={i}
                data-testid={`portal-visit-brief-ask-${i}`}
                className="text-sm before:mr-2 before:text-primary before:content-['•']"
              >
                {q}
              </li>
            ))}
          </ul>
        </div>
      )}

      {Array.isArray(brief.reminders) && brief.reminders.length > 0 && (
        <div className="mt-4" data-testid="portal-visit-brief-reminders">
          <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            <Bell className="h-3 w-3" />
            Before you arrive
          </h3>
          <ul className="space-y-1">
            {brief.reminders.slice(0, 3).map((r, i) => (
              <li
                key={i}
                data-testid={`portal-visit-brief-reminder-${i}`}
                className="text-sm before:mr-2 before:text-primary before:content-['→']"
              >
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data?.cached && (
        <span
          data-testid="portal-visit-brief-cached-badge"
          className="absolute right-3 top-3 text-[10px] uppercase tracking-wider text-muted-foreground/70"
        >
          cached
        </span>
      )}
    </section>
  );
}
