/**
 * ClinicalTabV2 — Phase 1 redesign of Patient Profile > Clinical.
 *
 * This file is the *shell*: data load, scroll behaviour, telemetry
 * hooks, and section composition. The rendered pieces (patient context
 * header, section nav, care-status panel, summary tiles) live in
 * sibling files under `pages/clinical/`.
 *
 * All permissions, masking, audit, signed-record rules, and API
 * contracts flow through the wrapped sub-cards untouched.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ArrowUp } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { formatDateTime } from "../../utils/time";
import { trackUiEvent } from "../../utils/telemetry";

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

import PatientContextHeader from "./PatientContextHeader";
import SectionNav from "./SectionNav";
import CurrentCareStatusPanel from "./CurrentCareStatusPanel";
import SummaryTiles from "./SummaryTiles";
import ActiveEpisodeCard from "./ActiveEpisodeCard";
import GroupedEncountersCard from "./GroupedEncountersCard";
import GroupedTimelineCard from "./GroupedTimelineCard";
import SafetySummary from "./SafetySummary";
import IntakeHistoryProgressive from "./IntakeHistoryProgressive";
import ReExamSection from "./ReExamSection";
import NextActionsPanel from "./NextActionsPanel";
import OutcomesSection from "./OutcomesSection";
import ImagingCard from "./ImagingCard";
import DataQualityPanel from "./DataQualityPanel";
import WorkspaceModeSwitcher from "./WorkspaceModeSwitcher";
import SummaryConfigDrawer from "./SummaryConfigDrawer";
import { effectiveMode, resolveSummaryOrder, sectionOrderForMode } from "./workspaceModes";
import { getOrCreateRouteInstanceToken } from "./useClinicalReturnState";
import { useFeatureFlag } from "../../utils/featureFlags";
import {
  NAV_ITEMS,
  computeAge,
  extractRedFlagFindings,
  getInitials,
  pickActiveEpisode,
  pickNextAppointment,
  pickPrimaryDiagnosis,
} from "./clinicalHelpers";

function BackToTopButton({ visible, onClick }) {
  if (!visible) return null;
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid="clinical-back-to-top"
      aria-label="Back to top"
      className="fixed bottom-6 right-6 z-50 inline-flex items-center gap-1.5 rounded-full border border-border bg-card/95 px-3 py-2 text-xs font-medium text-foreground shadow-lg backdrop-blur transition-transform hover:-translate-y-0.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 motion-reduce:transition-none motion-reduce:hover:transform-none"
    >
      <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
      Back to top
    </button>
  );
}

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
  const [phase2WaveA] = useFeatureFlag("clinicalRedesignPhase2WaveA");
  const [phase2WaveB] = useFeatureFlag("clinicalRedesignPhase2WaveB");
  const [phase3] = useFeatureFlag("clinicalRedesignPhase3");
  const [phase3Slice3] = useFeatureFlag("clinicalRedesignPhase3Slice3");
  const [phase3Slice4] = useFeatureFlag("clinicalRedesignPhase3Slice4");
  const [phase3Slice5] = useFeatureFlag("clinicalRedesignPhase3Slice5");
  const [summary, setSummary] = useState(null);
  const [episodes, setEpisodes] = useState(null);
  const [diagnoses, setDiagnoses] = useState(null);
  const [history, setHistory] = useState(null);
  const [activePlan, setActivePlan] = useState(null);
  const [encounterGroups, setEncounterGroups] = useState([]);
  const [encountersOpenCount, setEncountersOpenCount] = useState(0);
  const [err, setErr] = useState(null);

  // Route-instance token. Generated once on chart mount and mirrored
  // into `history.state.ccms_route_token` so browser back/forward and
  // in-tab navigation preserve return state without ever exposing a
  // patient identifier. Direct URL entry starts from a fresh token
  // and empty state — no cross-chart bleed-through.
  const [routeInstanceToken] = useState(() => getOrCreateRouteInstanceToken());

  const [activeId, setActiveId] = useState("summary");
  const [showBackToTop, setShowBackToTop] = useState(false);
  const sectionRefs = useRef({});
  const suppressObserverUntil = useRef(0);

  // ---- Slice 5A/5B — workspace mode + summary rail order --------
  const [workspaceMode, setWorkspaceMode] = useState(() =>
    effectiveMode({
      role: currentUser?.role,
      requested: currentUser?.clinical_ui_defaults?.default_workspace_mode || null,
    }),
  );
  const [summaryConfigOpen, setSummaryConfigOpen] = useState(false);
  const summaryOrder = useMemo(
    () => resolveSummaryOrder({
      mode: workspaceMode,
      stored: currentUser?.clinical_ui_defaults?.summary_module_order || null,
    }),
    [workspaceMode, currentUser],
  );
  const [savedSummaryOrder, setSavedSummaryOrder] = useState(summaryOrder);
  useEffect(() => setSavedSummaryOrder(summaryOrder), [summaryOrder]);

  // Slice 5A: reorder NAV_ITEMS according to workspace mode (only
  // section positions change — the section content itself is unchanged).
  const modeSectionOrder = useMemo(
    () => (phase3Slice5 ? sectionOrderForMode(workspaceMode) : NAV_ITEMS.map((n) => n.id)),
    [phase3Slice5, workspaceMode],
  );
  const navCountsWithMode = useMemo(() => {
    const base = {
      diagnoses: summary?.diagnoses?.open || 0,
      encounters: encountersOpenCount,
      "care-plan": summary?.treatment_plans?.open || 0,
      outcomes: summary?.outcomes?.total || 0,
      imaging: summary?.media?.total || 0,
    };
    return base;
  }, [summary, encountersOpenCount]);

  // ---- data load ----------------------------------------------------
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
      const sectionMap = [
        [sumRes, "summary"],
        [epRes, "summary"],
        [dxRes, "diagnoses"],
        [histRes, "history"],
        [planRes, "care-plan"],
      ];
      for (const [res, section] of sectionMap) {
        if (res.status === "rejected") {
          const code = res.reason?.response?.status
            ? String(res.reason.response.status)
            : "network_error";
          trackUiEvent("clinical.section.load_failed", { section, error_code: code });
        }
      }
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
      trackUiEvent("clinical.section.load_failed", { section: "summary", error_code: "load_exception" });
    }
  }, [patientId]);

  useEffect(() => {
    trackUiEvent("clinical.layout.activated", { layout: "v2" });
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    api
      .get(`/patients/${patientId}/clinical/encounters`)
      .then((r) => {
        if (!cancelled) {
          const rows = r.data || [];
          const open = rows.filter(
            (e) =>
              e.status === "in_progress" ||
              e.sign_status === "draft" ||
              e.sign_status === "sign_ready",
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

  // Grouped encounters power Phase 3 next-action rules that depend on
  // per-visit documentation / billing status. When Wave A is off, the
  // grouped endpoint is still safe to call — it's presentation-layer
  // and does not mutate source records — but we skip it to keep the
  // legacy layout's network footprint unchanged.
  useEffect(() => {
    if (!phase3) return undefined;
    let cancelled = false;
    api
      .get(`/patients/${patientId}/clinical/encounters/grouped`)
      .then((r) => {
        if (!cancelled) setEncounterGroups(r.data?.groups || []);
      })
      .catch(() => {
        if (!cancelled) setEncounterGroups([]);
      });
    return () => {
      cancelled = true;
    };
  }, [patientId, phase3]);

  // ---- derived ------------------------------------------------------
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
  const reExamDue = useMemo(
    () => activePlan?.next_reexam_due_date || activePlan?.reexam_due_date || null,
    [activePlan],
  );
  const missingIntakeCount = useMemo(() => {
    if (!history || Object.keys(history).length === 0) return 0;
    const requiredKeys = ["chief_complaint", "history_of_present_illness"];
    return requiredKeys.filter(
      (k) => !history[k] || String(history[k]).trim() === "",
    ).length;
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

  // Chart-wide billing readiness. Reused from the aggregate endpoint;
  // permission-scoped server-side, so a 403 / 5xx cleanly collapses to
  // the hidden state below (no misleading "0 warnings" flash).
  const [billingAggregate, setBillingAggregate] = useState(null); // null = unknown/hidden

  useEffect(() => {
    let cancelled = false;
    api
      .get(`/patients/${patientId}/clinical/billing-readiness/aggregate`)
      .then((r) => {
        if (!cancelled) setBillingAggregate(r.data);
      })
      .catch(() => {
        // 403 (no billing permission), 5xx, or network — leave the
        // aggregate as null so the panel omits the row instead of
        // showing a misleading zero.
        if (!cancelled) setBillingAggregate(null);
      });
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  const billingWarnings = useMemo(() => billingAggregate, [billingAggregate]);

  // ---- scroll behaviour --------------------------------------------
  const jumpTo = useCallback((id, opts = {}) => {
    const el = sectionRefs.current[id];
    if (!el) return;
    if (typeof window !== "undefined" && window.history) {
      const hash = `#${id}`;
      if (opts.userInitiated && window.history.pushState && window.location.hash !== hash) {
        window.history.pushState(null, "", hash);
      } else if (window.history.replaceState) {
        window.history.replaceState(null, "", hash);
      }
    }
    if (opts.userInitiated) {
      trackUiEvent("clinical.nav.jump", { section: id });
    }
    suppressObserverUntil.current = Date.now() + 600;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveId(id);
  }, []);

  useEffect(() => {
    const onPop = () => {
      const hash = window.location.hash.replace("#", "");
      if (hash && sectionRefs.current[hash]) {
        jumpTo(hash);
      }
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [jumpTo]);

  useEffect(() => {
    const hash = typeof window !== "undefined" ? window.location.hash.replace("#", "") : "";
    if (hash && sectionRefs.current[hash]) {
      const t = setTimeout(() => jumpTo(hash), 60);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [summary, jumpTo]);

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return undefined;
    const io = new IntersectionObserver(
      (entries) => {
        if (Date.now() < suppressObserverUntil.current) return;
        const nearBottom =
          window.innerHeight + window.scrollY >=
          document.documentElement.scrollHeight - 8;
        if (nearBottom) {
          setActiveId(NAV_ITEMS[NAV_ITEMS.length - 1].id);
          return;
        }
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort(
            (a, b) => a.target.getBoundingClientRect().top - b.target.getBoundingClientRect().top,
          );
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
      if (Date.now() < suppressObserverUntil.current) return;
      const nearBottom =
        window.innerHeight + window.scrollY >=
        document.documentElement.scrollHeight - 8;
      if (nearBottom) {
        setActiveId(NAV_ITEMS[NAV_ITEMS.length - 1].id);
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const registerSection = (id) => (el) => {
    if (el) sectionRefs.current[id] = el;
  };

  const handleOpenCurrentEncounter = useCallback(() => {
    jumpTo("encounters");
    toast.message("Open the encounter you want to continue below.");
  }, [jumpTo]);

  const scrollToTop = useCallback(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
    setActiveId("summary");
  }, []);

  // ---- render -------------------------------------------------------
  return (
    <div data-testid="patient-clinical-tab-v2" className="space-y-8">
      <a
        href="#summary"
        data-testid="clinical-skip-link"
        onClick={(e) => {
          e.preventDefault();
          jumpTo("summary", { userInitiated: true });
        }}
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-full focus:border focus:border-primary focus:bg-background focus:px-4 focus:py-2 focus:text-sm focus:text-primary focus:shadow-lg"
      >
        Skip to clinical summary
      </a>
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
            <span
              data-testid="clinical-refresh-timestamp"
              className="text-sm text-muted-foreground"
              title="This chart's summary payload was last refreshed at this time."
            >
              Chart data last refreshed {formatDateTime(summary.generated_at)}
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

        {phase3Slice5 && (
          <div
            data-testid="clinical-workspace-toolbar"
            className="flex flex-wrap items-center justify-between gap-2 rounded-full border border-border bg-card/60 px-4 py-2"
          >
            <WorkspaceModeSwitcher
              currentUser={currentUser}
              mode={workspaceMode}
              onModeChange={setWorkspaceMode}
            />
            <button
              type="button"
              onClick={() => setSummaryConfigOpen(true)}
              data-testid="clinical-summary-config-open"
              className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-border bg-card px-4 py-2 text-sm text-foreground hover:bg-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
            >
              Configure summary
            </button>
          </div>
        )}

        {phase3Slice4 && (
          <DataQualityPanel
            canWrite={canWrite}
            summary={summary}
            activePlan={activePlan}
            primaryDx={primaryDx}
            encounterGroups={encounterGroups}
            imaging={[]}
            episodes={episodes || []}
            outcomeEntries={[]}
            onJumpTo={jumpTo}
          />
        )}

        <SummaryTiles summary={summary} onJumpTo={jumpTo} />

        {phase3 && (
          <NextActionsPanel
            canWrite={canWrite}
            summary={summary}
            activePlan={activePlan}
            primaryDx={primaryDx}
            missingIntakeCount={missingIntakeCount}
            reExamDue={reExamDue}
            billingAggregate={billingAggregate}
            encounterGroups={encounterGroups}
            routeInstanceToken={routeInstanceToken}            onJumpTo={jumpTo}
          />
        )}

        {phase2WaveA && (
          <ActiveEpisodeCard
            patientId={patientId}
            episode={activeEpisode}
            activePlan={activePlan}
            primaryDx={primaryDx}
            nextAppt={nextAppt}
            reExamDue={reExamDue}
            canWrite={canWrite}
            onJumpTo={jumpTo}
            onReauthNeeded={onReauthNeeded}
            onNewEpisode={() => jumpTo("summary")}
            onEpisodeClosed={() => load()}
          />
        )}

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

      <section id="history" ref={registerSection("history")} className="scroll-mt-40 space-y-4">
        {phase2WaveB ? (
          <>
            <SafetySummary history={history} />
            <IntakeHistoryProgressive
              history={history}
              patientId={patientId}
              canWrite={canWrite}
              onReauthNeeded={onReauthNeeded}
            />
          </>
        ) : (
          <IntakeHistoryCard
            patientId={patientId}
            canWrite={canWrite}
            onReauthNeeded={onReauthNeeded}
          />
        )}
      </section>

      <section id="diagnoses" ref={registerSection("diagnoses")} className="scroll-mt-40">
        <DiagnosesCard
          patientId={patientId}
          episodes={episodes || []}
          canWrite={canWrite}
          onReauthNeeded={onReauthNeeded}
        />
      </section>

      <section
        id="encounters"
        ref={registerSection("encounters")}
        className="scroll-mt-40 space-y-8"
      >
        {phase2WaveA ? (
          <GroupedEncountersCard patientId={patientId} />
        ) : (
          <>
            <EncountersCard
              patientId={patientId}
              canWrite={canWrite}
              currentUser={currentUser}
              onReauthNeeded={onReauthNeeded}
            />
            <InitialExamsCard patientId={patientId} canWrite={canWrite} />
            <FollowUpNotesCard patientId={patientId} />
          </>
        )}
      </section>

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
        {phase2WaveB ? (
          <ReExamSection
            patientId={patientId}
            activePlan={activePlan}
            canWrite={canWrite}
            onJumpTo={jumpTo}
          />
        ) : (
          <ReExamsCard patientId={patientId} />
        )}
      </section>

      <section id="timeline" ref={registerSection("timeline")} className="scroll-mt-40">
        {phase2WaveA ? (
          <GroupedTimelineCard
            patientId={patientId}
            providers={providers}
            episodes={episodes || []}
            clinicalUiDefaults={currentUser?.clinical_ui_defaults}
            routeInstanceToken={routeInstanceToken}
          />
        ) : (
          <CareTimelineCard patientId={patientId} />
        )}
      </section>

      <section id="imaging" ref={registerSection("imaging")} className="scroll-mt-40 space-y-4">
        {phase3Slice4 ? (
          <ImagingCard
            patientId={patientId}
            canWrite={canWrite}
            routeInstanceToken={routeInstanceToken}
            onOpenImaging={() => {
              const el = document.getElementById("imaging-legacy");
              if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
            }}
          />
        ) : null}
        <div id="imaging-legacy" className={phase3Slice4 ? "mt-4" : ""}>
          <MediaCard
            patientId={patientId}
            canWrite={canWrite}
            onReauthNeeded={onReauthNeeded}
          />
        </div>
      </section>

      <section id="outcomes" ref={registerSection("outcomes")} className="scroll-mt-40">
        {phase3Slice3 ? (
          <OutcomesSection
            patientId={patientId}
            canWrite={canWrite}
            activePlan={activePlan}
            routeInstanceToken={routeInstanceToken}
            suggestionContext={{
              patient_age: age,
              primary_dx_body_region: primaryDx?.body_region || null,
              episode_case_type: activeEpisode?.case_type || null,
            }}
            onRecordOutcome={() => {
              // Delegate to the legacy card for the actual capture UI —
              // Slice 3 is intentionally read-only. Scroll the legacy
              // card into view so the user can complete the workflow.
              const el = document.getElementById("outcomes-legacy");
              if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
            }}
          />
        ) : null}
        <div id="outcomes-legacy" className={phase3Slice3 ? "mt-4" : ""}>
          <OutcomesCard
            patientId={patientId}
            canWrite={canWrite}
            onReauthNeeded={onReauthNeeded}
          />
        </div>
      </section>

      <BackToTopButton visible={showBackToTop} onClick={scrollToTop} />

      {phase3Slice5 && (
        <SummaryConfigDrawer
          open={summaryConfigOpen}
          onOpenChange={setSummaryConfigOpen}
          mode={workspaceMode}
          currentUser={currentUser}
          order={savedSummaryOrder}
          allowedModules={null}
          onOrderChange={setSavedSummaryOrder}
        />
      )}
    </div>
  );
}
