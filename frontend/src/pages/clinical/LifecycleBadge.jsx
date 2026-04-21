/**
 * LifecycleBadge — Phase 8. Single source of truth for the
 * `draft / sign_ready / signed / signed + addenda` pill rendered across
 * editor headers, cards, and timeline rows. Keeps the visual language
 * consistent everywhere the note lifecycle surfaces.
 */
import { Badge } from "../../components/ui/badge";

const BASE_LABEL = {
  draft: "Draft",
  sign_ready: "Sign ready",
  signed: "Signed",
};

const BASE_TONE = {
  draft: "border-border bg-card text-muted-foreground",
  sign_ready: "border-warning/40 bg-warning-soft text-warning",
  signed: "border-success/40 bg-success-soft text-success",
};

/**
 * @param {object} props
 * @param {"draft"|"sign_ready"|"signed"|string|null|undefined} props.status
 * @param {number} [props.addendumCount]
 * @param {string} [props.testId]
 * @param {string} [props.className]
 */
export default function LifecycleBadge({
  status,
  addendumCount = 0,
  testId,
  className,
}) {
  const baseKey = BASE_LABEL[status] ? status : "draft";
  const label = BASE_LABEL[baseKey] || (status || "unknown");
  const tone = BASE_TONE[baseKey];
  const suffix =
    baseKey === "signed" && addendumCount > 0
      ? ` · +${addendumCount} addendum${addendumCount === 1 ? "" : "s"}`
      : "";
  return (
    <Badge
      variant="outline"
      data-testid={testId || "lifecycle-badge"}
      className={`text-[10px] uppercase tracking-wider ${tone} ${className || ""}`}
    >
      {label}
      {suffix}
    </Badge>
  );
}
