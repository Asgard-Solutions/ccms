/**
 * SectionNav — sticky in-page navigation for the Clinical redesign.
 *
 * Pure UI: takes the current active section id, a jump handler, and an
 * optional counts map. Scrollspy and history integration live in the
 * parent (ClinicalTabV2) so this component can be unit tested in
 * isolation.
 */
import { NAV_ITEMS } from "./clinicalHelpers";

export default function SectionNav({ activeId, onJump, counts }) {
  return (
    <nav
      aria-label="Clinical sections"
      data-testid="clinical-section-nav"
      className="border-b border-border bg-background/90 px-2 backdrop-blur supports-[backdrop-filter]:bg-background/70"
    >
      <ul className="flex flex-wrap items-center gap-1 overflow-x-auto py-1.5">
        {NAV_ITEMS.map((item) => {
          const isActive = activeId === item.id;
          const count = counts?.[item.id];
          return (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => onJump(item.id, { userInitiated: true })}
                data-testid={`clinical-nav-${item.id}`}
                aria-current={isActive ? "location" : undefined}
                className={[
                  "inline-flex min-h-11 items-center gap-1.5 rounded-full px-4 py-2 text-sm transition-colors",
                  "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                  isActive
                    ? "bg-primary text-primary-foreground font-semibold shadow-sm"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                ].join(" ")}
              >
                {item.label}
                {count != null && count > 0 && (
                  <span
                    className={[
                      "rounded-full px-1.5 text-xs font-medium",
                      isActive ? "bg-primary-foreground/25" : "bg-muted-foreground/15",
                    ].join(" ")}
                    aria-label={`${count} items`}
                  >
                    {count}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
