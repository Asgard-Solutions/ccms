/**
 * StatusBadge — Phase 2 Wave A shared status vocabulary.
 *
 * Five independent dimensions, each with its own colour + label +
 * icon-agnostic mapping so status is never communicated by colour alone
 * (WCAG 2.2 §1.4.1). Sentence-case labels throughout.
 *
 * Consumers pass `dim` + `value`; unknown values render as neutral.
 */

const WORKFLOW = {
  scheduled:   { label: "Scheduled",   tone: "border-border bg-muted text-muted-foreground" },
  checked_in:  { label: "Checked in",  tone: "border-primary/30 bg-primary/10 text-primary" },
  in_progress: { label: "In progress", tone: "border-primary/40 bg-primary/15 text-primary" },
  completed:   { label: "Completed",   tone: "border-success/40 bg-success-soft text-success" },
  cancelled:   { label: "Cancelled",   tone: "border-border bg-muted text-muted-foreground" },
};
const DOCUMENTATION = {
  missing:  { label: "Note missing", tone: "border-warning/40 bg-warning-soft text-warning" },
  draft:    { label: "Note draft",   tone: "border-border bg-muted text-muted-foreground" },
  signed:   { label: "Note signed",  tone: "border-success/40 bg-success-soft text-success" },
  amended:  { label: "Amended",      tone: "border-primary/30 bg-primary/10 text-primary" },
};
const CLINICAL_RESPONSE = {
  improving:    { label: "Improving",    tone: "border-success/40 bg-success-soft text-success" },
  stable:       { label: "Stable",       tone: "border-border bg-muted text-muted-foreground" },
  worsening:    { label: "Worsening",    tone: "border-destructive/30 bg-destructive-soft text-destructive" },
  not_recorded: { label: "Response not recorded", tone: "border-border bg-card text-muted-foreground" },
};
const BILLING = {
  ready:         { label: "Billing ready",   tone: "border-success/40 bg-success-soft text-success" },
  warning:       { label: "Billing warning", tone: "border-warning/40 bg-warning-soft text-warning" },
  blocked:       { label: "Billing blocked", tone: "border-destructive/30 bg-destructive-soft text-destructive" },
  not_evaluated: { label: "Billing not evaluated", tone: "border-border bg-card text-muted-foreground" },
};
const RECORD_STATE = {
  active:   { label: "Active",   tone: "border-primary/30 bg-primary/10 text-primary" },
  inactive: { label: "Inactive", tone: "border-border bg-muted text-muted-foreground" },
  resolved: { label: "Resolved", tone: "border-success/40 bg-success-soft text-success" },
  archived: { label: "Archived", tone: "border-border bg-muted text-muted-foreground" },
};

const DIMENSIONS = {
  workflow: WORKFLOW,
  documentation: DOCUMENTATION,
  clinical_response: CLINICAL_RESPONSE,
  billing: BILLING,
  record_state: RECORD_STATE,
};

export default function StatusBadge({ dim, value, label, testId }) {
  const spec = DIMENSIONS[dim]?.[value];
  if (!spec) {
    return (
      <span
        data-testid={testId}
        className="inline-flex items-center rounded-full border border-border bg-card px-2 py-0.5 text-[11px] text-muted-foreground"
      >
        {label || value || "—"}
      </span>
    );
  }
  return (
    <span
      data-testid={testId}
      role="status"
      aria-label={`${dim.replace(/_/g, " ")}: ${spec.label}`}
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${spec.tone}`}
    >
      {/* Small textual prefix (dot) so the badge is not colour-only. */}
      <span aria-hidden="true" className="mr-1 leading-none">●</span>
      {spec.label}
    </span>
  );
}

export const STATUS_LABELS = DIMENSIONS;
