import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, Archive, Download, Eye, EyeOff, FileText, MoreHorizontal, Pencil, Plus, ShieldAlert, Trash2 } from "lucide-react";
import { api, formatApiError } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import { useProviders } from "../contexts/ProvidersContext";
import { formatDate, formatDateTime, relativeFromNow } from "../utils/time";
import { useFeatureFlag } from "../utils/featureFlags";
import { trackUiEvent } from "../utils/telemetry";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { formatPhoneDisplay } from "../utils/phone";
import { PatientWizardDialog } from "../components/patient-wizard/PatientWizardDialog";
import { payloadToForm } from "./patientWizardLogic";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../components/ui/tabs";
import DateRangeFilter, { isInRange } from "../components/DateRangeFilter";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../components/ui/alert-dialog";
import { Label } from "../components/ui/label";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import BreakGlassDialog from "../components/BreakGlassDialog";
import ReauthDialog from "../components/ReauthDialog";
import PatientDocumentsCard from "../components/PatientDocumentsCard";
import PatientLedgerCard from "./billing/PatientLedgerCard";
import PatientStatementsCard from "./billing/PatientStatementsCard";
import PatientQuestionnairesCard from "./patients/PatientQuestionnairesCard";
import ChartBriefCard from "./ai/ChartBriefCard";
import PatientSemanticSearch from "./ai/PatientSemanticSearch";
import PatientInsuranceManager from "./billing/PatientInsuranceManager";
import { PatientEligibilityCard } from "./billing/PatientEligibilityCard";
import ChargeCaptureDialog from "./billing/ChargeCaptureDialog";
import ClinicalTab from "./clinical/ClinicalTab";
import ClinicalTabV2 from "./clinical/ClinicalTabV2";

// ---------------------------------------------------------------------------
// Expanded-intake section renderers (Phase 4).
//
// Each helper below is defensive: it never assumes a nested section exists
// and short-circuits to `null` when the backend returns neither a legacy
// scalar nor a grouped object for that slice of intake.
// ---------------------------------------------------------------------------

function hasValue(v) {
  if (v === null || v === undefined) return false;
  if (typeof v === "string") return v.trim() !== "";
  if (Array.isArray(v)) return v.some(hasValue);
  if (typeof v === "object") return Object.values(v).some(hasValue);
  return true;
}

function Row({ label, children, testId }) {
  if (!hasValue(children)) return null;
  return (
    <div className="grid grid-cols-1 gap-0.5 sm:grid-cols-[200px_1fr] sm:gap-4" data-testid={testId}>
      <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="text-sm leading-relaxed text-foreground break-words">
        {Array.isArray(children) ? children.join(", ") : children}
      </span>
    </div>
  );
}

function IntakeCard({ title, hint, testId, children }) {
  return (
    <div
      data-testid={testId}
      className="rounded-sm border border-border bg-card p-6"
    >
      <div className="mb-4 border-b border-border pb-2">
        <h3 className="font-display text-lg font-medium text-foreground">{title}</h3>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

function formatAddressObj(a) {
  if (!a || typeof a !== "object") return null;
  const line = [
    a.line1,
    a.line2,
    [a.city, a.state].filter(Boolean).join(", ") || null,
    a.postal_code,
    a.country,
  ]
    .filter(Boolean)
    .join(" · ");
  return line || null;
}

// ---------------------------------------------------------------------------
// PatientOverview — read-only mirror of the Edit Patient wizard. Renders the
// same section hierarchy (Identity → Contact → Address → Emergency → Care
// assignment → Employment → Responsible party → Insurance) so the Overview
// tab and the Edit modal feel like two views of the same object.
// ---------------------------------------------------------------------------

function OverviewField({ label, value, testId, full = false }) {
  return (
    <div
      data-testid={testId}
      className={full ? "sm:col-span-3" : ""}
    >
      <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-sm text-foreground">
        {hasValue(value) ? value : <span className="text-muted-foreground">—</span>}
      </div>
    </div>
  );
}

function OverviewSection({ title, hint, children, testId }) {
  return (
    <section
      data-testid={testId}
      className="rounded-sm border border-border bg-card p-6"
    >
      <header className="mb-4 border-b border-border pb-2">
        <h3 className="font-display text-lg font-medium text-foreground">{title}</h3>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </header>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {children}
      </div>
    </section>
  );
}

function InsuranceRow({ label, carrier, plan, memberId, planType, testId }) {
  if (!hasValue(carrier) && !hasValue(plan) && !hasValue(memberId)) return null;
  const badge = planType ? (
    <span className="ml-2 rounded-sm bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
      {planType}
    </span>
  ) : null;
  return (
    <div
      data-testid={testId}
      className="rounded-sm border border-border bg-background p-3"
    >
      <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-foreground">
        <span className="font-medium">{carrier || "—"}</span>
        {hasValue(plan) && <span className="text-muted-foreground">· {plan}</span>}
        {badge}
      </div>
      {hasValue(memberId) && (
        <div className="mt-0.5 text-xs text-muted-foreground">
          Member ID <span className="text-foreground">{memberId}</span>
        </div>
      )}
    </div>
  );
}

function InsurancePlanBlock({ plan, label, testId }) {
  if (!plan || typeof plan !== "object") return null;
  const fields = [
    ["Carrier", plan.carrier],
    ["Plan name", plan.plan_name],
    ["Plan type", plan.plan_type],
    ["Member ID", plan.member_id],
    ["Group #", plan.group_number],
    ["Policy holder", plan.policy_holder_name],
    ["Relationship", plan.policy_holder_relationship],
    ["Policy holder DOB", plan.policy_holder_dob],
    ["Effective", plan.effective_date],
    ["Termination", plan.termination_date],
    ["Copay", plan.copay],
    ["Deductible", plan.deductible],
  ].filter(([, v]) => hasValue(v));
  if (!fields.length) return null;
  return (
    <div data-testid={testId} className="rounded-sm border border-border bg-background p-4">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="space-y-1.5">
        {fields.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[140px_1fr] gap-3 text-sm">
            <span className="text-muted-foreground">{k}</span>
            <span className="text-foreground">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PatientOverview({ patient, providers, locations, onEdit, canEdit }) {
  const demo = patient.demographics || {};
  const contact = patient.contact || {};
  const addr = patient.address_details || {};
  const ec = patient.emergency_contact_details || {};
  const admin = patient.admin || {};
  const g = patient.guarantor || {};
  const ins = patient.insurance || {};
  const primary = ins.primary || {};
  const secondary = ins.secondary || {};

  const providerId = admin.primary_provider_id;
  const providerLabel =
    (providers || []).find((p) => p.id === providerId)?.name || providerId || null;
  const locationLabel =
    (locations || []).find((l) => l.id === patient.location_id)?.name
    || patient.location_name
    || null;

  const consentSummary = [
    contact.sms_consent && "SMS",
    contact.email_consent && "Email",
    contact.voicemail_consent && "Voicemail",
  ].filter(Boolean).join(" · ") || null;

  const addressLine = formatAddressObj(addr) || patient.address || null;
  const guarantorSame = g && (g.same_as_patient === true ||
    (Object.keys(g).length === 0 && !patient.guarantor));
  const guarantorName = [g.first_name, g.last_name].filter(Boolean).join(" ");

  return (
    <div data-testid="patient-overview" className="space-y-6">
      {canEdit && (
        <div className="flex items-center justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={onEdit}
            data-testid="overview-edit-patient-btn"
            className="rounded-sm"
          >
            <Pencil className="mr-2 h-3.5 w-3.5" /> Edit patient info
          </Button>
        </div>
      )}

      <OverviewSection
        title="Identity"
        hint="Legal name first; preferred name appears in the patient portal."
        testId="overview-identity"
      >
        <OverviewField label="First name" value={demo.first_name || patient.first_name} testId="ov-first-name" />
        <OverviewField label="Middle name" value={demo.middle_name} testId="ov-middle-name" />
        <OverviewField label="Last name" value={demo.last_name || patient.last_name} testId="ov-last-name" />
        <OverviewField label="Preferred name" value={demo.preferred_name} testId="ov-preferred-name" />
        <OverviewField
          label="Date of birth"
          value={demo.date_of_birth || patient.date_of_birth
            ? (patient.unmasked ? formatDate(demo.date_of_birth || patient.date_of_birth) : (demo.date_of_birth || patient.date_of_birth))
            : null}
          testId="ov-dob"
        />
        <OverviewField label="Sex at birth" value={demo.sex_at_birth} testId="ov-sex" />
        <OverviewField label="Gender identity" value={demo.gender || patient.gender} testId="ov-gender" />
        <OverviewField label="Pronouns" value={demo.pronouns} testId="ov-pronouns" />
        <OverviewField label="Marital status" value={demo.marital_status} testId="ov-marital" />
        <OverviewField label="Preferred language" value={demo.language} testId="ov-language" />
      </OverviewSection>

      <OverviewSection
        title="Contact"
        hint="Default contact details — patients may update these in the portal."
        testId="overview-contact"
      >
        <OverviewField label="Mobile phone" value={formatPhoneDisplay(contact.phone || patient.phone)} testId="ov-mobile" />
        <OverviewField label="Home phone" value={formatPhoneDisplay(contact.phone_alt)} testId="ov-home" />
        <OverviewField label="Work phone" value={formatPhoneDisplay(contact.phone_work)} testId="ov-work" />
        <OverviewField label="Email" value={contact.email || patient.email} testId="ov-email" />
        <OverviewField label="Preferred contact method" value={contact.preferred_contact_method} testId="ov-pcm" />
        <OverviewField label="Communication consents" value={consentSummary} testId="ov-comm-consents" />
      </OverviewSection>

      <OverviewSection title="Address" testId="overview-address">
        <OverviewField label="Address" value={addressLine} testId="ov-address" full />
      </OverviewSection>

      <OverviewSection
        title="Emergency contact"
        hint="Someone we can reach if the patient is unresponsive."
        testId="overview-emergency"
      >
        <OverviewField label="Name" value={ec.name || patient.emergency_contact} testId="ov-ec-name" />
        <OverviewField label="Relationship" value={ec.relationship} testId="ov-ec-rel" />
        <OverviewField label="Phone" value={formatPhoneDisplay(ec.phone)} testId="ov-ec-phone" />
        <OverviewField label="Alt phone" value={formatPhoneDisplay(ec.phone_alt)} testId="ov-ec-alt" />
        <OverviewField label="Email" value={ec.email} testId="ov-ec-email" />
      </OverviewSection>

      <OverviewSection
        title="Care assignment"
        hint="Who sees this patient and where."
        testId="overview-care"
      >
        <OverviewField label="Assigned provider" value={providerLabel} testId="ov-provider" />
        <OverviewField label="Preferred location" value={locationLabel} testId="ov-location" />
        <OverviewField label="Referral source" value={admin.referral_source} testId="ov-referral" />
      </OverviewSection>

      <OverviewSection title="Employment" testId="overview-employment">
        <OverviewField label="Occupation" value={demo.occupation} testId="ov-occupation" />
        <OverviewField label="Employer" value={demo.employer} testId="ov-employer" />
        <OverviewField label="Employer phone" value={formatPhoneDisplay(demo.employer_phone)} testId="ov-employer-phone" />
      </OverviewSection>

      <OverviewSection
        title="Responsible party / Guarantor"
        hint="Person financially responsible for this account."
        testId="overview-guarantor"
      >
        {guarantorSame ? (
          <OverviewField
            label="Status"
            value="Same as patient"
            testId="ov-guarantor-same"
            full
          />
        ) : (
          <>
            <OverviewField label="Name" value={guarantorName || null} testId="ov-g-name" />
            <OverviewField label="Relationship" value={g.relationship} testId="ov-g-rel" />
            <OverviewField label="Date of birth" value={g.date_of_birth} testId="ov-g-dob" />
            <OverviewField label="Phone" value={formatPhoneDisplay(g.phone)} testId="ov-g-phone" />
            <OverviewField label="Email" value={g.email} testId="ov-g-email" />
            <OverviewField label="Address" value={g.address} testId="ov-g-addr" />
            <OverviewField label="Employer" value={g.employer} testId="ov-g-employer" />
            <OverviewField label="Employer phone" value={formatPhoneDisplay(g.employer_phone)} testId="ov-g-employer-phone" />
          </>
        )}
      </OverviewSection>

      <section
        data-testid="overview-insurance-summary"
        className="rounded-sm border border-border bg-card p-6"
      >
        <header className="mb-4 border-b border-border pb-2 flex items-center justify-between">
          <div>
            <h3 className="font-display text-lg font-medium text-foreground">Insurance</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Summary only — full plan management lives on the Insurance tab.
            </p>
          </div>
        </header>
        {(!hasValue(primary.carrier) && !hasValue(secondary.carrier)) ? (
          <p className="text-sm text-muted-foreground">No insurance on file.</p>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <InsuranceRow
              label="Primary"
              carrier={primary.carrier}
              plan={primary.plan_name}
              memberId={primary.member_id}
              planType={primary.plan_type}
              testId="ov-insurance-primary"
            />
            <InsuranceRow
              label="Secondary"
              carrier={secondary.carrier}
              plan={secondary.plan_name}
              memberId={secondary.member_id}
              planType={secondary.plan_type}
              testId="ov-insurance-secondary"
            />
          </div>
        )}
      </section>

      {hasValue(patient.notes) && (
        <section
          data-testid="overview-intake-notes"
          className="rounded-sm border border-border bg-card p-6"
        >
          <header className="mb-3 border-b border-border pb-2">
            <h3 className="font-display text-lg font-medium text-foreground">Intake notes</h3>
          </header>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
            {patient.notes}
          </p>
        </section>
      )}
    </div>
  );
}

function ConsentLine({ label, consent, consentType, onDownloadPdf }) {
  if (!consent || !consent.accepted) return null;
  const meta = [consent.signature_name, consent.signed_at].filter(Boolean).join(" · ");
  return (
    <div
      className="flex items-start justify-between gap-4 text-sm"
      data-testid={consentType ? `consent-row-${consentType}` : undefined}
    >
      <span className="text-foreground">{label}</span>
      <div className="flex items-center gap-2">
        <span className="text-right text-xs text-muted-foreground">{meta || "Accepted"}</span>
        {onDownloadPdf && consentType && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onDownloadPdf(consentType)}
            data-testid={`consent-pdf-${consentType}`}
            className="h-6 px-2 text-[11px] font-semibold uppercase tracking-wider text-primary hover:bg-primary/10"
          >
            <Download className="mr-1 h-3 w-3" /> PDF
          </Button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// IntakeFormsTab — list of clinical intake forms for a patient. The backend
// currently stores a single intake blob on the patient record; the UI already
// treats it as a versioned list so switching to a multi-form backend (one row
// per encounter) later is a drop-in.
// ---------------------------------------------------------------------------

function IntakeFormRow({ form, onEdit, canEdit }) {
  const painAreas = Array.isArray(form.pain_locations) ? form.pain_locations : [];
  const symptoms = Array.isArray(form.symptoms) ? form.symptoms : [];
  const caseLabel = form.case_type ? form.case_type.replace(/_/g, " ") : null;
  const isDraft = form.version_label?.startsWith("Draft");
  return (
    <li
      data-testid={`intake-form-${form.id}`}
      className="relative rounded-sm border border-border bg-card p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-sm bg-muted px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {form.version_label}
            </span>
            {caseLabel && (
              <span className="rounded-sm bg-primary/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-primary">
                {caseLabel}
              </span>
            )}
            {typeof form.pain_level === "number" && (
              <span className="rounded-sm bg-warning-soft px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-warning">
                Pain {form.pain_level}/10
              </span>
            )}
          </div>
          <h3 className="mt-2 font-display text-lg font-medium text-foreground">
            {form.chief_complaint || "No chief complaint recorded"}
          </h3>
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="text-right text-xs text-muted-foreground">
            {form.captured_at ? formatDateTime(form.captured_at) : "Date unknown"}
          </div>
          {canEdit && isDraft && onEdit && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onEdit(form)}
              data-testid={`intake-form-edit-${form.id}`}
              className="rounded-sm"
            >
              <Pencil className="mr-1 h-3 w-3" /> Edit draft
            </Button>
          )}
        </div>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 text-sm md:grid-cols-3">
        <div>
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">Onset</div>
          <div className="text-foreground">
            {form.complaint_onset
              ? formatDate(form.complaint_onset)
              : form.onset_type || <span className="text-muted-foreground">—</span>}
          </div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">Pain areas</div>
          <div className="text-foreground">
            {painAreas.length > 0
              ? painAreas.slice(0, 4).join(", ") + (painAreas.length > 4 ? "…" : "")
              : <span className="text-muted-foreground">—</span>}
          </div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">Symptoms</div>
          <div className="text-foreground">
            {symptoms.length > 0
              ? `${symptoms.length} reported`
              : <span className="text-muted-foreground">—</span>}
          </div>
        </div>
      </div>

      {form.notes && (
        <p className="mt-3 whitespace-pre-wrap text-sm text-muted-foreground">
          {form.notes}
        </p>
      )}
    </li>
  );
}

function IntakeFormsTab({ patientId, patient, range, onNew, onEdit, onRangeChange, canEdit, refreshKey }) {
  const [forms, setForms] = useState(null);
  const [creating, setCreating] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const { data } = await api.get(`/patients/${patientId}/intake-forms`);
      setForms(data || []);
    } catch (err) {
      setForms([]);
      toast.error(formatApiError(err));
    }
  }, [patientId]);

  useEffect(() => { refresh(); }, [refresh, refreshKey]);

  async function handleNew() {
    setCreating(true);
    try {
      // Seed the new draft from the patient's most-recent intake so the
      // wizard opens pre-filled with what we already know.
      const { data } = await api.post(
        `/patients/${patientId}/intake-forms`,
        { seed_from_patient: true },
      );
      toast.success(`Intake draft v${data.version} created`);
      await refresh();
      // Let parent decide whether to open the wizard for further edits.
      onNew?.(data);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally { setCreating(false); }
  }

  const rows = (forms || []).map((f) => ({
    id: f.id,
    version_label:
      f.status === "draft" ? `Draft · v${f.version}` : `v${f.version}`,
    captured_at: f.captured_at || f.updated_at || f.created_at,
    chief_complaint: f.clinical_intake?.chief_complaint,
    complaint_onset: f.clinical_intake?.complaint_onset,
    onset_type: f.clinical_intake?.onset_type,
    pain_level: typeof f.clinical_intake?.pain_level === "number"
      ? f.clinical_intake.pain_level : null,
    pain_locations: f.clinical_intake?.pain_locations,
    symptoms: f.clinical_intake?.symptoms,
    notes: f.notes || f.clinical_intake?.notes,
    case_type: f.case_details?.case_type,
    // Keep the original form object around so parent can open the wizard
    // seeded with its latest clinical + case data.
    _raw: f,
  }));

  const filteredForms = rows.filter(
    (f) => !f.captured_at || isInRange(f.captured_at, range),
  );

  return (
    <section data-testid="patient-intake-forms" className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Clinical intake
          </span>
          <h2 className="mt-1 font-display text-2xl font-medium tracking-tight">
            Intake forms
          </h2>
          <p className="mt-1 max-w-xl text-xs text-muted-foreground">
            Every intake captured for this patient — chief complaint, pain, symptoms,
            and case details. Each save creates a new versioned draft.
          </p>
        </div>
        {canEdit && (
          <Button
            onClick={handleNew}
            disabled={creating}
            data-testid="intake-new-form-btn"
            className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
          >
            <Plus className="mr-2 h-4 w-4" />
            {creating ? "Creating…" : "New intake form"}
          </Button>
        )}
      </div>

      <DateRangeFilter
        testId="intake-date-range"
        onChange={onRangeChange}
      />

      {!patient.unmasked && (
        <p className="text-xs text-muted-foreground">
          Clinical details are hidden by default — unmask above to reveal the structured intake data.
        </p>
      )}

      {forms === null ? (
        <div className="rounded-sm border border-dashed border-border bg-card p-12 text-center text-sm text-muted-foreground">
          Loading intake forms…
        </div>
      ) : filteredForms.length === 0 ? (
        <div
          data-testid="intake-forms-empty"
          className="rounded-sm border border-dashed border-border bg-card p-12 text-center text-sm text-muted-foreground"
        >
          {rows.length === 0
            ? "No intake forms captured yet."
            : "No intake forms in the selected range."}
        </div>
      ) : (
        <ul className="space-y-3">
          {filteredForms.map((f) => (
            <IntakeFormRow
              key={f.id}
              form={f}
              canEdit={canEdit}
              onEdit={() => onEdit?.(f._raw)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}


function IntakeSections({ patient, onDownloadConsent }) {
  const demo = patient.demographics || {};
  const contact = patient.contact || {};
  const addr = patient.address_details || {};
  const ec = patient.emergency_contact_details || {};
  const admin = patient.admin || {};
  const guarantor = patient.guarantor || null;
  const insurance = patient.insurance || null;
  const clinical = patient.clinical_intake || {};
  const caseDetails = patient.case_details || {};
  const consents = patient.consents || {};

  // Pre-compute whether the whole section would be empty. Works for both
  // legacy records (grouped sections absent → every card returns null) and
  // masked responses (backend strips grouped sections outright → same).
  const anyGrouped =
    hasValue(patient.demographics) ||
    hasValue(patient.contact) ||
    hasValue(patient.address_details) ||
    hasValue(patient.emergency_contact_details) ||
    hasValue(patient.admin) ||
    hasValue(guarantor) ||
    hasValue(insurance) ||
    hasValue(clinical) ||
    hasValue(caseDetails) ||
    hasValue(consents);

  if (!anyGrouped) {
    return (
      <section aria-hidden data-testid="patient-intake-empty" className="hidden" />
    );
  }

  return (
    <section data-testid="patient-intake-sections" className="space-y-6">
      <div>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Expanded intake
        </span>
        <h2 className="mt-1 font-display text-2xl font-medium tracking-tight">
          Intake sections
        </h2>
        {!patient.unmasked && (
          <p className="mt-1 text-xs text-muted-foreground">
            Sections below are hidden by default — unmask above to reveal the structured intake data.
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {hasValue(demo) && (
          <IntakeCard title="Demographics" testId="intake-demographics">
            <Row label="Legal first name">{demo.first_name}</Row>
            <Row label="Legal last name">{demo.last_name}</Row>
            <Row label="Date of birth">{demo.date_of_birth}</Row>
            <Row label="Preferred name" testId="demo-preferred-name">{demo.preferred_name}</Row>
            <Row label="Middle name" testId="demo-middle-name">{demo.middle_name}</Row>
            <Row label="Sex at birth">{demo.sex_at_birth}</Row>
            <Row label="Gender identity">{demo.gender || patient.gender}</Row>
            <Row label="Pronouns">{demo.pronouns}</Row>
            <Row label="Marital status">{demo.marital_status}</Row>
            <Row label="Language">{demo.language}</Row>
            <Row label="Occupation">{demo.occupation}</Row>
            <Row label="Employer">{demo.employer}</Row>
            <Row label="Employer phone">{formatPhoneDisplay(demo.employer_phone)}</Row>
            <Row label="SSN (last 4)">{demo.ssn_last4 ? `•••• ${demo.ssn_last4}` : null}</Row>
          </IntakeCard>
        )}

        {hasValue(contact) && (
          <IntakeCard title="Contact" testId="intake-contact">
            <Row label="Mobile phone">{formatPhoneDisplay(contact.phone || patient.phone)}</Row>
            <Row label="Home phone">{formatPhoneDisplay(contact.phone_alt)}</Row>
            <Row label="Work phone">{formatPhoneDisplay(contact.phone_work)}</Row>
            <Row label="Email">{contact.email || patient.email}</Row>
            <Row label="Preferred method">{contact.preferred_contact_method}</Row>
            <Row label="SMS consent">
              {contact.sms_consent === true ? "Yes" : contact.sms_consent === false ? "No" : null}
            </Row>
            <Row label="Email consent">
              {contact.email_consent === true ? "Yes" : contact.email_consent === false ? "No" : null}
            </Row>
            <Row label="OK to leave voicemail">
              {contact.voicemail_consent === true ? "Yes" : contact.voicemail_consent === false ? "No" : null}
            </Row>
          </IntakeCard>
        )}

        {hasValue(addr) && (
          <IntakeCard title="Address" hint="Structured shipping / billing address on file." testId="intake-address">
            <Row label="Street">
              {[addr.line1, addr.line2].filter(Boolean).join(", ") || patient.address}
            </Row>
            <Row label="City">{addr.city}</Row>
            <Row label="State">{addr.state}</Row>
            <Row label="Postal code">{addr.postal_code}</Row>
            <Row label="Country">{addr.country}</Row>
          </IntakeCard>
        )}

        {(hasValue(ec) || hasValue(patient.emergency_contact)) && (
          <IntakeCard title="Emergency contact" testId="intake-emergency-contact">
            <Row label="Name">{ec.name}</Row>
            <Row label="Relationship">{ec.relationship}</Row>
            <Row label="Primary phone">{formatPhoneDisplay(ec.phone)}</Row>
            <Row label="Alternate phone">{formatPhoneDisplay(ec.phone_alt)}</Row>
            <Row label="Email">{ec.email}</Row>
            <Row label="Address">{ec.address}</Row>
            {/* Legacy fallback: when structured details are absent but the scalar is, show it. */}
            {!hasValue(ec) && hasValue(patient.emergency_contact) && (
              <Row label="Contact" testId="ec-legacy-scalar">{patient.emergency_contact}</Row>
            )}
          </IntakeCard>
        )}

        {hasValue(admin) && (
          <IntakeCard title="Administrative" hint="Provider assignment & intake metadata." testId="intake-admin">
            <Row label="Primary provider">{admin.primary_provider_id}</Row>
            <Row label="Referred by">{admin.referred_by}</Row>
            <Row label="Referral source">{admin.referral_source}</Row>
            <Row label="MRN">{admin.mrn}</Row>
            <Row label="Tags">{admin.tags}</Row>
            <Row label="Internal flags">{admin.internal_flags}</Row>
          </IntakeCard>
        )}

        {guarantor && !(guarantor.same_as_patient === true && Object.keys(guarantor).length === 1) && (
          <IntakeCard
            title="Responsible party / Guarantor"
            hint={guarantor.same_as_patient ? "Same as patient" : "Billing responsibility"}
            testId="intake-guarantor"
          >
            {guarantor.same_as_patient ? (
              <Row label="Same as patient">Yes — no separate guarantor on file.</Row>
            ) : (
              <>
                <Row label="Name">
                  {[guarantor.first_name, guarantor.last_name].filter(Boolean).join(" ")}
                </Row>
                <Row label="Relationship">{guarantor.relationship}</Row>
                <Row label="Date of birth">{guarantor.date_of_birth}</Row>
                <Row label="Phone">{formatPhoneDisplay(guarantor.phone)}</Row>
                <Row label="Email">{guarantor.email}</Row>
                <Row label="Address">{guarantor.address}</Row>
                <Row label="Employer">{guarantor.employer}</Row>
                <Row label="Employer phone">{formatPhoneDisplay(guarantor.employer_phone)}</Row>
              </>
            )}
          </IntakeCard>
        )}

        {hasValue(insurance) && (
          <IntakeCard title="Insurance" testId="intake-insurance">
            <InsurancePlanBlock plan={insurance.primary} label="Primary" testId="insurance-primary" />
            <InsurancePlanBlock plan={insurance.secondary} label="Secondary" testId="insurance-secondary" />
            <InsurancePlanBlock plan={insurance.tertiary} label="Tertiary" testId="insurance-tertiary" />
          </IntakeCard>
        )}

        {hasValue(clinical) && (
          <IntakeCard
            title="Clinical intake"
            hint="Self-reported at intake; provider visits are on the medical records timeline below."
            testId="intake-clinical"
          >
            <Row label="Chief complaint">{clinical.chief_complaint}</Row>
            <Row label="Symptom start">{clinical.complaint_onset}</Row>
            <Row label="Onset type">{clinical.onset_type}</Row>
            <Row label="Pain score">
              {typeof clinical.pain_level === "number" ? `${clinical.pain_level}/10` : null}
            </Row>
            <Row label="Pain areas">{clinical.pain_locations}</Row>
            <Row label="Symptoms">{clinical.symptoms}</Row>
            <Row label="Aggravating factors">{clinical.aggravating_factors}</Row>
            <Row label="Relieving factors">{clinical.relieving_factors}</Row>
            <Row label="Prior treatments">{clinical.prior_treatments}</Row>
            <Row label="Medications">{clinical.medications}</Row>
            <Row label="Allergies">{clinical.allergies}</Row>
            <Row label="Past medical">{clinical.past_medical_history}</Row>
            <Row label="Past surgical">{clinical.past_surgical_history}</Row>
            <Row label="Family history">{clinical.family_history}</Row>
            <Row label="Social history">{clinical.social_history}</Row>
            <Row label="Provider notes">{clinical.notes}</Row>
          </IntakeCard>
        )}

        {hasValue(caseDetails) && (
          <IntakeCard
            title="Case details"
            hint={caseDetails.case_type ? `Type: ${caseDetails.case_type.replace(/_/g, " ")}` : null}
            testId="intake-case"
          >
            <Row label="Date of injury">{caseDetails.date_of_injury}</Row>
            <Row label="Injury description">{caseDetails.injury_description}</Row>
            <Row label="Accident location">{caseDetails.accident_location}</Row>
            <Row label="Police report #">{caseDetails.police_report_number}</Row>
            <Row label="Auto carrier">{caseDetails.auto_carrier}</Row>
            <Row label="Claim #">{caseDetails.claim_number}</Row>
            <Row label="Adjuster name">{caseDetails.adjuster_name}</Row>
            <Row label="Adjuster phone">{formatPhoneDisplay(caseDetails.adjuster_phone)}</Row>
            <Row label="Attorney name">{caseDetails.attorney_name}</Row>
            <Row label="Attorney phone">{formatPhoneDisplay(caseDetails.attorney_phone)}</Row>
            <Row label="Attorney email">{caseDetails.attorney_email}</Row>
            <Row label="Employer for claim">{caseDetails.employer_for_claim}</Row>
            <Row label="Workers' comp carrier">{caseDetails.work_comp_carrier}</Row>
            <Row label="Return-to-work status">{caseDetails.return_to_work_status}</Row>
            <Row label="Notes">{caseDetails.notes}</Row>
          </IntakeCard>
        )}

        {hasValue(consents) && (
          <IntakeCard
            title="Consents"
            hint="Every consent is versioned and audited."
            testId="intake-consents"
          >
            <ConsentLine label="HIPAA privacy notice" consent={consents.hipaa} consentType="hipaa" onDownloadPdf={onDownloadConsent} />
            <ConsentLine label="Consent to treatment" consent={consents.treatment} consentType="treatment" onDownloadPdf={onDownloadConsent} />
            <ConsentLine label="Financial policy" consent={consents.financial} consentType="financial" onDownloadPdf={onDownloadConsent} />
            <ConsentLine label="Telehealth" consent={consents.telehealth} consentType="telehealth" onDownloadPdf={onDownloadConsent} />
            <ConsentLine label="Photo release" consent={consents.photo_release} consentType="photo_release" onDownloadPdf={onDownloadConsent} />
            {Array.isArray(consents.additional) &&
              consents.additional.map((c, i) => (
                <ConsentLine
                  key={`${c?.type || "extra"}-${i}`}
                  label={(c?.type || "Consent").replace(/_/g, " ")}
                  consent={c}
                  consentType={c?.type}
                  onDownloadPdf={onDownloadConsent}
                />
              ))}
          </IntakeCard>
        )}
      </div>
    </section>
  );
}

function RecordDialog({ open, onClose, patientId, onAdded, onReauthNeeded }) {
  const [form, setForm] = useState({
    record_type: "assessment", title: "", description: "", diagnosis: "", treatment: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const update = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const payload = {
        record_type: form.record_type,
        title: form.title,
        ...Object.fromEntries(
          Object.entries(form).filter(([, v]) => v && v.toString().trim() !== "")
        ),
      };
      const { data } = await api.post(`/patients/${patientId}/records`, payload);
      toast.success("Medical record added");
      onAdded(data);
      onClose();
      setForm({ record_type: "assessment", title: "", description: "", diagnosis: "", treatment: "" });
    } catch (err) {
      if (err?.response?.status === 401 && /re-auth/i.test(err.response?.data?.detail || "")) {
        onReauthNeeded();
      } else {
        toast.error(formatApiError(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="record-create-dialog" className="max-w-lg rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Add medical record</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1"><Label>Type</Label>
            <Select value={form.record_type} onValueChange={(v) => setForm({ ...form, record_type: v })}>
              <SelectTrigger data-testid="record-type" className="rounded-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="assessment">Assessment</SelectItem>
                <SelectItem value="treatment">Treatment</SelectItem>
                <SelectItem value="note">Note</SelectItem>
                <SelectItem value="diagnosis">Diagnosis</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1"><Label>Title</Label>
            <Input required data-testid="record-title" value={form.title} onChange={update("title")} /></div>
          <div className="space-y-1"><Label>Description</Label>
            <Textarea data-testid="record-description" value={form.description} onChange={update("description")} rows={3} /></div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1"><Label>Diagnosis</Label>
              <Input data-testid="record-diagnosis" value={form.diagnosis} onChange={update("diagnosis")} /></div>
            <div className="space-y-1"><Label>Treatment</Label>
              <Input data-testid="record-treatment" value={form.treatment} onChange={update("treatment")} /></div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} className="rounded-sm">Cancel</Button>
            <Button type="submit" disabled={submitting} data-testid="record-submit-btn"
              className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]">
              {submitting ? "Saving…" : "Add record"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function PatientDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState(() => searchParams.get("tab") || "overview");
  const [clinicalRedesignOn] = useFeatureFlag("clinicalRedesign");

  // Fire legacy-layout activation once whenever this patient's Clinical tab
  // resolves to the v1 experience. v2 activation is tracked inside
  // ClinicalTabV2 itself so the two events stay symmetric.
  useEffect(() => {
    if (tab === "clinical" && !clinicalRedesignOn) {
      trackUiEvent("clinical.layout.activated", { layout: "v1" });
    }
  }, [tab, clinicalRedesignOn, id]);
  useEffect(() => {
    const urlTab = searchParams.get("tab");
    if (urlTab && urlTab !== tab) setTab(urlTab);
  }, [searchParams, tab]);
  const canAddRecord = user.role === "admin" || user.role === "doctor";
  const canDelete = user.role === "admin";
  const canEditIntake = ["admin", "doctor", "staff"].includes(user.role);
  const reasonRequired = user.role === "doctor" || user.role === "staff";
  const canUnmask = user.role === "admin" || (user.role === "doctor" || user.role === "staff");
  const canExport = user.role === "admin" || user.role === "patient";

  const [patient, setPatient] = useState(null);
  const [records, setRecords] = useState(null);
  const [appointments, setAppointments] = useState(null);
  const [recDialog, setRecDialog] = useState(false);
  const [breakGlass, setBreakGlass] = useState(reasonRequired); // show on mount if needed
  const [reason, setReason] = useState(null);
  const [unmask, setUnmask] = useState(false);
  const [reauthOpen, setReauthOpen] = useState(false);
  const [reauthIntent, setReauthIntent] = useState(null); // 'record' | 'delete'
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleteReason, setDeleteReason] = useState("");
  const [editWizardOpen, setEditWizardOpen] = useState(false);
  const [intakeWizardOpen, setIntakeWizardOpen] = useState(false);
  const [editingIntakeForm, setEditingIntakeForm] = useState(null);
  const [intakeRefreshKey, setIntakeRefreshKey] = useState(0);
  const [chargeRecord, setChargeRecord] = useState(null);
  const { providers } = useProviders();
  const [locations, setLocations] = useState([]);
  useEffect(() => {
    let cancelled = false;
    api.get("/authz/locations")
      .then((r) => { if (!cancelled) setLocations(r.data || []); })
      .catch(() => { if (!cancelled) setLocations([]); });
    return () => { cancelled = true; };
  }, []);
  const [recordsRange, setRecordsRange] = useState(null);
  const [appointmentsRange, setAppointmentsRange] = useState(null);
  const [intakeRange, setIntakeRange] = useState(null);

  const filteredRecords = useMemo(
    () => (records || []).filter((r) => isInRange(r.recorded_at, recordsRange)),
    [records, recordsRange],
  );
  const filteredAppointments = useMemo(
    () => (appointments || []).filter((a) => isInRange(a.start_time, appointmentsRange)),
    [appointments, appointmentsRange],
  );

  const load = useCallback(
    async ({ withUnmask = false, breakGlassReason = null } = {}) => {
      try {
        const params = {};
        if (breakGlassReason) params.reason = breakGlassReason;
        if (withUnmask) params.unmask = true;
        const [pRes, rRes, aRes] = await Promise.all([
          api.get(`/patients/${id}`, { params }),
          api.get(`/patients/${id}/records`),
          api.get("/appointments", { params: { patient_id: id } }),
        ]);
        setPatient(pRes.data);
        setRecords(rRes.data);
        setAppointments(aRes.data);
      } catch (err) {
        toast.error(formatApiError(err));
      }
    },
    [id]
  );

  useEffect(() => {
    if (!reasonRequired) {
      load();
    }
    // If reasonRequired, wait for break-glass submit.
  }, [load, reasonRequired]);

  async function exportPatient() {
    try {
      const { data } = await api.get(`/patients/${id}/export`);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `patient-${id}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("Export downloaded");
    } catch (err) {
      toast.error(formatApiError(err));
    }
  }

  async function downloadConsentPdf(consentType) {
    try {
      const params = {};
      if (reason) params.reason = reason;
      const resp = await api.get(
        `/patients/${id}/consents/${consentType}/pdf`,
        { params, responseType: "blob" }
      );
      const url = URL.createObjectURL(resp.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `consent-${consentType}-${id.slice(0, 8)}.pdf`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
      toast.success("Signed consent PDF downloaded");
    } catch (err) {
      toast.error(formatApiError(err));
    }
  }

  async function softDelete() {
    try {
      const { data } = await api.delete(`/patients/${id}`, {
        params: { reason: deleteReason },
      });
      toast.success(`Patient soft-deleted. Retained until ${new Date(data.retention_until).toLocaleDateString()}.`);
      setDeleteConfirm(false);
      navigate("/patients");
    } catch (err) {
      if (err?.response?.status === 401) {
        setReauthIntent("delete");
        setReauthOpen(true);
      } else {
        toast.error(formatApiError(err));
      }
    }
  }

  async function openEditPatientWizard() {
    if (patient.unmasked) {
      setEditWizardOpen(true);
      return;
    }
    if (canUnmask) {
      try {
        setUnmask(true);
        await load({ withUnmask: true, breakGlassReason: reason });
        setEditWizardOpen(true);
      } catch (err) {
        setUnmask(false);
        toast.error(formatApiError(err));
      }
      return;
    }
    toast.message("Break-glass required to edit patient", {
      description: "Enter a reason above to unmask this record before editing.",
    });
  }

  async function openIntakeWizard(intakeForm) {
    if (!intakeForm) {
      toast.error("No intake form selected.");
      return;
    }
    if (patient.unmasked) {
      setEditingIntakeForm(intakeForm);
      setIntakeWizardOpen(true);
      return;
    }
    if (canUnmask) {
      try {
        setUnmask(true);
        await load({ withUnmask: true, breakGlassReason: reason });
        setEditingIntakeForm(intakeForm);
        setIntakeWizardOpen(true);
      } catch (err) {
        setUnmask(false);
        toast.error(formatApiError(err));
      }
      return;
    }
    toast.message("Break-glass required to edit intake", {
      description: "Enter a reason above to unmask this record before editing.",
    });
  }

  if (reasonRequired && !reason) {
    return (
      <BreakGlassDialog
        open={breakGlass}
        onClose={() => navigate("/patients")}
        onSubmit={(r) => {
          setReason(r);
          setBreakGlass(false);
          load({ breakGlassReason: r });
        }}
        title="Open patient record"
        description="Your role requires a clinical reason to view this patient's record. This will be recorded in the audit log."
      />
    );
  }

  if (!patient) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-32" />
      </div>
    );
  }

  const hasMoreActions = canUnmask || canExport || canDelete;
  return (
    <div data-testid="patient-detail-page" className="space-y-10 animate-in fade-in duration-300">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" asChild className="text-primary">
          <Link to="/patients" data-testid="patient-back-link">
            <ArrowLeft className="mr-2 h-4 w-4" /> All patients
          </Link>
        </Button>
        {hasMoreActions && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                data-testid="patient-more-actions-trigger"
                className="rounded-full"
              >
                <MoreHorizontal className="mr-2 h-4 w-4" aria-hidden="true" />
                More actions
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-[260px]">
              <DropdownMenuLabel>Patient record</DropdownMenuLabel>
              {canUnmask && (
                <DropdownMenuItem
                  data-testid="patient-menu-toggle-unmask"
                  onSelect={() => {
                    const next = !unmask;
                    setUnmask(next);
                    load({ withUnmask: next, breakGlassReason: reason });
                  }}
                >
                  {unmask ? (
                    <EyeOff className="mr-2 h-4 w-4" aria-hidden="true" />
                  ) : (
                    <Eye className="mr-2 h-4 w-4" aria-hidden="true" />
                  )}
                  {unmask ? "Hide protected information" : "Reveal protected information"}
                </DropdownMenuItem>
              )}
              {canExport && (
                <DropdownMenuItem
                  data-testid="patient-menu-export"
                  onSelect={exportPatient}
                >
                  <Download className="mr-2 h-4 w-4" aria-hidden="true" />
                  Export patient data
                </DropdownMenuItem>
              )}
              {canDelete && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    data-testid="patient-menu-archive"
                    onSelect={() => setDeleteConfirm(true)}
                    className="text-destructive focus:bg-destructive-soft focus:text-destructive"
                  >
                    <Archive className="mr-2 h-4 w-4" aria-hidden="true" />
                    Archive patient
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      <header className="flex flex-wrap items-start justify-between gap-6">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Patient profile {patient.unmasked ? "" : "· masked"}
          </span>
          <h1 className="mt-2 font-display text-4xl font-medium tracking-tight text-foreground">
            {patient.unmasked
              ? `${patient.first_name} ${patient.last_name}`
              : patient.display_name_masked || "—"}
          </h1>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted-foreground">
            {patient.date_of_birth && <span>DOB {patient.unmasked ? formatDate(patient.date_of_birth) : patient.date_of_birth}</span>}
            {patient.phone && <span>{formatPhoneDisplay(patient.phone)}</span>}
            {patient.email && <span>{patient.email}</span>}
            {patient.gender && <span>{patient.gender}</span>}
            {patient.status === "deleted" && (
              <span className="rounded-sm bg-destructive-soft px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-destructive">
                Deleted · retained until {patient.retention_until ? formatDate(patient.retention_until) : "—"}
              </span>
            )}
          </div>
        </div>
      </header>

      <Tabs
        value={tab}
        onValueChange={(v) => {
          setTab(v);
          // keep URL in sync so deep-links (e.g. ?tab=clinical) survive
          const next = new URLSearchParams(searchParams);
          if (v === "overview") next.delete("tab");
          else next.set("tab", v);
          setSearchParams(next, { replace: true });
        }}
        data-testid="patient-detail-tabs"
      >
        <TabsList
          data-testid="patient-detail-tablist"
          className="flex h-auto w-full flex-wrap justify-start gap-1 rounded-sm bg-muted/60 p-1"
        >
          <TabsTrigger value="overview" data-testid="tab-overview" className="rounded-sm">Overview</TabsTrigger>
          <TabsTrigger value="intake" data-testid="tab-intake" className="rounded-sm">Intake</TabsTrigger>
          <TabsTrigger value="clinical" data-testid="tab-clinical" className="rounded-sm">Clinical</TabsTrigger>
          <TabsTrigger value="documents" data-testid="tab-documents" className="rounded-sm">Documents &amp; Attachments</TabsTrigger>
          <TabsTrigger value="records" data-testid="tab-records" className="rounded-sm">Medical Records</TabsTrigger>
          <TabsTrigger value="appointments" data-testid="tab-appointments" className="rounded-sm">Appointments</TabsTrigger>
          <TabsTrigger value="insurance" data-testid="tab-insurance" className="rounded-sm">Insurance</TabsTrigger>
          <TabsTrigger value="billing" data-testid="tab-billing" className="rounded-sm">Billing &amp; Ledger</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-6">
          <PatientOverview
            patient={patient}
            providers={providers}
            locations={locations}
            canEdit={canEditIntake}
            onEdit={openEditPatientWizard}
          />
        </TabsContent>

        <TabsContent value="intake" className="mt-6 space-y-6">
          <IntakeFormsTab
            patientId={id}
            patient={patient}
            range={intakeRange}
            onRangeChange={setIntakeRange}
            canEdit={canEditIntake}
            onNew={openIntakeWizard}
            onEdit={openIntakeWizard}
            refreshKey={intakeRefreshKey}
          />
        </TabsContent>

        <TabsContent value="documents" className="mt-6 space-y-6">
          <IntakeSections patient={patient} onDownloadConsent={downloadConsentPdf} />
          <PatientDocumentsCard patientId={id} canEdit={canEditIntake} />
        </TabsContent>

        <TabsContent value="clinical" className="mt-6">
          {clinicalRedesignOn ? (
            <ClinicalTabV2
              patientId={id}
              patient={patient}
              appointments={appointments}
              providers={providers}
              canWrite={canAddRecord}
              currentUser={user}
              onReauthNeeded={() => {
                setReauthIntent("clinical");
                setReauthOpen(true);
              }}
            />
          ) : (
            <ClinicalTab
              patientId={id}
              providers={providers}
              canWrite={canAddRecord}
              currentUser={user}
              onReauthNeeded={() => {
                setReauthIntent("clinical");
                setReauthOpen(true);
              }}
            />
          )}
        </TabsContent>

        <TabsContent value="records" className="mt-6">
          <section>
            <div className="mb-4 flex items-end justify-between gap-4">
              <div>
                <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">Clinical history</span>
                <h2 className="mt-1 font-display text-2xl font-medium tracking-tight">Medical records</h2>
              </div>
              {canAddRecord && (
                <Button
                  onClick={() => setRecDialog(true)}
                  data-testid="record-new-btn"
                  className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
                >
                  <Plus className="mr-2 h-4 w-4" /> Add record
                </Button>
              )}
            </div>

            <DateRangeFilter
              testId="records-date-range"
              onChange={setRecordsRange}
              className="mb-4"
            />

            {records === null ? (
              <Skeleton className="h-32" />
            ) : filteredRecords.length === 0 ? (
              <div className="rounded-sm border border-dashed border-border bg-card p-12 text-center text-sm text-muted-foreground">
                {records.length === 0
                  ? "No medical records yet."
                  : "No medical records in the selected range."}
              </div>
            ) : (
              <ol className="relative space-y-4 border-l border-border pl-6">
                {filteredRecords.map((r) => (
                  <li key={r.id} data-testid={`record-${r.id}`} className="relative rounded-sm border border-border bg-card p-5">
                    <span className="absolute -left-[33px] top-5 flex h-5 w-5 items-center justify-center rounded-sm bg-primary/10 text-primary">
                      <FileText className="h-3 w-3" />
                    </span>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <span className="rounded-sm bg-muted px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                          {r.record_type}
                        </span>
                        {r.signed_at && (
                          <span className="ml-2 rounded-sm bg-success-soft px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-success">
                            Signed
                          </span>
                        )}
                        {r.charge_status === "captured" && (
                          <span className="ml-2 rounded-sm bg-primary/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-primary">
                            Charges captured
                          </span>
                        )}
                        <h3 className="mt-2 font-display text-lg font-medium text-foreground">{r.title}</h3>
                      </div>
                      <div className="flex flex-col items-end gap-2">
                        <div className="text-xs text-muted-foreground">
                          {formatDateTime(r.recorded_at)} · {r.recorded_by_name || "—"}
                        </div>
                        <Button
                          size="sm" variant="outline"
                          onClick={() => setChargeRecord(r)}
                          data-testid={`record-charge-capture-${r.id}`}
                        >
                          {r.charge_status === "captured" ? "View captured" : "Code & capture"}
                        </Button>
                      </div>
                    </div>
                    {r.description && <p className="mt-3 text-sm leading-relaxed text-foreground">{r.description}</p>}
                    <div className="mt-3 grid grid-cols-2 gap-4 text-sm">
                      {r.diagnosis && (
                        <div><span className="text-[11px] uppercase tracking-wider text-muted-foreground">Diagnosis</span>
                          <div className="text-foreground">{r.diagnosis}</div></div>
                      )}
                      {r.treatment && (
                        <div><span className="text-[11px] uppercase tracking-wider text-muted-foreground">Treatment</span>
                          <div className="text-foreground">{r.treatment}</div></div>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </TabsContent>

        <TabsContent value="appointments" className="mt-6">
          <section>
            <div className="mb-4">
              <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">Scheduling</span>
              <h2 className="mt-1 font-display text-2xl font-medium tracking-tight">Appointments</h2>
            </div>
            <DateRangeFilter
              testId="appointments-date-range"
              onChange={setAppointmentsRange}
              className="mb-4"
            />
            {appointments === null ? (
              <Skeleton className="h-24" />
            ) : filteredAppointments.length === 0 ? (
              <div className="rounded-sm border border-dashed border-border bg-card p-10 text-center text-sm text-muted-foreground">
                {appointments.length === 0
                  ? "No appointments for this patient."
                  : "No appointments in the selected range."}
              </div>
            ) : (
              <ul className="space-y-2" data-testid="patient-appointments-list">
                {filteredAppointments.map((a) => (
                  <li
                    key={a.id}
                    data-testid={`patient-appt-${a.id}`}
                    className="flex items-center justify-between gap-3 rounded-sm border border-border bg-card px-5 py-4 text-sm"
                  >
                    <div className="min-w-0">
                      <div className="font-medium text-foreground">{formatDateTime(a.start_time)}</div>
                      <div className="text-xs text-muted-foreground">
                        with {a.provider_name} · {relativeFromNow(a.start_time)}
                      </div>
                      {a.intake_status && (
                        <div
                          data-testid={`patient-appt-intake-${a.id}`}
                          className="mt-1 text-[11px] uppercase tracking-wider text-muted-foreground"
                        >
                          Intake: <span className={
                            a.intake_status === "completed"
                              ? "text-emerald-700 dark:text-emerald-300"
                              : a.intake_status === "in_progress"
                                ? "text-amber-700 dark:text-amber-300"
                                : "text-muted-foreground"
                          }>{a.intake_status.replace("_", " ")}</span>
                          {a.checked_in_at && (
                            <> · checked in {formatDateTime(a.checked_in_at)}</>
                          )}
                        </div>
                      )}
                    </div>
                    <span
                      data-testid={`patient-appt-status-${a.id}`}
                      className={`shrink-0 rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
                        a.status === "cancelled" || a.status === "canceled" || a.status === "no_show"
                          ? "bg-destructive-soft text-destructive"
                        : a.status === "completed" || a.status === "checked_out"
                          ? "bg-muted text-muted-foreground"
                        : "bg-primary/10 text-primary"}`}>
                      {String(a.status || "").replace(/_/g, " ")}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </TabsContent>

        <TabsContent value="insurance" className="mt-6">
          <div className="space-y-6">
            <PatientEligibilityCard patientId={id} />
            <PatientInsuranceManager patientId={id} />
          </div>
        </TabsContent>

        <TabsContent value="billing" className="mt-6">
          <div className="space-y-6">
            <PatientLedgerCard patientId={id} title="Activity" />
            <PatientStatementsCard patientId={id} />
            <PatientQuestionnairesCard patientId={id} />
            <ChartBriefCard patientId={id} />
            <PatientSemanticSearch patientId={id} />
          </div>
        </TabsContent>
      </Tabs>

      {canAddRecord && (
        <RecordDialog
          open={recDialog}
          onClose={() => setRecDialog(false)}
          patientId={id}
          onAdded={(rec) => setRecords((xs) => [rec, ...(xs || [])])}
          onReauthNeeded={() => {
            setRecDialog(false);
            setReauthIntent("record");
            setReauthOpen(true);
          }}
        />
      )}

      <ChargeCaptureDialog
        open={!!chargeRecord}
        onOpenChange={(v) => !v && setChargeRecord(null)}
        record={chargeRecord}
        patientId={id}
        onUpdated={() => load({ reason, unmask })}
      />

      <ReauthDialog
        open={reauthOpen}
        title={reauthIntent === "delete" ? "Confirm patient deletion" : "Confirm sensitive action"}
        description="HIPAA policy requires step-up re-authentication before writing to a medical record or deleting a patient."
        onClose={() => {
          setReauthOpen(false);
          setReauthIntent(null);
        }}
        onConfirmed={() => {
          setReauthOpen(false);
          if (reauthIntent === "record") setRecDialog(true);
          if (reauthIntent === "delete") setDeleteConfirm(true);
          setReauthIntent(null);
        }}
      />

      <AlertDialog open={deleteConfirm} onOpenChange={(v) => !v && setDeleteConfirm(false)}>
        <AlertDialogContent data-testid="patient-delete-confirm" className="rounded-sm">
          <AlertDialogHeader>
            <div className="flex items-center gap-2">
              <ShieldAlert className="h-5 w-5 text-destructive" aria-hidden="true" />
              <AlertDialogTitle className="font-display">Archive this patient?</AlertDialogTitle>
            </div>
            <AlertDialogDescription>
              The patient will be removed from active workflows but retained according to the 7-year
              record-retention policy. This action is audited and can only be reversed by an authorized user.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-1">
            <Label>Reason for archiving (8+ characters)</Label>
            <Textarea
              data-testid="patient-delete-reason"
              value={deleteReason}
              onChange={(e) => setDeleteReason(e.target.value)}
              rows={3}
              className="rounded-sm"
              placeholder="e.g. Patient transferred care to another clinic on Jul 3."
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-sm">Keep active</AlertDialogCancel>
            <AlertDialogAction
              data-testid="patient-delete-confirm-btn"
              disabled={deleteReason.trim().length < 8}
              onClick={softDelete}
              className="rounded-sm bg-destructive hover:brightness-95"
            >
              Archive patient
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {canEditIntake && (
        <>
          <PatientWizardDialog
            open={editWizardOpen}
            onClose={() => setEditWizardOpen(false)}
            mode="edit"
            scope="patient"
            patientId={id}
            initialForm={payloadToForm(patient)}
            userId={user.id}
            tenantId={user.tenant_id}
            onSaved={() => load({ withUnmask: unmask, breakGlassReason: reason })}
          />
          <PatientWizardDialog
            open={intakeWizardOpen}
            onClose={() => {
              setIntakeWizardOpen(false);
              setEditingIntakeForm(null);
            }}
            mode="edit"
            scope="intake"
            patientId={id}
            intakeFormId={editingIntakeForm?.id}
            intakeFormStatus={editingIntakeForm?.status}
            initialForm={
              editingIntakeForm
                ? payloadToForm({
                    ...patient,
                    clinical_intake: editingIntakeForm.clinical_intake,
                    case_details: editingIntakeForm.case_details,
                  })
                : payloadToForm(patient)
            }
            userId={user.id}
            tenantId={user.tenant_id}
            onSaved={() => {
              setIntakeRefreshKey((k) => k + 1);
              load({ withUnmask: unmask, breakGlassReason: reason });
            }}
          />
        </>
      )}
    </div>
  );
}

