import { useEffect, useState } from "react";
import { ShieldCheck, Activity, AlertTriangle, FileText, Briefcase, Users, Database, BookOpen } from "lucide-react";
import { fetchComplianceDashboard } from "./api";
import { Skeleton } from "../../components/ui/skeleton";

function Tile({ icon: Icon, label, value, sub, tone = "neutral", testid }) {
  const toneClass = {
    neutral: "border-border bg-card",
    warning: "border-warning/40 bg-warning-soft/40",
    danger: "border-destructive/40 bg-destructive-soft/40",
    success: "border-primary/40 bg-primary/5",
  }[tone];

  const valueClass = {
    neutral: "text-foreground",
    warning: "text-warning",
    danger: "text-destructive",
    success: "text-primary",
  }[tone];

  return (
    <div data-testid={testid} className={`rounded-sm border ${toneClass} px-4 py-3`}>
      <div className="flex items-start justify-between">
        <div>
          <div className={`font-display text-2xl font-medium ${valueClass}`}>{value}</div>
          <div className="mt-1 text-[11px] uppercase tracking-[0.15em] text-muted-foreground">{label}</div>
          {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
        </div>
        <Icon className="h-5 w-5 text-muted-foreground" />
      </div>
    </div>
  );
}

export default function DashboardPanel() {
  const [d, setD] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchComplianceDashboard()
      .then(setD)
      .catch((e) => setError(e?.response?.data?.detail || "Failed to load dashboard"));
  }, []);

  if (error) {
    return (
      <div data-testid="dashboard-error" className="rounded-sm border border-destructive-soft bg-destructive-soft p-4 text-sm text-destructive">
        {error}
      </div>
    );
  }
  if (!d) {
    return (
      <div data-testid="dashboard-loading" className="grid gap-3 md:grid-cols-3 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
      </div>
    );
  }

  const tiles = [
    { k: "controls-total", icon: ShieldCheck, label: "Controls", value: d.controls.total,
      sub: `${d.controls.planned} planned · ${d.controls.needs_review} need review`,
      tone: d.controls.needs_review > 0 ? "warning" : "neutral" },
    { k: "risks-open", icon: AlertTriangle, label: "Open risks", value: d.risks.open,
      sub: `${d.risks.high_severity_open} high-severity · ${d.risks.accepted} accepted`,
      tone: d.risks.high_severity_open > 0 ? "danger" : d.risks.open > 0 ? "warning" : "success" },
    { k: "incidents-open", icon: Activity, label: "Open incidents", value: d.incidents.open,
      sub: `${d.incidents.high_severity_open} high-severity`,
      tone: d.incidents.high_severity_open > 0 ? "danger" : d.incidents.open > 0 ? "warning" : "success" },
    { k: "policies-overdue", icon: BookOpen, label: "Overdue policies", value: d.policies.overdue,
      tone: d.policies.overdue > 0 ? "warning" : "success" },
    { k: "vendors-baa", icon: Briefcase, label: "BAA missing", value: d.vendors.baa_missing,
      sub: `${d.vendors.review_due} review due`,
      tone: d.vendors.baa_missing > 0 ? "danger" : "success" },
    { k: "access-reviews", icon: Users, label: "Access reviews", value: d.access_reviews.scheduled,
      sub: `${d.access_reviews.overdue} overdue`,
      tone: d.access_reviews.overdue > 0 ? "danger" : "neutral" },
    { k: "privacy", icon: FileText, label: "Privacy requests", value: d.privacy_requests.pending, sub: "pending" },
    { k: "evidence", icon: Database, label: "Evidence", value: d.evidence.total,
      sub: `${d.evidence.last_90_days} in last 90 days` },
  ];

  return (
    <div data-testid="compliance-dashboard" className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
      {tiles.map((t) => (
        <Tile
          key={t.k}
          testid={`dash-tile-${t.k}`}
          icon={t.icon}
          label={t.label}
          value={t.value}
          sub={t.sub}
          tone={t.tone}
        />
      ))}
    </div>
  );
}
