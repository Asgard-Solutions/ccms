/**
 * BillingReadinessPanel — Phase 8. Read-only per-encounter readiness
 * evaluator. Collapsible so long encounter lists don't blow up visually;
 * the header chip is always visible so users can see at a glance whether
 * an encounter is `ready`, `warnings`, or `blocked`.
 *
 * This panel never mutates billing data — it's an evaluative view.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleX,
  FilePlus2,
  Info,
  Loader2,
  Receipt,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { usePatientPolicies, usePayers } from "../billing/useBillingAdmin";

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
export default function BillingReadinessPanel({
  patientId,
  encounterId,
  defaultOpen = false,
  currentUser,
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [report, setReport] = useState(null);
  const [err, setErr] = useState(null);
  const [claimDialogOpen, setClaimDialogOpen] = useState(false);

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
  const canClaim =
    !!report &&
    (overall === "ready" ||
      overall === "warnings" ||
      currentUser?.role === "admin");

  return (
    <section
      data-testid={`billing-readiness-${encounterId}`}
      className="rounded-lg border border-border bg-card"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid={`billing-readiness-${encounterId}-toggle`}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <div className="flex min-w-0 items-center gap-2">
          <Receipt className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span className="font-display text-sm font-semibold text-foreground">
            Billing readiness
          </span>
          {report ? (
            <>
              <Badge
                variant="outline"
                data-testid={`billing-readiness-${encounterId}-status`}
                className={`text-[10px] font-medium capitalize ${meta.tone}`}
              >
                <HeaderIcon className="mr-1 h-3 w-3" aria-hidden="true" />
                {meta.label}
              </Badge>
              {(() => {
                // Surface count + top-priority summary in the header so
                // billing folks don't need to expand every row.
                const nonPassing = (report.checks || []).filter((c) => !c.passed);
                if (nonPassing.length === 0) return null;
                const top = nonPassing.find((c) => c.severity === "fail")
                  || nonPassing.find((c) => c.severity === "warn")
                  || nonPassing[0];
                return (
                  <span
                    data-testid={`billing-readiness-${encounterId}-summary`}
                    className="ml-1 min-w-0 truncate text-xs text-muted-foreground"
                  >
                    {nonPassing.length} warning{nonPassing.length === 1 ? "" : "s"}
                    {top?.message ? ` · ${top.message}` : ""}
                  </span>
                );
              })()}
            </>
          ) : (
            <Badge variant="outline" className="text-[10px] text-muted-foreground italic">
              Loading
            </Badge>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {report && (report.checks || []).some((c) => !c.passed) && (
            <span
              className="hidden text-xs text-primary sm:inline"
              aria-hidden="true"
            >
              Review billing issues
            </span>
          )}
          {open ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          )}
        </div>
      </button>

      {open && (
        <div className="border-t border-border px-4 py-3">
          {err ? (
            <p className="text-xs text-destructive">{err}</p>
          ) : report === null ? (
            <Skeleton className="h-24 rounded-sm" />
          ) : (
            <>
              <ReportBody report={report} />
              <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
                <p className="text-[11px] text-muted-foreground">
                  Create a draft claim from this encounter. CPT codes and
                  billed amounts must be filled in the claim editor before
                  submission.
                </p>
                <Button
                  size="sm"
                  disabled={!canClaim}
                  onClick={() => setClaimDialogOpen(true)}
                  data-testid={`billing-readiness-${encounterId}-create-claim-btn`}
                  className="rounded-sm"
                >
                  <FilePlus2 className="mr-1.5 h-3.5 w-3.5" />
                  Create claim draft
                </Button>
              </div>
              {overall === "blocked" && currentUser?.role !== "admin" && (
                <p
                  className="mt-1 text-[10px] text-destructive"
                  data-testid={`billing-readiness-${encounterId}-blocked-hint`}
                >
                  Resolve blocking checks before generating a claim.
                </p>
              )}
            </>
          )}
        </div>
      )}

      <CreateClaimDialog
        open={claimDialogOpen}
        onOpenChange={setClaimDialogOpen}
        report={report}
        patientId={patientId}
        encounterId={encounterId}
        isAdmin={currentUser?.role === "admin"}
      />
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

function CreateClaimDialog({
  open,
  onOpenChange,
  report,
  patientId,
  encounterId,
  isAdmin,
}) {
  const navigate = useNavigate();
  const { rows: payers, loading: payersLoading } = usePayers({ activeOnly: true });
  const { rows: policies, loading: policiesLoading } = usePatientPolicies(
    patientId,
  );
  const activePolicies = useMemo(
    () => policies.filter((p) => p.status === "active"),
    [policies],
  );
  const [payerId, setPayerId] = useState("");
  const [policyId, setPolicyId] = useState("");
  const [placeOfService, setPlaceOfService] = useState("11");
  const [notes, setNotes] = useState("");
  const [force, setForce] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setPayerId("");
    setPolicyId("");
    setPlaceOfService("11");
    setNotes("");
    setForce(false);
  }, [open]);

  // Auto-select primary policy's payer on open.
  useEffect(() => {
    if (!open || payerId || !activePolicies.length) return;
    const primary =
      activePolicies.find((p) => p.rank === "primary") || activePolicies[0];
    if (primary) {
      setPolicyId(primary.id);
      setPayerId(primary.payer_id);
    }
  }, [open, activePolicies, payerId]);

  const blocked = report?.overall_status === "blocked";
  const mustForce = blocked;

  const submit = async () => {
    if (!payerId) {
      toast.error("Pick a payer first");
      return;
    }
    if (mustForce && !isAdmin) {
      toast.error("Only admins can override a blocked encounter");
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await api.post("/billing/claims/from-encounter", {
        encounter_id: encounterId,
        payer_id: payerId,
        policy_id: policyId || null,
        place_of_service: placeOfService || "11",
        notes: notes || null,
        force: mustForce && force,
      });
      toast.success(
        `Claim draft created (${data.billed_cents === 0 ? "unpriced" : "$" + (data.billed_cents / 100).toFixed(2)})`,
      );
      onOpenChange(false);
      navigate(`/billing/claims/${data.id}`);
    } catch (e) {
      const payload = e?.response?.data?.detail;
      if (
        e?.response?.status === 409 &&
        typeof payload === "object" &&
        payload?.blocking
      ) {
        toast.error(
          `Blocked: ${payload.blocking.map((b) => b.label).join(", ")}`,
        );
      } else {
        toast.error(formatApiError(e));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="create-claim-from-encounter-dialog"
        className="max-w-lg rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            Create claim draft from encounter
          </DialogTitle>
          <DialogDescription>
            Pick the payer + policy. The clinical details (diagnoses,
            procedures, provider, DOS) are auto-filled from the signed note.
            CPT codes default to hints — you&rsquo;ll finalise them in the
            claim editor.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Payer
            </Label>
            <Select value={payerId} onValueChange={setPayerId}>
              <SelectTrigger
                data-testid="claim-from-enc-payer-select"
                className="rounded-sm"
              >
                <SelectValue
                  placeholder={payersLoading ? "Loading…" : "Select payer…"}
                />
              </SelectTrigger>
              <SelectContent>
                {payers.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Policy (optional)
            </Label>
            <Select
              value={policyId || "__none__"}
              onValueChange={(v) => setPolicyId(v === "__none__" ? "" : v)}
            >
              <SelectTrigger
                data-testid="claim-from-enc-policy-select"
                className="rounded-sm"
              >
                <SelectValue
                  placeholder={
                    policiesLoading ? "Loading…" : "No policy (patient-responsibility)"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">
                  None (patient-responsibility)
                </SelectItem>
                {activePolicies.map((p) => {
                  const payer = payers.find((x) => x.id === p.payer_id);
                  return (
                    <SelectItem key={p.id} value={p.id}>
                      {p.rank} · {payer?.name || "Unknown"} · {p.member_id}
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
          </div>

          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Place of service
            </Label>
            <Input
              value={placeOfService}
              onChange={(e) => setPlaceOfService(e.target.value)}
              maxLength={2}
              placeholder="CMS POS code — default 11 (office)"
              data-testid="claim-from-enc-pos"
              className="rounded-sm"
            />
          </div>

          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Notes (optional)
            </Label>
            <Input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional billing notes"
              data-testid="claim-from-enc-notes"
              className="rounded-sm"
            />
          </div>

          {mustForce && (
            <label
              className="flex items-start gap-2 rounded-sm border border-destructive/30 bg-destructive/10 p-2 text-[11px] text-destructive"
              data-testid="claim-from-enc-force-section"
            >
              <input
                type="checkbox"
                checked={force}
                disabled={!isAdmin}
                onChange={(e) => setForce(e.target.checked)}
                data-testid="claim-from-enc-force-checkbox"
                className="mt-0.5"
              />
              <span>
                Encounter is blocked. {isAdmin
                  ? "Force-create a claim anyway (admin override — audited)."
                  : "Ask an admin to override or resolve blocking checks."}
              </span>
            </label>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
            className="rounded-sm"
          >
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={submitting || !payerId || (mustForce && (!isAdmin || !force))}
            data-testid="claim-from-enc-submit-btn"
            className="rounded-sm"
          >
            {submitting ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <FilePlus2 className="mr-1.5 h-3.5 w-3.5" />
            )}
            Create draft
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
