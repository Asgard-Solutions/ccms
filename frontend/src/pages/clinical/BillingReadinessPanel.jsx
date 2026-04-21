/**
 * BillingReadinessPanel — Phase 8. Read-only per-encounter readiness
 * evaluator. Collapsible so long encounter lists don't blow up visually;
 * the header chip is always visible so users can see at a glance whether
 * an encounter is `ready`, `warnings`, or `blocked`.
 *
 * This panel never mutates billing data — it's an evaluative view.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleX,
  Info,
  Receipt,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Skeleton } from "../../components/ui/skeleton";

const STATUS_META = {
  ready: {
    label: "Ready",
    tone: "border-success/40 bg-success-soft text-success",
    Icon: CheckCircle2,
  },
  warnings: {
    label: "Warnings",
    tone: "border-warning/40 bg-warning-soft text-warning",
    Icon: CircleAlert,
  },
  blocked: {
    label: "Blocked",
    tone: "border-destructive/30 bg-destructive/10 text-destructive",
    Icon: CircleX,
  },
};

const CHECK_ICON = {
  fail: { Icon: CircleX, tone: "text-destructive" },
  warn: { Icon: CircleAlert, tone: "text-warning" },
  info: { Icon: Info, tone: "text-muted-foreground" },
};

/**
 * @param {object} props
 * @param {string} props.patientId
 * @param {string} props.encounterId
 * @param {boolean} [props.defaultOpen]
 */
export default function BillingReadinessPanel({ patientId, encounterId, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const [report, setReport] = useState(null);
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    if (!encounterId) return;
    try {
      const { data } = await api.get(
        `/patients/${patientId}/clinical/encounters/${encounterId}/billing-readiness`,
      );
      setReport(data);
      setErr(null);
    } catch (e) {
      setErr(formatApiError(e));
      toast.error(formatApiError(e));
    }
  }, [patientId, encounterId]);

  useEffect(() => {
    load();
  }, [load]);

  const overall = report?.overall_status;
  const meta = STATUS_META[overall] || STATUS_META.warnings;
  const HeaderIcon = meta.Icon;

  return (
    <section
      data-testid={`billing-readiness-${encounterId}`}
      className="rounded-lg border border-border bg-card"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid={`billing-readiness-${encounterId}-toggle`}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40"
      >
        <div className="flex items-center gap-2">
          <Receipt className="h-4 w-4 text-muted-foreground" />
          <span className="font-display text-sm font-semibold text-foreground">
            Billing Readiness
          </span>
          {report ? (
            <Badge
              variant="outline"
              data-testid={`billing-readiness-${encounterId}-status`}
              className={`text-[10px] uppercase tracking-wider ${meta.tone}`}
            >
              <HeaderIcon className="mr-1 h-3 w-3" />
              {meta.label}
            </Badge>
          ) : (
            <Badge variant="outline" className="text-[10px] text-muted-foreground">
              —
            </Badge>
          )}
        </div>
        {open ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
      </button>

      {open && (
        <div className="border-t border-border px-4 py-3">
          {err ? (
            <p className="text-xs text-destructive">{err}</p>
          ) : report === null ? (
            <Skeleton className="h-24 rounded-sm" />
          ) : (
            <ReportBody report={report} />
          )}
        </div>
      )}
    </section>
  );
}

function ReportBody({ report }) {
  return (
    <div className="space-y-4">
      <ul data-testid="billing-readiness-checks" className="space-y-1.5">
        {report.checks.map((c) => {
          const ok = c.passed;
          const fallback = CHECK_ICON[c.severity] || CHECK_ICON.info;
          const Icon = ok ? CheckCircle2 : fallback.Icon;
          const tone = ok
            ? "text-success"
            : fallback.tone;
          return (
            <li
              key={c.key}
              data-testid={`billing-readiness-check-${c.key}`}
              className="flex items-start gap-2 rounded-sm border border-border bg-muted/30 px-2 py-1.5 text-xs"
            >
              <Icon className={`mt-0.5 h-3.5 w-3.5 ${tone}`} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-semibold text-foreground">{c.label}</span>
                  <Badge
                    variant="outline"
                    className={`text-[9px] uppercase tracking-wider ${
                      ok
                        ? "border-success/30 bg-success-soft text-success"
                        : c.severity === "fail"
                          ? "border-destructive/30 bg-destructive/10 text-destructive"
                          : c.severity === "warn"
                            ? "border-warning/30 bg-warning-soft text-warning"
                            : "border-border bg-card text-muted-foreground"
                    }`}
                  >
                    {ok ? "pass" : c.severity}
                  </Badge>
                </div>
                {!ok && c.detail && (
                  <p className="mt-0.5 text-[11px] text-muted-foreground">{c.detail}</p>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <div
        data-testid="billing-readiness-future-billing"
        className="rounded-sm border border-dashed border-border bg-background p-3"
      >
        <div className="font-display text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Future-billing summary
        </div>
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
          <Row k="Encounter" v={report.encounter_id?.slice(0, 8) + "…"} />
          <Row k="Appointment" v={report.appointment_id ? report.appointment_id.slice(0, 8) + "…" : "—"} />
          <Row k="Provider" v={report.provider_name || "—"} />
          <Row k="Date of service" v={report.date_of_service || "—"} />
          <Row k="Visit type" v={report.visit_type_label || report.visit_type || "—"} />
          <Row
            k="Note"
            v={
              report.note
                ? `${report.note.kind.replace("_", " ")} · ${report.note.status}${
                    report.note.addendum_count
                      ? ` · +${report.note.addendum_count} addenda`
                      : ""
                  }`
                : "—"
            }
          />
          <Row
            k="Treatment plan"
            v={report.treatment_plan ? `${report.treatment_plan.title} (${report.treatment_plan.plan_status})` : "—"}
          />
          <Row k="Episode" v={report.episode_id ? report.episode_id.slice(0, 8) + "…" : "—"} />
        </dl>
        <div className="mt-3">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Diagnoses
          </div>
          {report.diagnoses.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">None linked.</p>
          ) : (
            <ul data-testid="billing-readiness-dx" className="mt-1 flex flex-wrap gap-1.5">
              {report.diagnoses.map((d) => (
                <li key={d.id}>
                  <Badge variant="outline" className="text-[10px]">
                    {d.icd10_code || "?"} · {d.label || "(no label)"}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="mt-3">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Documented procedures / interventions
          </div>
          {report.procedures.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">None documented.</p>
          ) : (
            <ul data-testid="billing-readiness-procedures" className="mt-1 flex flex-wrap gap-1.5">
              {report.procedures.map((p, i) => (
                <li key={`${p.kind}-${i}`}>
                  <Badge variant="outline" className="text-[10px]">
                    {p.kind}
                    {p.body_region ? ` · ${p.body_region}` : ""}
                    {p.description ? ` — ${p.description}` : ""}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <>
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="truncate font-mono text-foreground">{v}</dd>
    </>
  );
}
