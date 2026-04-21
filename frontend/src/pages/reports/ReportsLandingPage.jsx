import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import {
  BarChart3,
  Briefcase,
  ClipboardList,
  FileBarChart,
  Lock,
  ShieldCheck,
  Stethoscope,
} from "lucide-react";
import { Skeleton } from "../../components/ui/skeleton";
import { fetchCatalog, formatApiError } from "./reportsApi";
import { toast } from "sonner";

const CATEGORY_ICON = {
  Operational: ClipboardList,
  Financial: Briefcase,
  Clinical: Stethoscope,
  Compliance: ShieldCheck,
};

const CATEGORY_HINT = {
  Operational: "Scheduling, attendance, throughput.",
  Financial: "Billing, claims, payments, denials.",
  Clinical: "Chart completeness and clinical activity.",
  Compliance: "Audit trails, credentialing, privileged access.",
};

export default function ReportsLandingPage() {
  const [catalog, setCatalog] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchCatalog();
        if (!cancelled) setCatalog(data);
      } catch (e) {
        toast.error(formatApiError(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const total = catalog?.total || 0;

  return (
    <div data-testid="reports-landing" className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">Insights</div>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
            Reports
          </h1>
          <p className="mt-1 max-w-xl text-sm text-muted-foreground">
            Operational, clinical, financial, and compliance reports across your
            practice. Filter, save views, and export securely.
          </p>
        </div>
        <div className="rounded-sm border border-border bg-card px-4 py-3 text-right">
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Available
          </div>
          <div data-testid="reports-total-count" className="font-display text-2xl font-medium tabular-nums">
            {loading ? "—" : total}
          </div>
        </div>
      </header>

      {loading && (
        <div className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      )}

      {!loading && total === 0 && (
        <div
          data-testid="reports-empty"
          className="rounded-sm border border-dashed border-border bg-card p-10 text-center text-muted-foreground"
        >
          <FileBarChart className="mx-auto mb-3 h-8 w-8" />
          No reports available. Ask an administrator to grant you reporting permissions.
        </div>
      )}

      {!loading && (catalog?.categories || []).map((cat) => {
        const Icon = CATEGORY_ICON[cat.category] || BarChart3;
        return (
          <section
            key={cat.category}
            data-testid={`reports-category-${cat.category.toLowerCase()}`}
            className="space-y-3"
          >
            <div className="flex items-baseline gap-3">
              <Icon className="h-5 w-5 text-primary" />
              <h2 className="font-display text-xl font-medium tracking-tight">
                {cat.category}
              </h2>
              <span className="text-xs text-muted-foreground">
                {CATEGORY_HINT[cat.category]}
              </span>
            </div>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {cat.reports.map((r) => (
                <Link
                  key={r.name}
                  to={`/reports/${r.name}`}
                  data-testid={`report-card-${r.name}`}
                  className="group relative flex flex-col gap-2 rounded-sm border border-border bg-card p-5 transition-all hover:border-primary/60 hover:shadow-md"
                >
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="font-display text-base font-semibold leading-tight">
                      {r.title}
                    </h3>
                    {r.contains_phi && (
                      <span
                        data-testid={`report-phi-badge-${r.name}`}
                        className="inline-flex items-center gap-1 rounded-sm bg-warning-soft px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-warning"
                      >
                        <Lock className="h-3 w-3" /> PHI
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">{r.description}</p>
                  <div className="mt-auto pt-2 text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
                    {r.default_columns?.length || 0} default columns ·{" "}
                    {r.export_formats?.join(" / ").toUpperCase()}
                  </div>
                </Link>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
