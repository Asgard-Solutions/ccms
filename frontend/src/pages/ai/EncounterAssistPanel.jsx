/**
 * Encounter-editor AI assist panel.
 *
 * Three things in one sidebar-friendly card:
 *   1. "Last time's S / O / A / P" (with `Pull in` buttons)
 *   2. "Draft Subjective + Plan with AI"
 *   3. "Since last visit" callouts (outcome deltas)
 *
 * The host encounter editor passes in `noteId` and an `onPullSection`
 * callback `(section, text) => void` that copies the summary text
 * into the corresponding SOAP field in the editor.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  ArrowDownToLine, ClipboardPaste, Loader2, Sparkles, TrendingUp,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import {
  draftEncounterSections, fetchPriorSections, fetchSinceLastDiff,
} from "../../api/ai";

function SectionCard({ label, value, section, onPull }) {
  if (!value) return null;
  return (
    <div
      data-testid={`assist-prior-${section}`}
      className="rounded-sm border border-border/60 bg-muted/30 p-3"
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        {onPull && (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 rounded-sm text-xs"
            onClick={() => onPull(section, value)}
            data-testid={`assist-pull-${section}-btn`}
          >
            <ArrowDownToLine className="mr-1 h-3 w-3" />
            Pull in
          </Button>
        )}
      </div>
      <p className="text-xs leading-relaxed">{value}</p>
    </div>
  );
}

function DirectionBadge({ direction }) {
  const map = {
    improved: { cls: "bg-green-100 text-green-800", icon: "↓" },
    worsened: { cls: "bg-red-100 text-red-800", icon: "↑" },
    plateau:  { cls: "bg-amber-100 text-amber-800", icon: "→" },
    qualitative: { cls: "bg-slate-100 text-slate-700", icon: "•" },
  };
  const conf = map[direction] || map.qualitative;
  return (
    <span className={`text-[10px] rounded-sm px-1.5 py-0.5 font-semibold ${conf.cls}`}>
      {conf.icon} {direction}
    </span>
  );
}

export default function EncounterAssistPanel({ noteId, onPullSection }) {
  const [prior, setPrior] = useState(null);
  const [diff, setDiff] = useState(null);
  const [loadingPrior, setLoadingPrior] = useState(true);
  const [loadingDiff, setLoadingDiff] = useState(true);
  const [drafting, setDrafting] = useState(false);
  const [drafts, setDrafts] = useState(null);

  const loadPrior = useCallback(async () => {
    if (!noteId) return;
    setLoadingPrior(true);
    try {
      setPrior(await fetchPriorSections(noteId));
    } catch (err) {
      // silent — panel just shows empty state
    } finally {
      setLoadingPrior(false);
    }
  }, [noteId]);

  const loadDiff = useCallback(async () => {
    if (!noteId) return;
    setLoadingDiff(true);
    try {
      setDiff(await fetchSinceLastDiff(noteId));
    } catch (err) {
      // silent
    } finally {
      setLoadingDiff(false);
    }
  }, [noteId]);

  useEffect(() => { loadPrior(); loadDiff(); }, [loadPrior, loadDiff]);

  async function runDraft() {
    setDrafting(true);
    try {
      const res = await draftEncounterSections(noteId);
      setDrafts(res?.drafts || null);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Draft failed");
    } finally {
      setDrafting(false);
    }
  }

  const sections = prior?.prior_sections;
  const callouts = diff?.diff?.callouts || [];

  const carryForward = useMemo(() => {
    const suggested = sections?.suggested_carry_forward || [];
    return new Set(suggested);
  }, [sections]);

  return (
    <aside
      data-testid="encounter-assist-panel"
      className="rounded-md border border-border bg-card p-4 space-y-5"
    >
      <header className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-primary" />
        <h3 className="font-medium text-sm">AI assist</h3>
      </header>

      {/* Since-last-visit callouts */}
      <section data-testid="assist-since-last" className="space-y-2">
        <h4 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          <TrendingUp className="h-3 w-3" />
          Since last visit
        </h4>
        {loadingDiff ? (
          <Skeleton className="h-10" />
        ) : callouts.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No clinically significant changes detected.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {callouts.map((c, i) => (
              <li
                key={i}
                data-testid={`assist-callout-${i}`}
                className="rounded-sm border border-border/60 bg-muted/30 p-2.5"
              >
                <div className="flex items-center justify-between gap-2 mb-0.5">
                  <span className="text-xs font-medium">{c.label}</span>
                  <DirectionBadge direction={c.direction} />
                </div>
                {(c.from != null || c.to != null) && (
                  <p className="text-[11px] font-mono text-muted-foreground">
                    {c.from ?? "—"} → {c.to ?? "—"}
                  </p>
                )}
                {c.note && (
                  <p className="text-xs mt-0.5 text-foreground/80">{c.note}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* AI draft */}
      <section data-testid="assist-draft" className="space-y-2">
        <h4 className="flex items-center justify-between text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <ClipboardPaste className="h-3 w-3" />
            AI draft
          </span>
          <Button
            size="sm"
            variant="outline"
            className="h-6 rounded-sm text-[11px]"
            onClick={runDraft}
            disabled={drafting}
            data-testid="assist-draft-btn"
          >
            {drafting ? <Loader2 className="h-3 w-3 animate-spin" /> : "Draft S + P"}
          </Button>
        </h4>
        {drafts ? (
          <div className="space-y-2">
            {drafts.subjective_draft && (
              <SectionCard
                label="Subjective draft"
                value={drafts.subjective_draft}
                section="subjective"
                onPull={onPullSection}
              />
            )}
            {drafts.plan_draft && (
              <SectionCard
                label="Plan draft"
                value={drafts.plan_draft}
                section="plan"
                onPull={onPullSection}
              />
            )}
            {drafts.rationale && (
              <p className="text-[11px] text-muted-foreground italic">
                {drafts.rationale}
              </p>
            )}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            Click "Draft S + P" to generate Subjective and Plan drafts from the prior
            encounter plus any patient-submitted questionnaires since.
          </p>
        )}
      </section>

      {/* Prior sections */}
      <section data-testid="assist-prior-sections" className="space-y-2">
        <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Last encounter
          {sections?.date_of_service && (
            <span className="ml-1 text-muted-foreground/70 normal-case font-normal">
              · {sections.date_of_service?.slice(0, 10)}
            </span>
          )}
        </h4>
        {loadingPrior ? (
          <div className="space-y-1.5">
            <Skeleton className="h-10" /><Skeleton className="h-10" />
          </div>
        ) : !sections ? (
          <p className="text-xs text-muted-foreground">
            {prior?.reason || "No prior signed encounters."}
          </p>
        ) : (
          <div className="space-y-2">
            <SectionCard
              label={`Subjective${carryForward.has("subjective") ? " · carry forward" : ""}`}
              value={sections.subjective_summary}
              section="subjective"
              onPull={onPullSection}
            />
            <SectionCard
              label="Objective"
              value={sections.objective_summary}
              section="objective"
              onPull={onPullSection}
            />
            <SectionCard
              label={`Assessment${carryForward.has("assessment") ? " · carry forward" : ""}`}
              value={sections.assessment_summary}
              section="assessment"
              onPull={onPullSection}
            />
            <SectionCard
              label="Plan"
              value={sections.plan_summary}
              section="plan"
              onPull={onPullSection}
            />
          </div>
        )}
      </section>
    </aside>
  );
}
