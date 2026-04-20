import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, Download, Eye, EyeOff, FileText, Pencil, Plus, Trash2 } from "lucide-react";
import { api, formatApiError } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import { formatDate, formatDateTime, relativeFromNow } from "../utils/time";
import { PatientWizardDialog } from "./Patients";
import { payloadToForm } from "./patientWizardLogic";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
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
      <span className="text-[11px] font-semibold uppercase tracking-wider text-[#5C6A61]">{label}</span>
      <span className="text-sm leading-relaxed text-[#1F2924] break-words">
        {Array.isArray(children) ? children.join(", ") : children}
      </span>
    </div>
  );
}

function IntakeCard({ title, hint, testId, children }) {
  return (
    <div
      data-testid={testId}
      className="rounded-sm border border-stone-200 bg-white p-6"
    >
      <div className="mb-4 border-b border-stone-200 pb-2">
        <h3 className="font-['Outfit'] text-lg font-medium text-[#1F2924]">{title}</h3>
        {hint && <p className="mt-0.5 text-xs text-[#5C6A61]">{hint}</p>}
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
    <div data-testid={testId} className="rounded-sm border border-stone-200 bg-[#FAF9F6] p-4">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[#5C6A61]">
        {label}
      </div>
      <div className="space-y-1.5">
        {fields.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[140px_1fr] gap-3 text-sm">
            <span className="text-[#5C6A61]">{k}</span>
            <span className="text-[#1F2924]">{v}</span>
          </div>
        ))}
      </div>
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
      <span className="text-[#1F2924]">{label}</span>
      <div className="flex items-center gap-2">
        <span className="text-right text-xs text-[#5C6A61]">{meta || "Accepted"}</span>
        {onDownloadPdf && consentType && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onDownloadPdf(consentType)}
            data-testid={`consent-pdf-${consentType}`}
            className="h-6 px-2 text-[11px] font-semibold uppercase tracking-wider text-[#526B58] hover:bg-[#EDF2EE]"
          >
            <Download className="mr-1 h-3 w-3" /> PDF
          </Button>
        )}
      </div>
    </div>
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
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
          Expanded intake
        </span>
        <h2 className="mt-1 font-['Outfit'] text-2xl font-medium tracking-tight">
          Intake sections
        </h2>
        {!patient.unmasked && (
          <p className="mt-1 text-xs text-[#5C6A61]">
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
            <Row label="Employer phone">{demo.employer_phone}</Row>
            <Row label="SSN (last 4)">{demo.ssn_last4 ? `•••• ${demo.ssn_last4}` : null}</Row>
          </IntakeCard>
        )}

        {hasValue(contact) && (
          <IntakeCard title="Contact" testId="intake-contact">
            <Row label="Mobile phone">{contact.phone || patient.phone}</Row>
            <Row label="Home phone">{contact.phone_alt}</Row>
            <Row label="Work phone">{contact.phone_work}</Row>
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
            <Row label="Primary phone">{ec.phone}</Row>
            <Row label="Alternate phone">{ec.phone_alt}</Row>
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
                <Row label="Phone">{guarantor.phone}</Row>
                <Row label="Email">{guarantor.email}</Row>
                <Row label="Address">{guarantor.address}</Row>
                <Row label="Employer">{guarantor.employer}</Row>
                <Row label="Employer phone">{guarantor.employer_phone}</Row>
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
            <Row label="Adjuster phone">{caseDetails.adjuster_phone}</Row>
            <Row label="Attorney name">{caseDetails.attorney_name}</Row>
            <Row label="Attorney phone">{caseDetails.attorney_phone}</Row>
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
          <DialogTitle className="font-['Outfit']">Add medical record</DialogTitle>
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
              className="rounded-sm bg-[#7B9A82] hover:bg-[#65826C]">
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

  return (
    <div data-testid="patient-detail-page" className="space-y-10 animate-in fade-in duration-300">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" asChild className="text-[#526B58]">
          <Link to="/patients" data-testid="patient-back-link">
            <ArrowLeft className="mr-2 h-4 w-4" /> All patients
          </Link>
        </Button>
        <div className="flex items-center gap-2">
          {canUnmask && (
            <Button
              variant="outline"
              onClick={() => {
                const next = !unmask;
                setUnmask(next);
                load({ withUnmask: next, breakGlassReason: reason });
              }}
              data-testid="patient-unmask-toggle"
              className="rounded-sm"
            >
              {unmask ? <EyeOff className="mr-2 h-4 w-4" /> : <Eye className="mr-2 h-4 w-4" />}
              {unmask ? "Mask" : "Unmask (audited)"}
            </Button>
          )}
          {canEditIntake && (
            <Button
              variant="outline"
              onClick={async () => {
                if (patient.unmasked) {
                  setEditWizardOpen(true);
                  return;
                }
                // Inline unmask flow — if the staff member is allowed to
                // unmask (admin), flip it automatically and open the wizard
                // right after the patient reloads. Audit trail still fires.
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
                // Non-admin clinicians still need break-glass — nudge them
                // to supply a reason instead of silently failing.
                toast.message("Break-glass required to edit intake", {
                  description: "Enter a reason above to unmask this record before editing.",
                });
              }}
              data-testid="patient-edit-intake-btn"
              className="rounded-sm"
            >
              <Pencil className="mr-2 h-4 w-4" /> Edit intake
            </Button>
          )}
          {canExport && (
            <Button
              variant="outline"
              onClick={exportPatient}
              data-testid="patient-export-btn"
              className="rounded-sm"
            >
              <Download className="mr-2 h-4 w-4" /> Export JSON
            </Button>
          )}
          {canDelete && (
            <Button
              variant="outline"
              onClick={() => setDeleteConfirm(true)}
              data-testid="patient-delete-btn"
              className="rounded-sm border-[#C76D54] text-[#C76D54] hover:bg-[#FBF1EE]"
            >
              <Trash2 className="mr-2 h-4 w-4" /> Soft-delete
            </Button>
          )}
        </div>
      </div>

      <header className="flex flex-wrap items-start justify-between gap-6">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
            Patient profile {patient.unmasked ? "" : "· masked"}
          </span>
          <h1 className="mt-2 font-['Outfit'] text-4xl font-medium tracking-tight text-[#1F2924]">
            {patient.unmasked
              ? `${patient.first_name} ${patient.last_name}`
              : patient.display_name_masked || "—"}
          </h1>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm text-[#5C6A61]">
            {patient.date_of_birth && <span>DOB {patient.unmasked ? formatDate(patient.date_of_birth) : patient.date_of_birth}</span>}
            {patient.phone && <span>{patient.phone}</span>}
            {patient.email && <span>{patient.email}</span>}
            {patient.gender && <span>{patient.gender}</span>}
            {patient.status === "deleted" && (
              <span className="rounded-sm bg-[#FBF1EE] px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-[#C76D54]">
                Deleted · retained until {patient.retention_until ? formatDate(patient.retention_until) : "—"}
              </span>
            )}
          </div>
        </div>
      </header>

      <section className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <div className="rounded-sm border border-stone-200 bg-white p-6">
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">Address</span>
          <p className="mt-3 text-sm leading-relaxed text-[#1F2924]">{patient.address || "—"}</p>
        </div>
        <div className="rounded-sm border border-stone-200 bg-white p-6">
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">Emergency contact</span>
          <p className="mt-3 text-sm leading-relaxed text-[#1F2924]">{patient.emergency_contact || "—"}</p>
        </div>
        <div className="rounded-sm border border-stone-200 bg-white p-6">
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">Intake notes</span>
          <p className="mt-3 text-sm leading-relaxed text-[#1F2924]">{patient.notes || "—"}</p>
        </div>
      </section>

      <IntakeSections patient={patient} onDownloadConsent={downloadConsentPdf} />

      <PatientDocumentsCard patientId={id} canEdit={canEditIntake} />

      <section>
        <div className="mb-4 flex items-end justify-between">
          <div>
            <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">Clinical history</span>
            <h2 className="mt-1 font-['Outfit'] text-2xl font-medium tracking-tight">Medical records</h2>
          </div>
          {canAddRecord && (
            <Button
              onClick={() => setRecDialog(true)}
              data-testid="record-new-btn"
              className="rounded-sm bg-[#7B9A82] hover:bg-[#65826C]"
            >
              <Plus className="mr-2 h-4 w-4" /> Add record
            </Button>
          )}
        </div>

        {records === null ? (
          <Skeleton className="h-32" />
        ) : records.length === 0 ? (
          <div className="rounded-sm border border-dashed border-stone-200 bg-white p-12 text-center text-sm text-[#5C6A61]">
            No medical records yet.
          </div>
        ) : (
          <ol className="relative space-y-4 border-l border-stone-200 pl-6">
            {records.map((r) => (
              <li key={r.id} data-testid={`record-${r.id}`} className="relative rounded-sm border border-stone-200 bg-white p-5">
                <span className="absolute -left-[33px] top-5 flex h-5 w-5 items-center justify-center rounded-sm bg-[#EDF2EE] text-[#526B58]">
                  <FileText className="h-3 w-3" />
                </span>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <span className="rounded-sm bg-[#F5F5F0] px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-[#5C6A61]">
                      {r.record_type}
                    </span>
                    <h3 className="mt-2 font-['Outfit'] text-lg font-medium text-[#1F2924]">{r.title}</h3>
                  </div>
                  <div className="text-xs text-[#5C6A61]">
                    {formatDateTime(r.recorded_at)} · {r.recorded_by_name || "—"}
                  </div>
                </div>
                {r.description && <p className="mt-3 text-sm leading-relaxed text-[#1F2924]">{r.description}</p>}
                <div className="mt-3 grid grid-cols-2 gap-4 text-sm">
                  {r.diagnosis && (
                    <div><span className="text-[11px] uppercase tracking-wider text-[#5C6A61]">Diagnosis</span>
                      <div className="text-[#1F2924]">{r.diagnosis}</div></div>
                  )}
                  {r.treatment && (
                    <div><span className="text-[11px] uppercase tracking-wider text-[#5C6A61]">Treatment</span>
                      <div className="text-[#1F2924]">{r.treatment}</div></div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section>
        <div className="mb-4">
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">Scheduling</span>
          <h2 className="mt-1 font-['Outfit'] text-2xl font-medium tracking-tight">Appointments</h2>
        </div>
        {appointments === null ? (
          <Skeleton className="h-24" />
        ) : appointments.length === 0 ? (
          <div className="rounded-sm border border-dashed border-stone-200 bg-white p-10 text-center text-sm text-[#5C6A61]">
            No appointments for this patient.
          </div>
        ) : (
          <ul className="space-y-2">
            {appointments.map((a) => (
              <li key={a.id} className="flex items-center justify-between rounded-sm border border-stone-200 bg-white px-5 py-4 text-sm">
                <div>
                  <div className="font-medium text-[#1F2924]">{formatDateTime(a.start_time)}</div>
                  <div className="text-xs text-[#5C6A61]">
                    with {a.provider_name} · {relativeFromNow(a.start_time)}
                  </div>
                </div>
                <span className={`rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
                  a.status === "cancelled" ? "bg-[#FBF1EE] text-[#C76D54]"
                    : a.status === "completed" ? "bg-[#F5F5F0] text-[#5C6A61]"
                    : "bg-[#EDF2EE] text-[#526B58]"}`}>
                  {a.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

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
            <AlertDialogTitle className="font-['Outfit']">Soft-delete patient?</AlertDialogTitle>
            <AlertDialogDescription>
              The record will be archived and retained for 7 years, per our retention policy. It will
              disappear from the active list but stay recoverable for compliance.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-1">
            <Label>Reason (8+ characters)</Label>
            <Textarea
              data-testid="patient-delete-reason"
              value={deleteReason}
              onChange={(e) => setDeleteReason(e.target.value)}
              rows={3}
              className="rounded-sm"
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-sm">Keep it</AlertDialogCancel>
            <AlertDialogAction
              data-testid="patient-delete-confirm-btn"
              disabled={deleteReason.trim().length < 8}
              onClick={softDelete}
              className="rounded-sm bg-[#C76D54] hover:bg-[#B35F47]"
            >
              Soft-delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {canEditIntake && (
        <PatientWizardDialog
          open={editWizardOpen}
          onClose={() => setEditWizardOpen(false)}
          mode="edit"
          patientId={id}
          initialForm={payloadToForm(patient)}
          userId={user.id}
          tenantId={user.tenant_id}
          onSaved={() => load({ withUnmask: unmask, breakGlassReason: reason })}
        />
      )}
    </div>
  );
}
