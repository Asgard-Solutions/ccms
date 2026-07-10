/**
 * ClinicalTabV2 — Phase 1 redesign of Patient Profile > Clinical.
 *
 * Wraps every existing clinical sub-card (business logic unchanged)
 * with a new orientation shell: sticky patient-context header, sticky
 * in-page section nav, and a "Current Care Status" panel that surfaces
 * the actionable state of the chart before the user scrolls.
 *
 * All permissions, masking, audit, signed-record rules, and API
 * contracts flow through the wrapped sub-cards untouched.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  AlertTriangle,
  ArrowUp,
  CalendarPlus,
  ClipboardList,
  FileWarning,
  PlayCircle,
  PlusCircle,
  Stethoscope,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { formatDate, formatDateTime } from "../../utils/time";

import IntakeHistoryCard from "./IntakeHistoryCard";
import DiagnosesCard from "./DiagnosesCard";
import EncountersCard from "./EncountersCard";
import InitialExamsCard from "./InitialExamsCard";
import FollowUpNotesCard from "./FollowUpNotesCard";
import CareTimelineCard from "./CareTimelineCard";
import TreatmentPlansCard from "./TreatmentPlansCard";
import ReExamsCard from "./ReExamsCard";
import MediaCard from "./MediaCard";
import OutcomesCard from "./OutcomesCard";
import EpisodesSection from "./EpisodesSection";

const NAV_ITEMS = [
  { id: "summary", label: "Summary" },
  { id: "history", label: "History" },
  { id: "diagnoses", label: "Diagnoses" },
  { id: "encounters", label: "Encounters" },
  { id: "care-plan", label: "Care plan" },
  { id: "timeline", label: "Timeline" },
  { id: "imaging", label: "Imaging" },
  { id: "outcomes", label: "Outcomes" },
];

// -------- helpers --------------------------------------------------

function getInitials(patient) {
  if (!patient) return "??";
  if (patient.unmasked) {
    const f = (patient.first_name || "").trim();
    const l = (patient.last_name || "").trim();
    const i = `${f.charAt(0)}${l.charAt(0)}`.toUpperCase();
    return i || "??";
  }
  // display_name_masked usually looks like "A. B." or "John D." — take the
  // first character of each token.
  const src = patient.display_name_masked || "";
  const parts = src.replace(/\./g, "").split(/\s+/).filter(Boolean);
  return (parts.map((p) => p.charAt(0)).join("").slice(0, 2) || "??").toUpperCase();
}

function computeAge(dobIso) {
  if (!dobIso) return null;
  const dob = new Date(dobIso);
  if (Number.isNaN(dob.getTime())) return null;
  const now = new Date();
  let age = now.getFullYear() - dob.getFullYear();
  const m = now.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && now.getDate() < dob.getDate())) age -= 1;
  return age >= 0 && age < 130 ? age : null;
}

function pickNextAppointment(appointments) {
  if (!Array.isArray(appointments)) return null;
  const now = Date.now();
  return appointments
    .filter(
      (a) =>
        a?.start_time &&
        new Date(a.start_time).getTime() > now &&
        !["cancelled", "canceled", "no_show"].includes(a.status),
    )
    .sort((a, b) => new Date(a.start_time) - new Date(b.start_time))[0];
}

function pickActiveEpisode(episodes) {
  if (!Array.isArray(episodes)) return null;
  return (
    episodes.find((e) => e.status === "active") ||
    episodes.find((e) => e.status === "on_hold") ||
    null
  );
}

function pickPrimaryDiagnosis(diagnoses) {
  if (!Array.isArray(diagnoses)) return null;
  return (
    diagnoses.find((d) => d.is_primary && d.status === "active") ||
    diagnoses.find((d) => d.status === "active") ||
    null
  );
}

function extractRedFlagFindings(history) {
  const rf = history?.red_flag_screening;
  if (!rf || typeof rf !== "object") return { positives: [], hasScreening: false };
  const positives = [];
  for (const [k, v] of Object.entries(rf)) {
    if (v === true) positives.push(k.replace(/_/g, " "));
  }
  return { positives, hasScreening: Object.keys(rf).length > 0 };
}

// -------- header / nav sub-components ------------------------------

function PatientContextHeader({
  patient,
  age,
  initials,
  activeEpisode,
  primaryDx,
  currentProviderName,
  nextAppt,
  reExamDue,
  alerts,
}) {
  const nameOrMask = patient.unmasked
    ? `${patient.first_name || ""} ${patient.last_name || ""}`.trim() || "—"
    : patient.display_name_masked || "Masked patient";

  return (
    <div
      data-testid="clinical-patient-context-header"
      className="border-b border-border bg-background/95 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/80"
    >
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/15 text-sm font-semibold text-primary"
          >
            {initials}
          </span>
          <div className="min-w-0">
            <div
              className="truncate font-display text-base font-semibold text-foreground"
              data-testid="clinical-context-patient-name"
            >
              {nameOrMask}
            </div>
            <div className="text-xs text-muted-foreground">
              {[
                age != null ? `Age ${age}` : null,
                patient.gender || null,
                patient.status === "deleted" ? "Archived" : null,
              ]
                .filter(Boolean)
                .join(" · ") || "Patient profile"}
            </div>
          </div>
        </div>

        <ContextChip label="Episode" testId="clinical-context-episode">
          {activeEpisode ? activeEpisode.title : "No active episode"}
        </ContextChip>

        <ContextChip label="Primary diagnosis" testId="clinical-context-primary-dx">
          {primaryDx ? primaryDx.label || primaryDx.icd10_code : "Not documented"}
        </ContextChip>

        <ContextChip label="Provider" testId="clinical-context-provider">
          {currentProviderName || "Unassigned"}
        </ContextChip>

        <ContextChip label="Next appointment" testId="clinical-context-next-appt">
          {nextAppt ? formatDateTime(nextAppt.start_time) : "Not scheduled"}
        </ContextChip>

        <ContextChip label="Re-exam due" testId="clinical-context-reexam-due">
          {reExamDue ? formatDate(reExamDue) : "Not scheduled"}
        </ContextChip>

        {alerts && alerts.length > 0 && (
          <span
            data-testid="clinical-context-alerts"
            className="inline-flex items-center gap-1.5 rounded-full border border-warning/40 bg-warning-soft px-2.5 py-1 text-xs font-medium text-warning"
            role="status"
          >
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            {alerts.length === 1
              ? alerts[0]
              : `${alerts.length} clinical alerts`}
          </span>
        )}
      </div>
    </div>
  );
}

function ContextChip({ label, children, testId }) {
  return (
    <div className="min-w-0" data-testid={testId}>
      <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="truncate text-sm text-foreground">{children}</div>
    </div>
  );
}

function SectionNav({ activeId, onJump, counts }) {
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
                onClick={() => onJump(item.id)}
                data-testid={`clinical-nav-${item.id}`}
                aria-current={isActive ? "location" : undefined}
                className={[
                  "inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm transition-colors",
                  "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                  isActive
                    ? "bg-primary text-primary-foreground font-medium"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                ].join(" ")}
              >
                {item.label}
                {count != null && count > 0 && (
                  <span
                    className={[
                      "rounded-full px-1.5 text-[10px]",
                      isActive ? "bg-primary-foreground/20" : "bg-muted-foreground/15",
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

function BackToTopButton({ visible, onClick }) {
  if (!visible) return null;
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid="clinical-back-to-top"
      aria-label="Back to top"
      className="fixed bottom-6 right-6 z-40 inline-flex items-center gap-1.5 rounded-full border border-border bg-card/95 px-3 py-2 text-xs font-medium text-foreground shadow-lg backdrop-blur transition-transform hover:-translate-y-0.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 motion-reduce:transition-none motion-reduce:hover:transform-none"
    >
      <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
      Back to top
    </button>
  );
}

// -------- Current Care Status panel --------------------------------

function CurrentCareStatusPanel({
  activeEpisode,
  primaryDx,
  activePlan,
  nextAppt,
  reExamDue,
  unsignedCount,
  billingWarnings,
  redFlag,
  missingIntakeCount,
  onJumpTo,
  onOpenEncounter,
  canWrite,
  navigate,
  patientId,
}) {
  const rows = [];

  // Active episode
  rows.push({
    key: "episode",
    label: "Active episode",
    value: activeEpisode ? activeEpisode.title : "No current episode",
    tone: activeEpisode ? "default" : "muted",
  });

  // Primary diagnosis
  rows.push({
    key: "primary-dx",
    label: "Primary diagnosis",
    value: primaryDx
      ? [primaryDx.icd10_code, primaryDx.label].filter(Boolean).join(" · ")
      : "Not documented",
    tone: primaryDx ? "default" : "muted",
    cta: primaryDx
      ? null
      : canWrite
        ? { label: "Add diagnosis", onClick: () => onJumpTo("diagnoses") }
        : null,
  });

  // Plan progress
  if (activePlan) {
    const completed = activePlan.visits_completed ?? 0;
    const planned = activePlan.total_visits_planned ?? activePlan.visits_planned ?? null;
    const scheduled = activePlan.visits_scheduled ?? null;
    if (planned != null) {
      rows.push({
        key: "plan-progress",
        label: "Visits progress",
        value: `${completed} of ${planned} planned visits completed`,
        tone: "default",
      });
    }
    if (scheduled != null && scheduled > 0) {
      rows.push({
        key: "plan-scheduled",
        label: "Scheduled",
        value: `${scheduled} visit${scheduled === 1 ? "" : "s"} scheduled`,
        tone: "default",
      });
    }
    if (planned != null && scheduled != null) {
      const remaining = Math.max(planned - completed - scheduled, 0);
      if (remaining > 0) {
        rows.push({
          key: "plan-remaining",
          label: "Unscheduled",
          value: `${remaining} visit${remaining === 1 ? "" : "s"} remain unscheduled`,
          tone: "warning",
          cta: canWrite
            ? {
                label: "Schedule visit",
                onClick: () => navigate(`/scheduling?patient=${patientId}`),
              }
            : null,
        });
      }
    }
  } else {
    rows.push({
      key: "plan-progress",
      label: "Treatment plan",
      value: "No active plan",
      tone: "muted",
      cta: canWrite
        ? { label: "Open care plan", onClick: () => onJumpTo("care-plan") }
        : null,
    });
  }

  // Next appointment
  rows.push({
    key: "next-appt",
    label: "Next appointment",
    value: nextAppt
      ? `${formatDateTime(nextAppt.start_time)}${
          nextAppt.provider_name ? ` with ${nextAppt.provider_name}` : ""
        }`
      : "Not scheduled",
    tone: nextAppt ? "default" : "warning",
    cta:
      !nextAppt && canWrite
        ? {
            label: "Schedule visit",
            onClick: () => navigate(`/scheduling?patient=${patientId}`),
          }
        : null,
  });

  // Re-exam due
  if (reExamDue) {
    rows.push({
      key: "reexam-due",
      label: "Re-exam due",
      value: formatDate(reExamDue),
      tone: "warning",
      cta: canWrite
        ? { label: "Schedule re-exam", onClick: () => onJumpTo("care-plan") }
        : null,
    });
  }

  // Unsigned documentation
  if (unsignedCount > 0) {
    rows.push({
      key: "unsigned",
      label: "Documentation",
      value: `${unsignedCount} unsigned or incomplete document${
        unsignedCount === 1 ? "" : "s"
      }`,
      tone: "warning",
      cta: canWrite
        ? { label: "Open encounters", onClick: () => onJumpTo("encounters") }
        : null,
    });
  }

  // Billing warnings
  if (billingWarnings && billingWarnings.count > 0) {
    rows.push({
      key: "billing",
      label: "Billing",
      value: `${billingWarnings.count} billing warning${
        billingWarnings.count === 1 ? "" : "s"
      } require review`,
      tone: "warning",
      cta: { label: "Review billing issues", onClick: () => onJumpTo("encounters") },
    });
  }

  // Red-flag / safety
  if (redFlag && redFlag.positives.length > 0) {
    rows.push({
      key: "red-flag",
      label: "Safety",
      value: `Positive red-flag findings: ${redFlag.positives.join(", ")}`,
      tone: "destructive",
      cta: { label: "Review history", onClick: () => onJumpTo("history") },
    });
  }

  // Missing required intake
  if (missingIntakeCount > 0) {
    rows.push({
      key: "missing-intake",
      label: "Intake",
      value: `Missing required information (${missingIntakeCount} field${
        missingIntakeCount === 1 ? "" : "s"
      })`,
      tone: "warning",
      cta: canWrite
        ? { label: "Open history", onClick: () => onJumpTo("history") }
        : null,
    });
  }

  return (
    <section
      data-testid="clinical-care-status-panel"
      aria-labelledby="clinical-care-status-title"
      className="rounded-xl border border-border bg-card/60 p-5"
    >
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3
            id="clinical-care-status-title"
            className="font-display text-lg font-semibold text-foreground"
          >
            Current care status
          </h3>
          <p className="text-sm text-muted-foreground">
            A snapshot of what needs attention today, drawn from the chart.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canWrite && (
            <Button
              size="sm"
              variant="outline"
              onClick={onOpenEncounter}
              data-testid="care-status-open-encounter"
              className="rounded-full"
            >
              <PlayCircle className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              Open current encounter
            </Button>
          )}
          {canWrite && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onJumpTo("encounters")}
              data-testid="care-status-add-note"
              className="rounded-full"
            >
              <PlusCircle className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              Add note
            </Button>
          )}
          {canWrite && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onJumpTo("outcomes")}
              data-testid="care-status-record-outcome"
              className="rounded-full"
            >
              <ClipboardList className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              Record outcome
            </Button>
          )}
          {canWrite && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => navigate(`/scheduling?patient=${patientId}`)}
              data-testid="care-status-schedule-visit"
              className="rounded-full"
            >
              <CalendarPlus className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              Schedule visit
            </Button>
          )}
        </div>
      </div>

      <ul
        data-testid="clinical-care-status-rows"
        className="divide-y divide-border/60"
      >
        {rows.map((r) => (
          <li
            key={r.key}
            data-testid={`care-status-row-${r.key}`}
            className="flex flex-wrap items-center justify-between gap-3 py-2.5"
          >
            <div className="min-w-0 flex-1">
              <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                {r.label}
              </div>
              <div
                className={[
                  "mt-0.5 text-sm",
                  r.tone === "warning"
                    ? "text-warning"
                    : r.tone === "destructive"
                      ? "text-destructive"
                      : r.tone === "muted"
                        ? "text-muted-foreground"
                        : "text-foreground",
                ].join(" ")}
              >
                {r.value}
              </div>
            </div>
            {r.cta && (
              <Button
                size="sm"
                variant="ghost"
                onClick={r.cta.onClick}
                data-testid={`care-status-cta-${r.key}`}
                className="rounded-full text-primary hover:bg-primary/10"
              >
                {r.cta.label}
              </Button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

// -------- Summary tiles (interactive) ------------------------------

function SummaryTiles({ summary, onJumpTo }) {
  if (summary === null) {
    return (
      <div
        data-testid="clinical-summary-tiles-loading"
        className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6"
      >
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-20 rounded-lg" />
        ))}
      </div>
    );
  }
  const tiles = [
    { key: "encounters",      label: "Visits",     jump: "encounters", data: summary.encounters },
    { key: "initial_exams",   label: "Exams",      jump: "encounters", data: summary.initial_exams },
    { key: "treatment_plans", label: "Plans",      jump: "care-plan",  data: summary.treatment_plans },
    { key: "re_exams",        label: "Re-exams",   jump: "care-plan",  data: summary.re_exams },
    { key: "notes",           label: "Notes",      jump: "encounters", data: summary.notes },
    { key: "diagnoses",       label: "Diagnoses",  jump: "diagnoses",  data: summary.diagnoses },
  ];
  return (
    <div
      data-testid="clinical-summary-tiles"
      className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6"
    >
      {tiles.map((t) => {
        const total = t.data?.total ?? 0;
        const open = t.data?.open ?? 0;
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onJumpTo(t.jump)}
            data-testid={`clinical-tile-${t.key}`}
            aria-label={`${t.label}: ${total} total, ${open} open. Go to section.`}
            className="group rounded-lg border border-border bg-card p-4 text-left transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background motion-reduce:transition-none motion-reduce:hover:transform-none"
          >
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground group-hover:text-foreground">
              {t.label}
            </div>
            <div className="mt-1 font-display text-2xl font-medium tracking-tight text-foreground">
              {total}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {open > 0 ? `${open} open` : "None open"}
            </div>
          </button>
        );
      })}
    </div>
  );
}

// -------- main component -------------------------------------------

export default function ClinicalTabV2({
  patientId,
  patient,
  appointments,
  providers = [],
  canWrite = false,
  currentUser,
  onReauthNeeded,
}) {
  const navigate = useNavigate();
  const [summary, setSummary] = useState(null);
  const [episodes, setEpisodes] = useState(null);
  const [diagnoses, setDiagnoses] = useState(null);
  const [history, setHistory] = useState(null);
  const [activePlan, setActivePlan] = useState(null);
  const [encountersOpenCount, setEncountersOpenCount] = useState(0);
  const [err, setErr] = useState(null);

  const [activeId, setActiveId] = useState("summary");
  const [showBackToTop, setShowBackToTop] = useState(false);
  const sectionRefs = useRef({});

  // ---- data load ------------------------------------------------
  const load = useCallback(async () => {
    setErr(null);
    try {
      const [sumRes, epRes, dxRes, histRes, planRes] = await Promise.allSettled([
        api.get(`/patients/${patientId}/clinical/summary`),
        api.get(`/patients/${patientId}/clinical/episodes`),
        api.get(`/patients/${patientId}/clinical/diagnoses`),
        api.get(`/patients/${patientId}/clinical/history`),
        api.get(`/patients/${patientId}/clinical/treatment-plans`),
      ]);
      setSummary(sumRes.status === "fulfilled" ? sumRes.value.data : {});
      setEpisodes(epRes.status === "fulfilled" ? epRes.value.data : []);
      setDiagnoses(dxRes.status === "fulfilled" ? dxRes.value.data : []);
      setHistory(histRes.status === "fulfilled" ? histRes.value.data : {});
      if (planRes.status === "fulfilled") {
        const plans = planRes.value.data || [];
        setActivePlan(plans.find((p) => p.plan_status === "active") || null);
      }
    } catch (e) {
      setErr(formatApiError(e));
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  // Fetch encounters count separately for badge on "encounters" nav item.
  useEffect(() => {
    let cancelled = false;
    api
      .get(`/patients/${patientId}/clinical/encounters`)
      .then((r) => {
        if (!cancelled) {
          const rows = r.data || [];
          const open = rows.filter(
            (e) => e.status === "in_progress" || e.sign_status === "draft" || e.sign_status === "sign_ready",
          ).length;
          setEncountersOpenCount(open);
        }
      })
      .catch(() => {
        if (!cancelled) setEncountersOpenCount(0);
      });
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  // ---- derived --------------------------------------------------
  const initials = useMemo(() => getInitials(patient), [patient]);
  const age = useMemo(
    () => (patient?.unmasked ? computeAge(patient?.date_of_birth) : null),
    [patient],
  );
  const activeEpisode = useMemo(() => pickActiveEpisode(episodes), [episodes]);
  const primaryDx = useMemo(() => pickPrimaryDiagnosis(diagnoses), [diagnoses]);
  const nextAppt = useMemo(() => pickNextAppointment(appointments), [appointments]);
  const currentProviderName = useMemo(() => {
    if (activeEpisode?.responsible_provider_name) return activeEpisode.responsible_provider_name;
    if (nextAppt?.provider_name) return nextAppt.provider_name;
    return null;
  }, [activeEpisode, nextAppt]);
  const redFlag = useMemo(() => extractRedFlagFindings(history), [history]);
  const reExamDue = useMemo(() => {
    // Derive from active plan if it exposes a due date; otherwise leave null.
    return (
      activePlan?.next_reexam_due_date ||
      activePlan?.reexam_due_date ||
      null
    );
  }, [activePlan]);
  const missingIntakeCount = useMemo(() => {
    // Count required-ish narrative fields that are blank on the chart-level
    // history doc. Conservative — only flag the always-expected clinical
    // narratives, not optional demographics.
    if (!history || Object.keys(history).length === 0) return 0;
    const requiredKeys = ["chief_complaint", "history_of_present_illness"];
    return requiredKeys.filter((k) => !history[k] || String(history[k]).trim() === "").length;
  }, [history]);

  const alerts = useMemo(() => {
    const list = [];
    if (redFlag.positives.length > 0) {
      list.push(
        `Red-flag: ${redFlag.positives.slice(0, 2).join(", ")}${
          redFlag.positives.length > 2 ? "…" : ""
        }`,
      );
    }
    return list;
  }, [redFlag]);

  const navCounts = useMemo(
    () => ({
      diagnoses: summary?.diagnoses?.open || 0,
      encounters: encountersOpenCount,
      "care-plan": summary?.treatment_plans?.open || 0,
      outcomes: summary?.outcomes?.total || 0,
      imaging: summary?.media?.total || 0,
    }),
    [summary, encountersOpenCount],
  );

  // Compute a mocked billing summary from any available data — Phase 1
  // just needs to surface count-level warnings; the actual per-encounter
  // details still live inside BillingReadinessPanel.
  const billingWarnings = useMemo(() => {
    // We don't have a chart-wide readiness endpoint; leave count 0 so
    // the row is hidden unless we later wire an aggregate. This keeps
    // us honest — no invented data.
    return { count: 0 };
  }, []);

  // ---- scroll behaviour ----------------------------------------
  const jumpTo = useCallback((id) => {
    const el = sectionRefs.current[id];
    if (!el) return;
    // Update the hash so refresh/deep-link keeps the section anchor.
    if (typeof window !== "undefined" && window.history?.replaceState) {
      window.history.replaceState(null, "", `#${id}`);
    }
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveId(id);
  }, []);

  useEffect(() => {
    // Deep-link: honour any #hash on first mount once refs exist.
    const hash = typeof window !== "undefined" ? window.location.hash.replace("#", "") : "";
    if (hash && sectionRefs.current[hash]) {
      // small delay so layout has settled before scrolling
      const t = setTimeout(() => jumpTo(hash), 60);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [summary, jumpTo]);

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return undefined;
    const io = new IntersectionObserver(
      (entries) => {
        // Choose the topmost visible section
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.target.getBoundingClientRect().top - b.target.getBoundingClientRect().top);
        if (visible[0]) {
          setActiveId(visible[0].target.id);
        }
      },
      { rootMargin: "-30% 0px -60% 0px", threshold: 0 },
    );
    Object.values(sectionRefs.current).forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, [summary]);

  useEffect(() => {
    const onScroll = () => {
      setShowBackToTop(window.scrollY > 400);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const registerSection = (id) => (el) => {
    if (el) sectionRefs.current[id] = el;
  };

  const handleOpenCurrentEncounter = useCallback(() => {
    // Delegate to encounters section — the encounter list has its own
    // "Continue" affordance for in-progress rows.
    jumpTo("encounters");
    toast.message("Open the encounter you want to continue below.");
  }, [jumpTo]);

  const scrollToTop = useCallback(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
    setActiveId("summary");
  }, []);

  // ---- render --------------------------------------------------
  return (
    <div data-testid="patient-clinical-tab-v2" className="space-y-8">
      {/* Sticky context header + section nav */}
      <div className="sticky top-0 z-30 -mx-4 sm:-mx-6 lg:-mx-8">
        <PatientContextHeader
          patient={patient || {}}
          age={age}
          initials={initials}
          activeEpisode={activeEpisode}
          primaryDx={primaryDx}
          currentProviderName={currentProviderName}
          nextAppt={nextAppt}
          reExamDue={reExamDue}
          alerts={alerts}
        />
        <SectionNav activeId={activeId} onJump={jumpTo} counts={navCounts} />
      </div>

      {err && (
        <div
          data-testid="clinical-v2-error"
          role="alert"
          className="rounded-sm border border-destructive/30 bg-destructive-soft p-3 text-sm text-destructive"
        >
          {err}
        </div>
      )}

      {/* Summary section */}
      <section
        id="summary"
        ref={registerSection("summary")}
        aria-labelledby="clinical-summary-title"
        className="scroll-mt-40 space-y-6"
      >
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2
              id="clinical-summary-title"
              className="font-display text-xl font-semibold text-foreground"
            >
              Clinical summary
            </h2>
            <p className="text-sm text-muted-foreground">
              Longitudinal chart view. Every artifact lives under this patient.
            </p>
          </div>
          {summary?.generated_at && (
            <span className="text-xs text-muted-foreground">
              Synced {formatDateTime(summary.generated_at)}
            </span>
          )}
        </div>

        <CurrentCareStatusPanel
          activeEpisode={activeEpisode}
          primaryDx={primaryDx}
          activePlan={activePlan}
          nextAppt={nextAppt}
          reExamDue={reExamDue}
          unsignedCount={
            (summary?.notes?.open || 0) +
            (summary?.initial_exams?.open || 0) +
            (summary?.re_exams?.open || 0)
          }
          billingWarnings={billingWarnings}
          redFlag={redFlag}
          missingIntakeCount={missingIntakeCount}
          onJumpTo={jumpTo}
          onOpenEncounter={handleOpenCurrentEncounter}
          canWrite={canWrite}
          navigate={navigate}
          patientId={patientId}
        />

        <SummaryTiles summary={summary} onJumpTo={jumpTo} />

        {/* Episodes list (inline, since the summary anchors chart context) */}
        <EpisodesSection
          patientId={patientId}
          providers={providers}
          canWrite={canWrite}
          onReauthNeeded={onReauthNeeded}
          episodes={episodes}
          onEpisodesChange={setEpisodes}
          onSummaryReload={load}
        />
      </section>

      {/* History */}
      <section
        id="history"
        ref={registerSection("history")}
        className="scroll-mt-40"
      >
        <IntakeHistoryCard
          patientId={patientId}
          canWrite={canWrite}
          onReauthNeeded={onReauthNeeded}
        />
      </section>

      {/* Diagnoses */}
      <section
        id="diagnoses"
        ref={registerSection("diagnoses")}
        className="scroll-mt-40"
      >
        <DiagnosesCard
          patientId={patientId}
          episodes={episodes || []}
          canWrite={canWrite}
          onReauthNeeded={onReauthNeeded}
        />
      </section>

      {/* Encounters (incl. exams + notes for grouping) */}
      <section
        id="encounters"
        ref={registerSection("encounters")}
        className="scroll-mt-40 space-y-8"
      >
        <EncountersCard
          patientId={patientId}
          canWrite={canWrite}
          currentUser={currentUser}
          onReauthNeeded={onReauthNeeded}
        />
        <InitialExamsCard patientId={patientId} canWrite={canWrite} />
        <FollowUpNotesCard patientId={patientId} />
      </section>

      {/* Care plan */}
      <section
        id="care-plan"
        ref={registerSection("care-plan")}
        className="scroll-mt-40 space-y-8"
      >
        <TreatmentPlansCard
          patientId={patientId}
          canWrite={canWrite}
          episodes={episodes || []}
          onReauthNeeded={onReauthNeeded}
        />
        <ReExamsCard patientId={patientId} />
      </section>

      {/* Timeline */}
      <section
        id="timeline"
        ref={registerSection("timeline")}
        className="scroll-mt-40"
      >
        <CareTimelineCard patientId={patientId} />
      </section>

      {/* Imaging */}
      <section
        id="imaging"
        ref={registerSection("imaging")}
        className="scroll-mt-40"
      >
        <MediaCard
          patientId={patientId}
          canWrite={canWrite}
          onReauthNeeded={onReauthNeeded}
        />
      </section>

      {/* Outcomes */}
      <section
        id="outcomes"
        ref={registerSection("outcomes")}
        className="scroll-mt-40"
      >
        <OutcomesCard
          patientId={patientId}
          canWrite={canWrite}
          onReauthNeeded={onReauthNeeded}
        />
      </section>

      <BackToTopButton visible={showBackToTop} onClick={scrollToTop} />
    </div>
  );
}
