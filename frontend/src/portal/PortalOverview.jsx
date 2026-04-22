/**
 * /portal — patient landing page.
 *
 * Tiny welcome card + pointers to key self-service surfaces. Kept
 * deliberately sparse since the heavy-lift portal features will grow
 * here over time (appointments, intake forms, messages, etc.).
 */
import { Link } from "react-router-dom";
import { FileText } from "lucide-react";
import { Button } from "../components/ui/button";
import { useAuth } from "../contexts/AuthContext";

export default function PortalOverview() {
  const { user } = useAuth();
  return (
    <div data-testid="portal-overview" className="space-y-6">
      <div>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Welcome back
        </span>
        <h1 className="mt-1 font-display text-3xl font-medium tracking-tight">
          Hi, {user?.name?.split(" ")[0] || "there"}.
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your account and clinic history at a glance.
        </p>
      </div>

      <section
        data-testid="portal-overview-statements-card"
        className="rounded-sm border border-border bg-card p-6"
      >
        <div className="flex items-start gap-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-sm bg-primary/10 text-primary">
            <FileText className="h-5 w-5" />
          </div>
          <div className="flex-1">
            <h2 className="font-display text-lg font-medium tracking-tight">
              Statements
            </h2>
            <p className="mt-0.5 text-sm text-muted-foreground">
              View or download any statement we've generated for you,
              including what insurance paid and what you still owe.
            </p>
          </div>
          <Link to="/portal/statements" data-testid="portal-overview-go-statements">
            <Button size="sm" className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]">
              View statements
            </Button>
          </Link>
        </div>
      </section>
    </div>
  );
}
