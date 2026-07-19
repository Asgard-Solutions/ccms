/**
 * SafetySummary — Phase 2 Wave B §3
 * Compact safety block near the top of History. Neutral tone for normal
 * findings; warning tone only for actual concerns.
 */
import { AlertTriangle, ShieldCheck } from "lucide-react";

function extractPositiveRedFlags(rf) {
  if (!rf || typeof rf !== "object") return [];
  return Object.entries(rf)
    .filter(([, v]) => v === true)
    .map(([k]) => k.replace(/_/g, " "));
}

function fmtList(items, fallback = "None reported") {
  if (!items) return fallback;
  if (typeof items === "string") return items.trim() || fallback;
  if (Array.isArray(items)) return items.length ? items.join(", ") : fallback;
  return fallback;
}

export default function SafetySummary({ history }) {
  const rf = extractPositiveRedFlags(history?.red_flag_screening);
  const allergies = fmtList(history?.allergies, "NKDA");
  const meds = fmtList(history?.medications, "None reported");
  const pmh = fmtList(history?.past_medical_history, "Not documented");
  const psh = fmtList(history?.past_surgical_history, "Not documented");

  const hasConcern = rf.length > 0
    || (typeof allergies === "string" && !/^nkda$/i.test(allergies) && allergies !== "None reported");

  const rows = [
    { key: "allergies", label: "Allergies", value: allergies, concern: !/^nkda$/i.test(allergies) && allergies !== "None reported" && allergies !== "Not documented" },
    { key: "medications", label: "Medications", value: meds, concern: false },
    { key: "red-flags", label: "Red flags", value: rf.length > 0 ? rf.join(", ") : "None reported", concern: rf.length > 0 },
    { key: "pmh", label: "Relevant medical history", value: pmh, concern: false },
    { key: "psh", label: "Relevant surgical history", value: psh, concern: false },
  ];

  return (
    <section
      data-testid="safety-summary"
      aria-labelledby="safety-summary-title"
      className={`rounded-xl border p-4 ${hasConcern ? "border-warning/40 bg-warning-soft/40" : "border-border bg-card/60"}`}
    >
      <div className="mb-2 flex items-center gap-2">
        {hasConcern ? (
          <AlertTriangle className="h-4 w-4 text-warning" aria-hidden="true" />
        ) : (
          <ShieldCheck className="h-4 w-4 text-success" aria-hidden="true" />
        )}
        <h3 id="safety-summary-title" className="font-display text-sm font-semibold text-foreground">
          Safety summary
        </h3>
      </div>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-base md:grid-cols-2">
        {rows.map((r) => (
          <div key={r.key} data-testid={`safety-summary-${r.key}`} className="flex flex-wrap items-baseline gap-x-2 py-1.5">
            <dt className="min-w-[170px] text-sm font-medium text-muted-foreground">
              {r.label}
            </dt>
            <dd className={r.concern ? "text-warning" : "text-foreground"}>
              {r.value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
