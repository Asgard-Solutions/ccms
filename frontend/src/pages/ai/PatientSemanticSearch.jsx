/**
 * Patient-chart natural-language search.
 *
 * Doctor / admin / staff can ask "How is this patient's lower back
 * pain trending?" or "Has the patient ever reported numbness?" and get
 * back a 2-3 sentence answer with cited snippet IDs plus the ranked
 * source snippets. Powered by Claude Sonnet 4.5 over a deterministic
 * candidate set (signed follow-ups, exams, diagnoses, treatment plans,
 * outcome entries) — see `services/ai/search_router.py`.
 *
 * Cached server-side per (patient_id, query_hash) so identical
 * questions are essentially free.
 */
import { useState } from "react";
import { Loader2, Search, Sparkles } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { aiSemanticSearch } from "../../api/ai";

const KIND_LABEL = {
  follow_up_note: "Follow-up note",
  initial_exam: "Initial exam",
  diagnosis: "Diagnosis",
  treatment_plan: "Treatment plan",
  outcome_entry: "Outcome entry",
};

function formatDate(d) {
  if (!d) return "—";
  const dt = new Date(d);
  if (Number.isNaN(dt.getTime())) return d;
  return dt.toLocaleDateString();
}

export default function PatientSemanticSearch({ patientId }) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);

  async function run(e) {
    e?.preventDefault?.();
    const q = query.trim();
    if (q.length < 2 || !patientId) return;
    setLoading(true);
    try {
      const res = await aiSemanticSearch({ patient_id: patientId, query: q });
      setData(res || null);
    } catch (err) {
      setData({
        answer: err?.response?.data?.detail || "Search failed.",
        results: [], _error: true,
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <section
      data-testid="patient-semantic-search"
      className="rounded-md border border-border bg-card p-4 space-y-3"
    >
      <header className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-primary" />
        <h3 className="font-medium text-sm">Ask the chart</h3>
        <span className="ml-auto text-[10px] uppercase tracking-wider text-muted-foreground">
          AI-powered search
        </span>
      </header>
      <form onSubmit={run} className="flex items-center gap-2">
        <Input
          data-testid="patient-semantic-search-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder='e.g. "How is the patient&apos;s low back pain trending?"'
          className="text-sm"
        />
        <Button
          type="submit"
          size="sm"
          disabled={loading || query.trim().length < 2}
          data-testid="patient-semantic-search-btn"
          className="rounded-sm"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Search className="h-4 w-4" />
          )}
        </Button>
      </form>

      {data && (
        <div className="space-y-2.5" data-testid="patient-semantic-search-result">
          {data.answer && (
            <p
              data-testid="patient-semantic-search-answer"
              className={`rounded-sm border px-3 py-2 text-sm leading-relaxed ${
                data._error
                  ? "border-destructive/30 bg-destructive/5 text-destructive"
                  : "border-border/50 bg-background/60"
              }`}
            >
              {data.answer}
              {data.cached && (
                <span
                  data-testid="patient-semantic-search-cached"
                  className="ml-2 text-[10px] uppercase tracking-wider text-muted-foreground"
                >
                  cached
                </span>
              )}
            </p>
          )}
          {Array.isArray(data.results) && data.results.length > 0 && (
            <ol className="space-y-1.5" data-testid="patient-semantic-search-snippets">
              {data.results.map((r) => (
                <li
                  key={r.snippet_id}
                  data-testid={`patient-semantic-search-snippet-${r.snippet_id}`}
                  className="rounded-sm border border-border/50 bg-muted/20 p-2 text-xs"
                >
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                      {r.snippet_id} · {KIND_LABEL[r.kind] || r.kind} · {formatDate(r.date)}
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      score {r.score}
                    </span>
                  </div>
                  <p className="leading-relaxed text-foreground/85 line-clamp-3">
                    {r.text}
                  </p>
                  {r.reason && (
                    <p className="mt-1 text-[11px] italic text-muted-foreground">
                      {r.reason}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}
