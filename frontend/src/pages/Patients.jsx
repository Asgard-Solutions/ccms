import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  Plus,
  Search,
  User2,
} from "lucide-react";
import { api, formatApiError } from "../api/client";
import { toast } from "sonner";
import { useAuth } from "../contexts/AuthContext";
import { formatDate } from "../utils/time";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import { Checkbox } from "../components/ui/checkbox";
import { Skeleton } from "../components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { SignaturePad } from "../components/SignaturePad";
import {
  PAIN_AREA_OPTIONS,
  SYMPTOM_OPTIONS,
  ONSET_TYPE_OPTIONS,
  buildPayload,
  validateStep,
  validateAll,
  visibilityForForm,
  payloadToForm,
  draftStorageKey,
  isDraftFresh,
  formHasAnyInput,
} from "./patientWizardLogic";

const STAFF_ROLES = ["admin", "doctor", "staff"];

const STEPS = [
  { id: 1, label: "Patient Info", sub: "Demographics, contact & address" },
  { id: 2, label: "Billing & Insurance", sub: "Provider, guarantor & plans" },
  { id: 3, label: "Clinical Intake", sub: "Chief complaint & history" },
  { id: 4, label: "Case & Consents", sub: "Case details & signatures" },
];

const TODAY_ISO = new Date().toISOString().slice(0, 10);

const EMPTY_FORM = {
  // Step 1 — Patient Info
  firstName: "",
  middleName: "",
  lastName: "",
  preferredName: "",
  dateOfBirth: "",
  sexAtBirth: "",
  genderIdentity: "",
  pronouns: "",
  maritalStatus: "",
  preferredLanguage: "",
  mobilePhone: "",
  homePhone: "",
  workPhone: "",
  email: "",
  preferredContactMethod: "",
  smsConsent: false,
  emailConsent: false,
  voicemailConsent: false,
  addressLine1: "",
  addressLine2: "",
  city: "",
  state: "",
  postalCode: "",
  country: "USA",
  emergencyContactName: "",
  emergencyContactRelationship: "",
  emergencyContactPhone: "",
  emergencyContactAltPhone: "",
  emergencyContactEmail: "",
  // Step 2 — Billing & Insurance
  assignedProviderId: "",
  preferredLocationId: "",
  referralSource: "",
  occupation: "",
  employerName: "",
  employerPhone: "",
  responsiblePartySameAsPatient: true,
  guarantorFullName: "",
  guarantorRelationship: "",
  guarantorDateOfBirth: "",
  guarantorPhone: "",
  guarantorEmail: "",
  guarantorAddress: "",
  guarantorEmployerName: "",
  guarantorEmployerPhone: "",
  hasInsurance: false,
  primaryCarrier: "",
  primaryPlanName: "",
  primaryPlanType: "",
  primaryMemberId: "",
  primaryGroupNumber: "",
  primaryPolicyHolderName: "",
  primaryPolicyHolderRelationship: "",
  primaryPolicyHolderDob: "",
  primaryEffectiveDate: "",
  primaryCopay: "",
  primaryDeductible: "",
  secondaryCarrier: "",
  secondaryPlanName: "",
  secondaryPlanType: "",
  secondaryMemberId: "",
  secondaryGroupNumber: "",
  secondaryPolicyHolderName: "",
  secondaryPolicyHolderRelationship: "",
  // Step 3 — Clinical Intake
  chiefComplaint: "",
  symptomStartDate: "",
  onsetType: "",
  accidentRelated: false,
  workComp: false,
  personalInjury: false,
  painAreas: [],
  painAreasOther: "",
  painScore: "",
  symptoms: [],
  symptomsOther: "",
  priorTreatment: "",
  medications: "",
  allergies: "",
  surgeries: "",
  medicalHistory: "",
  providerNotes: "",
  // Step 4 — Case Details & Consents
  accidentDate: "",
  claimNumber: "",
  autoCarrier: "",
  adjusterName: "",
  adjusterPhone: "",
  attorneyName: "",
  attorneyPhone: "",
  attorneyEmail: "",
  employerAtInjury: "",
  workCompCarrier: "",
  hipaaAcknowledged: false,
  consentToTreat: false,
  financialPolicyAccepted: false,
  assignmentOfBenefits: false,
  releaseOfInformation: false,
  signatureName: "",
  signatureDate: "",
  signatureImage: null,
};

// -----------------------------------------------------------------------
// Small field helpers
// -----------------------------------------------------------------------

function Field({ label, htmlFor, required, error, errorTestId, children, className = "" }) {
  return (
    <div className={`space-y-1.5 ${className}`}>
      <Label htmlFor={htmlFor} className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
        {required && <span className="text-destructive" aria-label="required">*</span>}
      </Label>
      {children}
      {error && (
        <p className="text-xs text-destructive" data-testid={errorTestId || `${htmlFor}-error`}>{error}</p>
      )}
    </div>
  );
}

function TextInput({ id, value, onChange, type = "text", placeholder, testId, autoComplete, max, min }) {
  return (
    <Input
      id={id}
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      autoComplete={autoComplete}
      max={max}
      min={min}
      data-testid={testId}
      className="h-10 rounded-sm border-border-strong bg-card text-sm"
    />
  );
}

function SelectField({ id, value, onChange, options, placeholder = "Select…", testId }) {
  return (
    <Select value={value || undefined} onValueChange={(v) => onChange(v)}>
      <SelectTrigger id={id} data-testid={testId} className="h-10 rounded-sm border-border-strong bg-card text-sm">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((opt) =>
          typeof opt === "string" ? (
            <SelectItem key={opt} value={opt}>{opt}</SelectItem>
          ) : (
            <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
          )
        )}
      </SelectContent>
    </Select>
  );
}

function CheckboxField({ id, checked, onChange, label, testId, disabled }) {
  return (
    <label
      htmlFor={id}
      className={
        "flex items-start gap-3 rounded-sm border border-border bg-card px-3 py-2.5 text-sm " +
        (disabled
          ? "cursor-not-allowed text-muted-foreground/70"
          : "cursor-pointer text-foreground hover:border-primary")
      }
    >
      <Checkbox
        id={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={(v) => onChange(Boolean(v))}
        data-testid={testId}
        className="mt-0.5 border-border-border-strong data-[state=checked]:border-primary data-[state=checked]:bg-primary"
      />
      <span className="leading-snug">{label}</span>
    </label>
  );
}

function SectionTitle({ children, hint }) {
  return (
    <div className="col-span-full mt-2 mb-1 border-b border-border pb-1">
      <h3 className="font-display text-base font-medium text-foreground">{children}</h3>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function CheckboxGroup({ options, selected, onChange, testId }) {
  const toggle = (value) => {
    const next = selected.includes(value)
      ? selected.filter((v) => v !== value)
      : [...selected, value];
    onChange(next);
  };
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3 lg:grid-cols-4" data-testid={testId}>
      {options.map((opt) => {
        const checked = selected.includes(opt);
        const id = `${testId}-${opt.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
        return (
          <label
            key={opt}
            htmlFor={id}
            className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1 text-sm text-foreground hover:bg-primary/10"
          >
            <Checkbox
              id={id}
              checked={checked}
              onCheckedChange={() => toggle(opt)}
              data-testid={`${testId}-opt-${opt.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`}
              className="border-border-border-strong data-[state=checked]:border-primary data-[state=checked]:bg-primary"
            />
            <span>{opt}</span>
          </label>
        );
      })}
    </div>
  );
}

// -----------------------------------------------------------------------
// Wizard step components
// -----------------------------------------------------------------------

function StepPatientInfo({ form, set, errors }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <SectionTitle hint="Legal name first; preferred name appears in portal.">Identity</SectionTitle>
      <Field label="First name" htmlFor="firstName" required error={errors.firstName}>
        <TextInput id="firstName" testId="w-first-name" value={form.firstName} onChange={set("firstName")} autoComplete="given-name" />
      </Field>
      <Field label="Middle name" htmlFor="middleName">
        <TextInput id="middleName" testId="w-middle-name" value={form.middleName} onChange={set("middleName")} />
      </Field>
      <Field label="Last name" htmlFor="lastName" required error={errors.lastName}>
        <TextInput id="lastName" testId="w-last-name" value={form.lastName} onChange={set("lastName")} autoComplete="family-name" />
      </Field>
      <Field label="Preferred name" htmlFor="preferredName">
        <TextInput id="preferredName" testId="w-preferred-name" value={form.preferredName} onChange={set("preferredName")} />
      </Field>
      <Field label="Date of birth" htmlFor="dob" required error={errors.dateOfBirth} errorTestId="w-dob-error">
        <TextInput id="dob" type="date" testId="w-dob" value={form.dateOfBirth} onChange={set("dateOfBirth")} max={TODAY_ISO} />
      </Field>
      <Field label="Sex at birth" htmlFor="sexAtBirth">
        <SelectField id="sexAtBirth" testId="w-sex-at-birth" value={form.sexAtBirth} onChange={set("sexAtBirth")}
          options={["Male", "Female", "Intersex", "Unknown"]} />
      </Field>
      <Field label="Gender identity" htmlFor="genderIdentity">
        <SelectField id="genderIdentity" testId="w-gender" value={form.genderIdentity} onChange={set("genderIdentity")}
          options={[
            { value: "male", label: "Male" },
            { value: "female", label: "Female" },
            { value: "non-binary", label: "Non-binary" },
            { value: "other", label: "Other" },
            { value: "prefer-not-to-say", label: "Prefer not to say" },
          ]} />
      </Field>
      <Field label="Pronouns" htmlFor="pronouns">
        <TextInput id="pronouns" testId="w-pronouns" value={form.pronouns} onChange={set("pronouns")} placeholder="she/her" />
      </Field>
      <Field label="Marital status" htmlFor="maritalStatus">
        <SelectField id="maritalStatus" testId="w-marital" value={form.maritalStatus} onChange={set("maritalStatus")}
          options={["single", "married", "divorced", "widowed", "partnered", "other"]} />
      </Field>
      <Field label="Preferred language" htmlFor="preferredLanguage">
        <TextInput id="preferredLanguage" testId="w-language" value={form.preferredLanguage} onChange={set("preferredLanguage")} placeholder="English" />
      </Field>

      <SectionTitle hint="Default contact details. Patient may update them in the portal.">Contact</SectionTitle>
      <Field label="Mobile phone" htmlFor="mobilePhone" required error={errors.mobilePhone} errorTestId="w-mobile-error">
        <TextInput id="mobilePhone" testId="w-mobile" value={form.mobilePhone} onChange={set("mobilePhone")} autoComplete="tel" />
      </Field>
      <Field label="Home phone" htmlFor="homePhone" error={errors.homePhone}>
        <TextInput id="homePhone" testId="w-home-phone" value={form.homePhone} onChange={set("homePhone")} />
      </Field>
      <Field label="Work phone" htmlFor="workPhone" error={errors.workPhone}>
        <TextInput id="workPhone" testId="w-work-phone" value={form.workPhone} onChange={set("workPhone")} />
      </Field>
      <Field label="Email" htmlFor="email" error={errors.email} errorTestId="w-email-error">
        <TextInput id="email" type="email" testId="w-email" value={form.email} onChange={set("email")} autoComplete="email" />
      </Field>
      <Field label="Preferred contact method" htmlFor="preferredContactMethod">
        <SelectField id="preferredContactMethod" testId="w-contact-method" value={form.preferredContactMethod} onChange={set("preferredContactMethod")}
          options={[
            { value: "phone", label: "Phone" },
            { value: "sms", label: "Text (SMS)" },
            { value: "email", label: "Email" },
            { value: "portal", label: "Patient portal" },
          ]} />
      </Field>
      <div className="col-span-full grid grid-cols-1 gap-2 sm:grid-cols-3">
        <CheckboxField id="smsConsent" testId="w-sms-consent" checked={form.smsConsent} onChange={set("smsConsent")}
          label="Consent to receive SMS reminders" />
        <CheckboxField id="emailConsent" testId="w-email-consent" checked={form.emailConsent} onChange={set("emailConsent")}
          label="Consent to receive email communications" />
        <CheckboxField id="voicemailConsent" testId="w-vm-consent" checked={form.voicemailConsent} onChange={set("voicemailConsent")}
          label="OK to leave a voicemail" />
      </div>

      <SectionTitle>Address</SectionTitle>
      <Field label="Address line 1" htmlFor="addressLine1" required error={errors.addressLine1} className="sm:col-span-2 lg:col-span-2">
        <TextInput id="addressLine1" testId="w-addr-line1" value={form.addressLine1} onChange={set("addressLine1")} autoComplete="address-line1" />
      </Field>
      <Field label="Address line 2" htmlFor="addressLine2">
        <TextInput id="addressLine2" testId="w-addr-line2" value={form.addressLine2} onChange={set("addressLine2")} autoComplete="address-line2" />
      </Field>
      <Field label="City" htmlFor="city" required error={errors.city}>
        <TextInput id="city" testId="w-city" value={form.city} onChange={set("city")} autoComplete="address-level2" />
      </Field>
      <Field label="State" htmlFor="state" required error={errors.state}>
        <TextInput id="state" testId="w-state" value={form.state} onChange={set("state")} autoComplete="address-level1" />
      </Field>
      <Field label="Postal code" htmlFor="postalCode" required error={errors.postalCode} errorTestId="w-postal-error">
        <TextInput id="postalCode" testId="w-postal" value={form.postalCode} onChange={set("postalCode")} autoComplete="postal-code" />
      </Field>
      <Field label="Country" htmlFor="country">
        <TextInput id="country" testId="w-country" value={form.country} onChange={set("country")} autoComplete="country-name" />
      </Field>

      <SectionTitle hint="Required for HIPAA and duty-of-care scenarios.">Emergency contact</SectionTitle>
      <Field label="Full name" htmlFor="ecName" required error={errors.emergencyContactName}>
        <TextInput id="ecName" testId="w-ec-name" value={form.emergencyContactName} onChange={set("emergencyContactName")} />
      </Field>
      <Field label="Relationship" htmlFor="ecRel" required error={errors.emergencyContactRelationship}>
        <TextInput id="ecRel" testId="w-ec-rel" value={form.emergencyContactRelationship} onChange={set("emergencyContactRelationship")} placeholder="Spouse, parent…" />
      </Field>
      <Field label="Primary phone" htmlFor="ecPhone" required error={errors.emergencyContactPhone}>
        <TextInput id="ecPhone" testId="w-ec-phone" value={form.emergencyContactPhone} onChange={set("emergencyContactPhone")} />
      </Field>
      <Field label="Alternate phone" htmlFor="ecAlt" error={errors.emergencyContactAltPhone}>
        <TextInput id="ecAlt" testId="w-ec-alt" value={form.emergencyContactAltPhone} onChange={set("emergencyContactAltPhone")} />
      </Field>
      <Field label="Email" htmlFor="ecEmail" error={errors.emergencyContactEmail}>
        <TextInput id="ecEmail" type="email" testId="w-ec-email" value={form.emergencyContactEmail} onChange={set("emergencyContactEmail")} />
      </Field>
    </div>
  );
}

function StepBillingInsurance({ form, set, errors, providers, locations, visibility }) {
  const { showGuarantor, requireGuarantor, showInsurance, isMinor: minor } = visibility;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <SectionTitle>Care assignment</SectionTitle>
      <Field label="Assigned provider" htmlFor="provider" required error={errors.assignedProviderId}>
        <SelectField id="provider" testId="w-provider" value={form.assignedProviderId} onChange={set("assignedProviderId")}
          options={providers.map((p) => ({ value: p.id, label: p.name }))} />
      </Field>
      <Field label="Preferred location" htmlFor="location">
        <SelectField id="location" testId="w-location" value={form.preferredLocationId} onChange={set("preferredLocationId")}
          options={locations.map((l) => ({ value: l.id, label: l.name }))} />
      </Field>
      <Field label="Referral source" htmlFor="referralSource">
        <TextInput id="referralSource" testId="w-referral" value={form.referralSource} onChange={set("referralSource")} placeholder="Google, Dr. Smith, friend…" />
      </Field>

      <SectionTitle>Employment</SectionTitle>
      <Field label="Occupation" htmlFor="occupation">
        <TextInput id="occupation" testId="w-occupation" value={form.occupation} onChange={set("occupation")} />
      </Field>
      <Field label="Employer name" htmlFor="employerName">
        <TextInput id="employerName" testId="w-employer" value={form.employerName} onChange={set("employerName")} />
      </Field>
      <Field label="Employer phone" htmlFor="employerPhone">
        <TextInput id="employerPhone" testId="w-employer-phone" value={form.employerPhone} onChange={set("employerPhone")} />
      </Field>

      <SectionTitle hint={minor
        ? "Patient is under 18 — a guarantor (parent/legal guardian) is required."
        : "Person financially responsible for this account."}>
        Responsible party / Guarantor
      </SectionTitle>
      <div className="col-span-full">
        <CheckboxField id="guarantorSame" testId="w-guarantor-same"
          checked={form.responsiblePartySameAsPatient && !minor}
          disabled={minor}
          onChange={(v) => set("responsiblePartySameAsPatient")(minor ? false : v)}
          label={minor
            ? "Minors cannot be their own responsible party — guarantor details required below."
            : "Responsible party is the same as the patient"} />
      </div>
      {showGuarantor && (
        <div data-testid="w-guarantor-block" className="col-span-full grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Full name" htmlFor="gName" required={requireGuarantor} error={errors.guarantorFullName} errorTestId="w-g-name-error">
            <TextInput id="gName" testId="w-g-name" value={form.guarantorFullName} onChange={set("guarantorFullName")} />
          </Field>
          <Field label="Relationship" htmlFor="gRel" required={requireGuarantor} error={errors.guarantorRelationship} errorTestId="w-g-rel-error">
            <TextInput id="gRel" testId="w-g-rel" value={form.guarantorRelationship} onChange={set("guarantorRelationship")} />
          </Field>
          <Field label="Date of birth" htmlFor="gDob">
            <TextInput id="gDob" type="date" testId="w-g-dob" value={form.guarantorDateOfBirth} onChange={set("guarantorDateOfBirth")} max={TODAY_ISO} />
          </Field>
          <Field label="Phone" htmlFor="gPhone" required={requireGuarantor} error={errors.guarantorPhone} errorTestId="w-g-phone-error">
            <TextInput id="gPhone" testId="w-g-phone" value={form.guarantorPhone} onChange={set("guarantorPhone")} />
          </Field>
          <Field label="Email" htmlFor="gEmail" error={errors.guarantorEmail}>
            <TextInput id="gEmail" type="email" testId="w-g-email" value={form.guarantorEmail} onChange={set("guarantorEmail")} />
          </Field>
          <Field label="Address" htmlFor="gAddr">
            <TextInput id="gAddr" testId="w-g-addr" value={form.guarantorAddress} onChange={set("guarantorAddress")} />
          </Field>
          <Field label="Employer name" htmlFor="gEmployer">
            <TextInput id="gEmployer" testId="w-g-employer" value={form.guarantorEmployerName} onChange={set("guarantorEmployerName")} />
          </Field>
          <Field label="Employer phone" htmlFor="gEmployerPhone">
            <TextInput id="gEmployerPhone" testId="w-g-employer-phone" value={form.guarantorEmployerPhone} onChange={set("guarantorEmployerPhone")} />
          </Field>
        </div>
      )}

      <SectionTitle>Insurance</SectionTitle>
      <div className="col-span-full">
        <CheckboxField id="hasInsurance" testId="w-has-insurance"
          checked={form.hasInsurance}
          onChange={set("hasInsurance")}
          label="Patient has insurance coverage to record" />
      </div>
      {showInsurance && (
        <div data-testid="w-insurance-block" className="col-span-full grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="col-span-full -mb-1 mt-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Primary insurance
          </div>
          <Field label="Carrier" htmlFor="pCarrier">
            <TextInput id="pCarrier" testId="w-pi-carrier" value={form.primaryCarrier} onChange={set("primaryCarrier")} placeholder="BlueCross, Aetna…" />
          </Field>
          <Field label="Plan name" htmlFor="pPlan">
            <TextInput id="pPlan" testId="w-pi-plan" value={form.primaryPlanName} onChange={set("primaryPlanName")} />
          </Field>
          <Field label="Plan type" htmlFor="pType">
            <SelectField id="pType" testId="w-pi-type" value={form.primaryPlanType} onChange={set("primaryPlanType")}
              options={["PPO", "HMO", "EPO", "POS", "HDHP", "Medicare", "Medicaid", "Auto-MedPay", "Other"]} />
          </Field>
          <Field label="Member ID" htmlFor="pMember">
            <TextInput id="pMember" testId="w-pi-member" value={form.primaryMemberId} onChange={set("primaryMemberId")} />
          </Field>
          <Field label="Group #" htmlFor="pGroup">
            <TextInput id="pGroup" testId="w-pi-group" value={form.primaryGroupNumber} onChange={set("primaryGroupNumber")} />
          </Field>
          <Field label="Policy holder name" htmlFor="pHolder">
            <TextInput id="pHolder" testId="w-pi-holder" value={form.primaryPolicyHolderName} onChange={set("primaryPolicyHolderName")} />
          </Field>
          <Field label="Relationship to policy holder" htmlFor="pHolderRel">
            <SelectField id="pHolderRel" testId="w-pi-holder-rel" value={form.primaryPolicyHolderRelationship} onChange={set("primaryPolicyHolderRelationship")}
              options={["self", "spouse", "parent", "child", "other"]} />
          </Field>
          <Field label="Policy holder DOB" htmlFor="pHolderDob">
            <TextInput id="pHolderDob" type="date" testId="w-pi-holder-dob" value={form.primaryPolicyHolderDob} onChange={set("primaryPolicyHolderDob")} max={TODAY_ISO} />
          </Field>
          <Field label="Effective date" htmlFor="pEff">
            <TextInput id="pEff" type="date" testId="w-pi-eff" value={form.primaryEffectiveDate} onChange={set("primaryEffectiveDate")} />
          </Field>
          <Field label="Copay" htmlFor="pCopay">
            <TextInput id="pCopay" testId="w-pi-copay" value={form.primaryCopay} onChange={set("primaryCopay")} placeholder="$30" />
          </Field>
          <Field label="Deductible" htmlFor="pDed">
            <TextInput id="pDed" testId="w-pi-deductible" value={form.primaryDeductible} onChange={set("primaryDeductible")} placeholder="$1500" />
          </Field>

          <div className="col-span-full -mb-1 mt-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Secondary insurance (optional)
          </div>
          <Field label="Carrier" htmlFor="sCarrier">
            <TextInput id="sCarrier" testId="w-si-carrier" value={form.secondaryCarrier} onChange={set("secondaryCarrier")} />
          </Field>
          <Field label="Plan name" htmlFor="sPlan">
            <TextInput id="sPlan" testId="w-si-plan" value={form.secondaryPlanName} onChange={set("secondaryPlanName")} />
          </Field>
          <Field label="Plan type" htmlFor="sType">
            <SelectField id="sType" testId="w-si-type" value={form.secondaryPlanType} onChange={set("secondaryPlanType")}
              options={["PPO", "HMO", "EPO", "POS", "HDHP", "Medicare", "Medicaid", "Other"]} />
          </Field>
          <Field label="Member ID" htmlFor="sMember">
            <TextInput id="sMember" testId="w-si-member" value={form.secondaryMemberId} onChange={set("secondaryMemberId")} />
          </Field>
          <Field label="Group #" htmlFor="sGroup">
            <TextInput id="sGroup" testId="w-si-group" value={form.secondaryGroupNumber} onChange={set("secondaryGroupNumber")} />
          </Field>
          <Field label="Policy holder" htmlFor="sHolder">
            <TextInput id="sHolder" testId="w-si-holder" value={form.secondaryPolicyHolderName} onChange={set("secondaryPolicyHolderName")} />
          </Field>
          <Field label="Relationship" htmlFor="sHolderRel">
            <SelectField id="sHolderRel" testId="w-si-holder-rel" value={form.secondaryPolicyHolderRelationship} onChange={set("secondaryPolicyHolderRelationship")}
              options={["self", "spouse", "parent", "child", "other"]} />
          </Field>
        </div>
      )}
    </div>
  );
}

function StepClinicalIntake({ form, set }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <SectionTitle hint="The patient's primary reason for visiting.">Chief complaint</SectionTitle>
      <Field label="Chief complaint" htmlFor="cc" className="col-span-full">
        <Textarea id="cc" data-testid="w-chief-complaint"
          className="min-h-[90px] rounded-sm border-border-strong bg-card text-sm"
          value={form.chiefComplaint} onChange={(e) => set("chiefComplaint")(e.target.value)} />
      </Field>
      <Field label="Symptom start date" htmlFor="symStart">
        <TextInput id="symStart" type="date" testId="w-sym-start" value={form.symptomStartDate} onChange={set("symptomStartDate")} max={TODAY_ISO} />
      </Field>
      <Field label="Onset type" htmlFor="onsetType">
        <SelectField id="onsetType" testId="w-onset-type" value={form.onsetType} onChange={set("onsetType")}
          options={ONSET_TYPE_OPTIONS} />
      </Field>
      <Field label="Pain score (0–10)" htmlFor="painScore">
        <TextInput id="painScore" type="number" testId="w-pain-score" value={form.painScore} onChange={set("painScore")} placeholder="0–10" min="0" max="10" />
      </Field>

      <div className="col-span-full grid grid-cols-1 gap-2 sm:grid-cols-3">
        <CheckboxField id="accidentRelated" testId="w-accident-related"
          checked={form.accidentRelated} onChange={set("accidentRelated")}
          label="Accident-related (auto / slip & fall)" />
        <CheckboxField id="workComp" testId="w-work-comp"
          checked={form.workComp} onChange={set("workComp")} label="Workers' compensation case" />
        <CheckboxField id="personalInjury" testId="w-pi"
          checked={form.personalInjury} onChange={set("personalInjury")} label="Personal injury case" />
      </div>

      <SectionTitle hint="Check every area the patient is currently experiencing pain. Add free-text for anything else.">
        Pain areas
      </SectionTitle>
      <div className="col-span-full">
        <CheckboxGroup
          testId="w-pain-areas"
          options={PAIN_AREA_OPTIONS}
          selected={form.painAreas}
          onChange={set("painAreas")}
        />
      </div>
      <Field label="Other pain area(s) (comma-separated)" htmlFor="painOther" className="col-span-full">
        <TextInput id="painOther" testId="w-pain-other" value={form.painAreasOther} onChange={set("painAreasOther")} placeholder="abdomen, ribs…" />
      </Field>

      <SectionTitle>Associated symptoms</SectionTitle>
      <div className="col-span-full">
        <CheckboxGroup
          testId="w-symptoms"
          options={SYMPTOM_OPTIONS}
          selected={form.symptoms}
          onChange={set("symptoms")}
        />
      </div>
      <Field label="Other symptom(s) (comma-separated)" htmlFor="symOther" className="col-span-full">
        <TextInput id="symOther" testId="w-symptoms-other" value={form.symptomsOther} onChange={set("symptomsOther")} placeholder="blurred vision, tinnitus…" />
      </Field>

      <SectionTitle>History</SectionTitle>
      <Field label="Prior treatment tried" htmlFor="priorTreatment" className="col-span-full">
        <Textarea id="priorTreatment" data-testid="w-prior-treatment"
          className="min-h-[70px] rounded-sm border-border-strong bg-card text-sm"
          value={form.priorTreatment} onChange={(e) => set("priorTreatment")(e.target.value)} />
      </Field>
      <Field label="Current medications" htmlFor="medications" className="col-span-full">
        <Textarea id="medications" data-testid="w-medications"
          className="min-h-[60px] rounded-sm border-border-strong bg-card text-sm"
          value={form.medications} onChange={(e) => set("medications")(e.target.value)} />
      </Field>
      <Field label="Allergies" htmlFor="allergies" className="col-span-full">
        <TextInput id="allergies" testId="w-allergies" value={form.allergies} onChange={set("allergies")} placeholder="penicillin, latex…" />
      </Field>
      <Field label="Prior surgeries" htmlFor="surgeries" className="col-span-full">
        <Textarea id="surgeries" data-testid="w-surgeries"
          className="min-h-[60px] rounded-sm border-border-strong bg-card text-sm"
          value={form.surgeries} onChange={(e) => set("surgeries")(e.target.value)} />
      </Field>
      <Field label="Past medical history" htmlFor="medicalHistory" className="col-span-full">
        <Textarea id="medicalHistory" data-testid="w-medical-history"
          className="min-h-[70px] rounded-sm border-border-strong bg-card text-sm"
          value={form.medicalHistory} onChange={(e) => set("medicalHistory")(e.target.value)} />
      </Field>
      <Field label="Provider notes (internal)" htmlFor="providerNotes" className="col-span-full">
        <Textarea id="providerNotes" data-testid="w-provider-notes"
          className="min-h-[70px] rounded-sm border-border-strong bg-card text-sm"
          value={form.providerNotes} onChange={(e) => set("providerNotes")(e.target.value)} />
      </Field>
    </div>
  );
}

function StepCaseConsents({ form, set, visibility }) {
  const { showAccident, showWorkComp, showPersonalInjury } = visibility;
  const anyCase = showAccident || showWorkComp || showPersonalInjury;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {!anyCase && (
        <div
          data-testid="w-case-empty-state"
          className="col-span-full rounded-sm border border-dashed border-border-strong bg-card px-5 py-6 text-sm text-muted-foreground"
        >
          No case-type flags selected on the Clinical Intake step. Accident, workers&apos; compensation
          and personal-injury detail fields will appear here when you mark the matching check-box on Step 3.
        </div>
      )}

      {showAccident && (
        <>
          <SectionTitle hint="Auto accident, slip-and-fall, sports injury, etc.">
            Accident details
          </SectionTitle>
          <Field label="Date of accident" htmlFor="accidentDate">
            <TextInput id="accidentDate" type="date" testId="w-accident-date" value={form.accidentDate} onChange={set("accidentDate")} max={TODAY_ISO} />
          </Field>
          <Field label="Auto insurance carrier" htmlFor="autoCarrier">
            <TextInput id="autoCarrier" testId="w-auto-carrier" value={form.autoCarrier} onChange={set("autoCarrier")} />
          </Field>
          <Field label="Claim #" htmlFor="claimNumberA">
            <TextInput id="claimNumberA" testId="w-claim-number" value={form.claimNumber} onChange={set("claimNumber")} />
          </Field>
          <Field label="Adjuster name" htmlFor="adjusterName">
            <TextInput id="adjusterName" testId="w-adjuster-name" value={form.adjusterName} onChange={set("adjusterName")} />
          </Field>
          <Field label="Adjuster phone" htmlFor="adjusterPhone">
            <TextInput id="adjusterPhone" testId="w-adjuster-phone" value={form.adjusterPhone} onChange={set("adjusterPhone")} />
          </Field>
        </>
      )}

      {showWorkComp && (
        <div data-testid="w-workcomp-block" className="col-span-full grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <SectionTitle hint="Employer details active at the time of injury.">Workers&apos; compensation</SectionTitle>
          <Field label="Employer at time of injury" htmlFor="employerAtInjury">
            <TextInput id="employerAtInjury" testId="w-employer-injury" value={form.employerAtInjury} onChange={set("employerAtInjury")} />
          </Field>
          <Field label="Workers' comp carrier" htmlFor="workCompCarrier">
            <TextInput id="workCompCarrier" testId="w-wc-carrier" value={form.workCompCarrier} onChange={set("workCompCarrier")} />
          </Field>
          <Field label="Claim #" htmlFor="claimNumberW">
            <TextInput id="claimNumberW" testId="w-claim-number-wc" value={form.claimNumber} onChange={set("claimNumber")} />
          </Field>
          {!showAccident && (
            <>
              <Field label="Adjuster name" htmlFor="adjusterNameW">
                <TextInput id="adjusterNameW" testId="w-adjuster-name-wc" value={form.adjusterName} onChange={set("adjusterName")} />
              </Field>
              <Field label="Adjuster phone" htmlFor="adjusterPhoneW">
                <TextInput id="adjusterPhoneW" testId="w-adjuster-phone-wc" value={form.adjusterPhone} onChange={set("adjusterPhone")} />
              </Field>
            </>
          )}
        </div>
      )}

      {showPersonalInjury && (
        <div data-testid="w-pi-block" className="col-span-full grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <SectionTitle hint="Attorney representation for this injury claim.">Personal injury</SectionTitle>
          <Field label="Attorney name" htmlFor="attorneyName">
            <TextInput id="attorneyName" testId="w-attorney-name" value={form.attorneyName} onChange={set("attorneyName")} />
          </Field>
          <Field label="Attorney phone" htmlFor="attorneyPhone">
            <TextInput id="attorneyPhone" testId="w-attorney-phone" value={form.attorneyPhone} onChange={set("attorneyPhone")} />
          </Field>
          <Field label="Attorney email" htmlFor="attorneyEmail">
            <TextInput id="attorneyEmail" type="email" testId="w-attorney-email" value={form.attorneyEmail} onChange={set("attorneyEmail")} />
          </Field>
          {!showAccident && !showWorkComp && (
            <Field label="Claim #" htmlFor="claimNumberP">
              <TextInput id="claimNumberP" testId="w-claim-number-pi" value={form.claimNumber} onChange={set("claimNumber")} />
            </Field>
          )}
        </div>
      )}

      <SectionTitle hint="All consents are versioned and audited.">Consents & signature</SectionTitle>
      <div className="col-span-full grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <CheckboxField id="hipaa" testId="w-consent-hipaa" checked={form.hipaaAcknowledged} onChange={set("hipaaAcknowledged")}
          label="HIPAA Notice of Privacy Practices acknowledged" />
        <CheckboxField id="treat" testId="w-consent-treat" checked={form.consentToTreat} onChange={set("consentToTreat")}
          label="Consent to treatment" />
        <CheckboxField id="finPol" testId="w-consent-financial" checked={form.financialPolicyAccepted} onChange={set("financialPolicyAccepted")}
          label="Financial policy accepted" />
        <CheckboxField id="aob" testId="w-consent-aob" checked={form.assignmentOfBenefits} onChange={set("assignmentOfBenefits")}
          label="Assignment of benefits" />
        <CheckboxField id="roi" testId="w-consent-roi" checked={form.releaseOfInformation} onChange={set("releaseOfInformation")}
          label="Release of information" />
      </div>
      <Field label="Signed by (typed signature)" htmlFor="sigName">
        <TextInput id="sigName" testId="w-sig-name" value={form.signatureName} onChange={set("signatureName")} />
      </Field>
      <Field label="Signature date" htmlFor="sigDate">
        <TextInput id="sigDate" type="date" testId="w-sig-date" value={form.signatureDate} onChange={set("signatureDate")} max={TODAY_ISO} />
      </Field>
      <Field label="Drawn signature (optional, wet-ink style)" htmlFor="sigImage" className="col-span-full">
        <SignaturePad
          testId="w-sig-pad"
          value={form.signatureImage}
          onChange={set("signatureImage")}
        />
      </Field>
    </div>
  );
}

// -----------------------------------------------------------------------
// Wizard modal
// -----------------------------------------------------------------------

export function PatientWizardDialog({
  open,
  onClose,
  onCreated,
  onSaved,
  mode = "create",
  scope = "patient",
  patientId,
  initialForm,
  userId,
  tenantId,
}) {
  // Scope slices the 4-step wizard into two focused flows:
  //   - `patient`: steps 1–2 (demographics + billing). Used for Add/Edit
  //     patient. No clinical intake required here.
  //   - `intake`:  steps 3–4 (clinical intake + case/consents). Opened from
  //     an existing patient record to capture/update intake data without
  //     scrolling past demographics.
  const visibleStepIds = useMemo(
    () => (scope === "intake" ? [3, 4] : [1, 2]),
    [scope]
  );
  const firstStep = visibleStepIds[0];
  const lastStep = visibleStepIds[visibleStepIds.length - 1];

  const [step, setStep] = useState(firstStep);
  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [providers, setProviders] = useState([]);
  const [locations, setLocations] = useState([]);
  const [draftPrompt, setDraftPrompt] = useState(null); // {savedAt, form}
  const [draftNotice, setDraftNotice] = useState(false); // "Draft saved"

  const visibility = useMemo(() => visibilityForForm(form), [form]);
  const isEdit = mode === "edit";
  const isIntakeScope = scope === "intake";
  // Draft autosave only makes sense for the Patient-scope create flow.
  // Intake scope is always edit-an-existing-record, so no draft is kept.
  const draftKey = useMemo(
    () => (isEdit || isIntakeScope ? null : draftStorageKey(userId, tenantId)),
    [isEdit, isIntakeScope, userId, tenantId]
  );

  const set = (k) => (v) => {
    setForm((prev) => {
      const next = { ...prev, [k]: v };
      // If the patient DOB just flipped them into minor status, force the
      // "same as patient" toggle off so the guarantor block is required.
      if (k === "dateOfBirth") {
        const willBeMinor = visibilityForForm(next).isMinor;
        if (willBeMinor) next.responsiblePartySameAsPatient = false;
      }
      return next;
    });
    if (errors[k]) setErrors((e) => ({ ...e, [k]: undefined }));
  };

  // Autosave to localStorage (create mode only). Fires on every form change,
  // so staff don't lose work to an accidental tab close. The key is scoped
  // to the signed-in user + tenant to avoid leaking drafts on shared kiosks.
  useEffect(() => {
    if (!open || isEdit || !draftKey) return;
    // Skip the first empty write so opening-then-closing doesn't stash junk.
    if (!formHasAnyInput(form)) return;
    try {
      window.localStorage.setItem(
        draftKey,
        JSON.stringify({ savedAt: new Date().toISOString(), step, form })
      );
      setDraftNotice(true);
      const t = setTimeout(() => setDraftNotice(false), 1200);
      return () => clearTimeout(t);
    } catch {
      /* localStorage quota / SecurityError — silently ignore */
    }
  }, [form, step, draftKey, isEdit, open]);

  useEffect(() => {
    if (!open) return;
    setErrors({});
    if (isEdit) {
      setStep(firstStep);
      setForm({ ...EMPTY_FORM, ...(initialForm || {}) });
      setDraftPrompt(null);
    } else {
      // Start from a clean form unless the user decides to resume.
      setForm(EMPTY_FORM);
      setStep(firstStep);
      // Probe localStorage for a resumable draft.
      let draft = null;
      try {
        const raw = draftKey && window.localStorage.getItem(draftKey);
        draft = raw ? JSON.parse(raw) : null;
      } catch {
        draft = null;
      }
      if (draft && isDraftFresh(draft.savedAt) && formHasAnyInput(draft.form)) {
        setDraftPrompt(draft);
      } else {
        if (draft && draftKey) {
          try { window.localStorage.removeItem(draftKey); } catch { /* ignore */ }
        }
        setDraftPrompt(null);
      }
    }
    (async () => {
      try {
        const [pr, ctx] = await Promise.all([
          api.get("/auth/providers"),
          api.get("/tenancy/me/context"),
        ]);
        setProviders(pr.data || []);
        setLocations((ctx.data && ctx.data.locations) || []);
      } catch {
        /* providers/locations optional */
      }
    })();
  }, [open, isEdit, initialForm, draftKey, firstStep]);

  const clearDraft = () => {
    if (!draftKey) return;
    try { window.localStorage.removeItem(draftKey); } catch { /* ignore */ }
  };

  const resumeDraft = () => {
    if (!draftPrompt) return;
    setForm({ ...EMPTY_FORM, ...(draftPrompt.form || {}) });
    // Clamp the resumed step to this scope's visible range.
    const savedStep = Number(draftPrompt.step) || firstStep;
    const clamped = visibleStepIds.includes(savedStep) ? savedStep : firstStep;
    setStep(clamped);
    setDraftPrompt(null);
  };

  const discardDraft = () => {
    clearDraft();
    setDraftPrompt(null);
    setForm(EMPTY_FORM);
    setStep(firstStep);
  };

  const goNext = () => {
    const errs = validateStep(step, form);
    if (Object.keys(errs).length) {
      setErrors(errs);
      toast.error("Please complete the required fields on this step.");
      return;
    }
    setErrors({});
    const idx = visibleStepIds.indexOf(step);
    if (idx < visibleStepIds.length - 1) setStep(visibleStepIds[idx + 1]);
  };

  const goBack = () => {
    setErrors({});
    const idx = visibleStepIds.indexOf(step);
    if (idx > 0) setStep(visibleStepIds[idx - 1]);
  };

  async function submit() {
    // Scope "patient" still enforces demographics + billing validation.
    // Scope "intake" fields (steps 3–4) have no hard validation — staff can
    // save partial intake and come back to it later.
    if (!isIntakeScope) {
      const errs = validateAll(form);
      if (Object.keys(errs).length) {
        setErrors(errs);
        const hasStep1 = Object.keys(errs).some((k) => validateStep(1, form)[k]);
        setStep(hasStep1 ? 1 : 2);
        toast.error("Some required fields are missing.");
        return;
      }
    }

    setSubmitting(true);
    try {
      const payload = buildPayload(form);
      let data;
      if (isEdit) {
        const resp = await api.patch(`/patients/${patientId}`, payload);
        data = resp.data;
        toast.success(isIntakeScope ? "Intake updated" : "Patient updated");
        onSaved && onSaved(data);
      } else {
        const resp = await api.post("/patients", payload);
        data = resp.data;
        toast.success(`Patient ${data.first_name} ${data.last_name} created`);
        clearDraft();
        onCreated && onCreated(data);
      }
      onClose();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  const visibleSteps = STEPS.filter((s) => visibleStepIds.includes(s.id));
  const currentIndex = visibleStepIds.indexOf(step);
  const current = STEPS.find((s) => s.id === step) || visibleSteps[0];
  const totalSteps = visibleSteps.length;

  const dialogTitle = isIntakeScope
    ? (isEdit ? "Edit intake" : "Start intake")
    : (isEdit ? "Edit patient" : "New patient");

  const saveLabel = isIntakeScope
    ? (isEdit ? "Save intake" : "Save intake")
    : (isEdit ? "Save changes" : "Save patient");

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !submitting && onClose()}>
      <DialogContent
        data-testid="patient-wizard-dialog"
        className="max-h-[92vh] max-w-5xl overflow-hidden rounded-sm border-border bg-background p-0"
      >
        <DialogHeader className="border-b border-border bg-card px-8 py-5">
          <DialogTitle className="font-display text-2xl font-medium tracking-tight text-foreground">
            {dialogTitle}
          </DialogTitle>
          <DialogDescription className="text-sm text-muted-foreground">
            Step {currentIndex + 1} of {totalSteps} — {current.label}. All PHI is encrypted at rest and every save is audited.
            {!isEdit && !isIntakeScope && (
              <span
                data-testid="wizard-draft-autosave-indicator"
                className={
                  "ml-3 text-xs " +
                  (draftNotice ? "text-primary opacity-100" : "opacity-0")
                }
                aria-live="polite"
              >
                Draft autosaved.
              </span>
            )}
          </DialogDescription>

          <ol
            className={
              "mt-4 grid gap-2 " +
              (totalSteps === 2 ? "grid-cols-2" : "grid-cols-4")
            }
            data-testid="wizard-steps"
          >
            {visibleSteps.map((s) => {
              const sIdx = visibleStepIds.indexOf(s.id);
              const state = sIdx < currentIndex ? "done" : s.id === step ? "active" : "todo";
              return (
                <li key={s.id} data-testid={`wizard-step-${s.id}`} data-state={state} className="flex items-start gap-3">
                  <div
                    className={
                      "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold " +
                      (state === "done"
                        ? "border-primary bg-primary text-primary-foreground"
                        : state === "active"
                        ? "border-foreground bg-card text-foreground"
                        : "border-border-strong bg-card text-muted-foreground/70")
                    }
                  >
                    {state === "done" ? <Check className="h-4 w-4" /> : s.id}
                  </div>
                  <div className="min-w-0">
                    <div
                      className={
                        "truncate text-sm font-medium " +
                        (state === "todo" ? "text-muted-foreground/70" : "text-foreground")
                      }
                    >
                      {s.label}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">{s.sub}</div>
                  </div>
                </li>
              );
            })}
          </ol>
        </DialogHeader>

        {draftPrompt && (
          <div
            data-testid="wizard-draft-prompt"
            className="flex items-center justify-between gap-4 border-b border-border bg-warning-soft px-8 py-3 text-sm text-warning"
          >
            <span>
              <strong>Unfinished draft found.</strong>{" "}
              Last saved {new Date(draftPrompt.savedAt).toLocaleString()}. Resume where you left off?
            </span>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={discardDraft}
                data-testid="wizard-draft-discard"
                className="rounded-sm text-warning hover:bg-warning-soft"
              >
                Discard
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={resumeDraft}
                data-testid="wizard-draft-resume"
                className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
              >
                Resume draft
              </Button>
            </div>
          </div>
        )}

        <div
          className="max-h-[56vh] overflow-y-auto px-8 py-6"
          data-testid={`wizard-step-body-${step}`}
        >
          {step === 1 && <StepPatientInfo form={form} set={set} errors={errors} />}
          {step === 2 && (
            <StepBillingInsurance
              form={form}
              set={set}
              errors={errors}
              providers={providers}
              locations={locations}
              visibility={visibility}
            />
          )}
          {step === 3 && <StepClinicalIntake form={form} set={set} />}
          {step === 4 && <StepCaseConsents form={form} set={set} visibility={visibility} />}
        </div>

        <DialogFooter className="flex flex-row items-center justify-between border-t border-border bg-card px-8 py-4 sm:justify-between">
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            disabled={submitting}
            className="rounded-sm"
            data-testid="wizard-cancel-btn"
          >
            Cancel
          </Button>
          <div className="flex items-center gap-3">
            <Button
              type="button"
              variant="ghost"
              onClick={goBack}
              disabled={currentIndex === 0 || submitting}
              className="rounded-sm text-primary hover:bg-primary/10"
              data-testid="wizard-back-btn"
            >
              <ChevronLeft className="mr-1 h-4 w-4" /> Back
            </Button>
            {step !== lastStep ? (
              <Button
                type="button"
                onClick={goNext}
                className="h-10 rounded-sm bg-primary px-5 hover:bg-[var(--primary-hover)]"
                data-testid="wizard-next-btn"
              >
                Next <ChevronRight className="ml-1 h-4 w-4" />
              </Button>
            ) : (
              <Button
                type="button"
                onClick={submit}
                disabled={submitting}
                className="h-10 rounded-sm bg-primary px-6 hover:bg-[var(--primary-hover)]"
                data-testid="wizard-save-btn"
              >
                {submitting ? "Saving…" : saveLabel}
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// -----------------------------------------------------------------------
// Page
// -----------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Recently-viewed patients — localStorage-backed utility.
// ---------------------------------------------------------------------------
const RECENT_KEY = "ccms.recentPatients";
const RECENT_LIMIT = 6;

function readRecent(userId) {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) || "{}");
    const list = Array.isArray(raw?.[userId]) ? raw[userId] : [];
    return list.slice(0, RECENT_LIMIT);
  } catch {
    return [];
  }
}

function pushRecent(userId, entry) {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) || "{}");
    const list = Array.isArray(raw?.[userId]) ? raw[userId] : [];
    const dedup = [entry, ...list.filter((x) => x?.id !== entry.id)].slice(0, RECENT_LIMIT);
    localStorage.setItem(RECENT_KEY, JSON.stringify({ ...raw, [userId]: dedup }));
  } catch {
    /* ignore quota errors */
  }
}

// ---------------------------------------------------------------------------
// Typeahead debounce.
// ---------------------------------------------------------------------------
function useDebouncedValue(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

// ---------------------------------------------------------------------------
// Result row highlighter — wraps matched substrings in <mark>.
// Supports the SQL `%` wildcard (treated as `.*`) and escapes the rest.
// ---------------------------------------------------------------------------
function escapeRegExp(str) {
  return String(str).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildHighlightRegex(term) {
  if (!term) return null;
  const t = term.trim();
  if (!t) return null;
  const placeholder = "\u0000WILDCARD\u0000";
  const safe = escapeRegExp(t.replace(/%/g, placeholder)).split(placeholder).join(".*?");
  try {
    return new RegExp(`(${safe})`, "gi");
  } catch {
    return null;
  }
}

function Highlight({ value, rx }) {
  if (!value) return <span>—</span>;
  const s = String(value);
  if (!rx) return <>{s}</>;
  const parts = s.split(rx);
  return (
    <>
      {parts.map((chunk, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded-sm bg-primary/25 px-0.5 text-foreground">
            {chunk}
          </mark>
        ) : (
          <span key={i}>{chunk}</span>
        )
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Patients — search-first lookup page.
// ---------------------------------------------------------------------------
export default function Patients() {
  const { user } = useAuth();
  const canCreate = STAFF_ROLES.includes(user.role);
  const [mode, setMode] = useState("global"); // 'global' | 'advanced'
  const [q, setQ] = useState("");
  const [fields, setFields] = useState({ name: "", phone: "", address: "", dob: "" });
  const [submittedAt, setSubmittedAt] = useState(0);
  const [results, setResults] = useState(null); // null = idle, [] = empty
  const [meta, setMeta] = useState({ total: 0, truncated: false });
  const [loading, setLoading] = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);
  const [open, setOpen] = useState(false);
  const [recent, setRecent] = useState(() => readRecent(user.id));
  const navigate = useNavigate();

  const debouncedQ = useDebouncedValue(q, 250);
  const debouncedFields = useDebouncedValue(fields, 300);

  const buildParams = useCallback(() => {
    if (mode === "global") return q.trim() ? { q: q.trim(), limit: 10 } : null;
    const active = Object.fromEntries(
      Object.entries(fields).filter(([, v]) => v.trim())
    );
    return Object.keys(active).length ? { ...active, limit: 25 } : null;
  }, [mode, q, fields]);

  const runSearch = useCallback(
    async (params, { typeahead = false } = {}) => {
      if (!params) {
        setResults(null);
        setMeta({ total: 0, truncated: false });
        return;
      }
      setLoading(true);
      try {
        const { data } = await api.get("/patients/search", { params });
        setResults(data.results);
        setMeta({ total: data.total, truncated: data.truncated_candidates });
        setActiveIdx(data.results.length ? 0 : -1);
      } catch (err) {
        if (!typeahead) toast.error(formatApiError(err));
        setResults([]);
        setMeta({ total: 0, truncated: false });
      } finally {
        setLoading(false);
      }
    },
    []
  );

  // Typeahead (lazy): runs on debounced input for quick global lookups only.
  // Advanced mode waits for an explicit submit to avoid scattershot requests.
  useEffect(() => {
    if (mode !== "global") return;
    const params = buildParams();
    if (!params || params.q.length < 2) {
      setResults(null);
      return;
    }
    runSearch(params, { typeahead: true });
  }, [debouncedQ, mode, buildParams, runSearch]);

  // Advanced submits only via button / Enter.
  useEffect(() => {
    if (mode !== "advanced" || submittedAt === 0) return;
    runSearch(buildParams(), { typeahead: false });
  }, [submittedAt, mode, buildParams, runSearch]);

  function onSubmit(e) {
    e?.preventDefault();
    setSubmittedAt(Date.now());
    if (mode === "global") runSearch(buildParams(), { typeahead: false });
  }

  function openPatient(row) {
    const entry = {
      id: row.id,
      display: row.display_name_masked || `${row.first_name || ""} ${row.last_name || ""}`.trim() || row.id.slice(0, 8),
      dob: row.date_of_birth,
      viewedAt: new Date().toISOString(),
    };
    pushRecent(user.id, entry);
    setRecent(readRecent(user.id));
    navigate(`/patients/${row.id}`);
  }

  function onKeyDown(e) {
    if (!results || !results.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(results.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter" && activeIdx >= 0) {
      e.preventDefault();
      openPatient(results[activeIdx]);
    }
  }

  const highlightRx = useMemo(() => {
    const term = mode === "global" ? q : (fields.name || fields.phone || fields.address || fields.dob);
    return buildHighlightRegex(term);
  }, [mode, q, fields]);

  const hasQuery = mode === "global" ? q.trim().length > 0 : Object.values(fields).some((v) => v.trim());

  return (
    <div data-testid="patients-page" className="space-y-8 animate-in fade-in duration-300">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">Patient lookup</span>
          <h1 className="mt-2 font-display text-4xl font-medium tracking-tight text-foreground">Find a patient</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Search by name, phone, address, or DOB. Use <code className="rounded-sm bg-muted px-1">%</code> as a wildcard (e.g., <code className="rounded-sm bg-muted px-1">Test%</code>). PHI is masked in results.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {canCreate && (
            <Button
              data-testid="patients-new-btn"
              onClick={() => setOpen(true)}
              className="h-11 rounded-sm bg-primary px-5 hover:bg-[var(--primary-hover)]"
            >
              <Plus className="mr-2 h-4 w-4" /> New patient
            </Button>
          )}
        </div>
      </header>

      <form onSubmit={onSubmit} onKeyDown={onKeyDown} className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div className="inline-flex rounded-sm border border-border bg-card p-0.5 text-xs font-semibold">
            <button
              type="button"
              data-testid="search-mode-global"
              onClick={() => setMode("global")}
              className={`px-3 py-1.5 rounded-sm uppercase tracking-wider ${mode === "global" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
            >
              Quick lookup
            </button>
            <button
              type="button"
              data-testid="search-mode-advanced"
              onClick={() => setMode("advanced")}
              className={`px-3 py-1.5 rounded-sm uppercase tracking-wider ${mode === "advanced" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
            >
              Advanced
            </button>
          </div>
          {meta.truncated && (
            <span data-testid="too-many-candidates" className="text-xs text-warning">
              Too many candidates — refine your search for complete results.
            </span>
          )}
        </div>

        {mode === "global" ? (
          <div className="relative">
            <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/70" />
            <Input
              data-testid="search-q"
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Test Patient · (555) 123-4567 · Test% · 01/15/1985"
              className="h-14 rounded-sm border-border pl-11 text-base"
            />
            {loading && (
              <div className="pointer-events-none absolute right-4 top-1/2 -translate-y-1/2">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-[color:var(--sage-accent)] border-t-transparent" />
              </div>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <AdvancedInput testid="search-name"    label="Name"    placeholder="Test Patient / Test%" value={fields.name}    onChange={(v) => setFields((f) => ({ ...f, name: v }))} />
            <AdvancedInput testid="search-phone"   label="Phone"   placeholder="5551234567"       value={fields.phone}   onChange={(v) => setFields((f) => ({ ...f, phone: v }))} />
            <AdvancedInput testid="search-address" label="Address" placeholder="Meadow / Portland"value={fields.address} onChange={(v) => setFields((f) => ({ ...f, address: v }))} />
            <AdvancedInput testid="search-dob"     label="DOB"     placeholder="01/15/1985"       value={fields.dob}     onChange={(v) => setFields((f) => ({ ...f, dob: v }))} />
            <div className="md:col-span-4 flex justify-end">
              <Button
                type="submit"
                data-testid="search-submit"
                className="h-10 rounded-sm bg-primary px-6 hover:bg-[var(--primary-hover)]"
              >
                <Search className="mr-2 h-4 w-4" /> Search
              </Button>
            </div>
          </div>
        )}
      </form>

      {/* Result list / recently viewed / empty state */}
      {hasQuery ? (
        <SearchResults
          results={results}
          loading={loading}
          activeIdx={activeIdx}
          meta={meta}
          onOpen={openPatient}
          highlightRx={highlightRx}
        />
      ) : (
        <RecentPatients recent={recent} onOpen={(r) => navigate(`/patients/${r.id}`)} canCreate={canCreate} />
      )}

      {canCreate && (
        <PatientWizardDialog
          open={open}
          onClose={() => setOpen(false)}
          onCreated={(p) => {
            pushRecent(user.id, { id: p.id, display: `${p.first_name || ""} ${p.last_name || ""}`.trim(), dob: p.date_of_birth, viewedAt: new Date().toISOString() });
            setRecent(readRecent(user.id));
            toast.success("Patient added to recently-viewed");
          }}
          userId={user.id}
          tenantId={user.tenant_id}
        />
      )}
    </div>
  );
}

function AdvancedInput({ testid, label, placeholder, value, onChange }) {
  return (
    <label className="flex flex-col gap-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
      <span>{label}</span>
      <Input
        data-testid={testid}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-10 rounded-sm border-border text-sm font-normal normal-case tracking-normal text-foreground"
      />
    </label>
  );
}

function SearchResults({ results, loading, activeIdx, meta, onOpen, highlightRx }) {
  if (results === null && loading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-14 rounded-sm" />)}
      </div>
    );
  }
  if (results === null) return null;
  if (results.length === 0) {
    return (
      <div data-testid="search-empty" className="rounded-sm border border-dashed border-border bg-card p-12 text-center">
        <User2 className="mx-auto h-10 w-10 text-muted-foreground/70" />
        <p className="mt-4 font-display text-lg text-foreground">No matching patients</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Try a different name, phone, address, or DOB. Wildcard <code className="rounded-sm bg-muted px-1">%</code> is supported.
        </p>
      </div>
    );
  }
  return (
    <div data-testid="search-results" className="overflow-hidden rounded-sm border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border bg-background px-4 py-2 text-xs uppercase tracking-wider text-muted-foreground">
        <span>{meta.total} result{meta.total === 1 ? "" : "s"}</span>
        <span className="text-[11px]">Use ↑↓ + Enter</span>
      </div>
      <ul role="listbox">
        {results.map((r, idx) => (
          <li
            key={r.id}
            role="option"
            aria-selected={idx === activeIdx}
            data-testid={`search-result-${r.id}`}
            onClick={() => onOpen(r)}
            onMouseEnter={() => {}}
            className={`cursor-pointer border-b border-border last:border-b-0 px-4 py-3 text-sm transition-colors ${
              idx === activeIdx ? "bg-primary/10" : "hover:bg-muted"
            }`}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 text-foreground">
                  <span className="font-medium">
                    <Highlight
                      value={r.display_name_masked || `${r.first_name || ""} ${r.last_name || ""}`.trim() || "—"}
                      rx={highlightRx}
                    />
                  </span>
                  {r.status === "deleted" && (
                    <span className="rounded-sm bg-destructive-soft px-1.5 py-0.5 text-[10px] font-semibold uppercase text-destructive">deleted</span>
                  )}
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  <Highlight value={`DOB ${r.date_of_birth || "—"}`} rx={highlightRx} />
                  <span className="mx-2 text-muted-foreground/70">·</span>
                  <Highlight value={r.primary_phone || "—"} rx={highlightRx} />
                  <span className="mx-2 text-muted-foreground/70">·</span>
                  <Highlight value={r.address_summary || "—"} rx={highlightRx} />
                </div>
              </div>
              <ChevronRight className="h-4 w-4 text-muted-foreground/70" />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RecentPatients({ recent, onOpen, canCreate }) {
  if (!recent.length) {
    return (
      <div data-testid="patients-empty-hero" className="rounded-sm border border-dashed border-border bg-card p-16 text-center">
        <Search className="mx-auto h-10 w-10 text-muted-foreground/70" />
        <p className="mt-4 font-display text-lg text-foreground">Search to find a patient</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Try <code className="rounded-sm bg-muted px-1">Test Patient</code>, <code className="rounded-sm bg-muted px-1">5551234567</code>, <code className="rounded-sm bg-muted px-1">01/15/1985</code>, or a wildcard like <code className="rounded-sm bg-muted px-1">Test%</code>.
          {canCreate && " New patient? Use the button above."}
        </p>
      </div>
    );
  }
  return (
    <section data-testid="recent-patients" className="space-y-3">
      <h2 className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">Recently viewed</h2>
      <ul className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
        {recent.map((r) => (
          <li key={r.id}>
            <button
              type="button"
              data-testid={`recent-${r.id}`}
              onClick={() => onOpen(r)}
              className="group flex w-full items-center justify-between rounded-sm border border-border bg-card px-4 py-3 text-left transition-colors hover:bg-muted"
            >
              <div>
                <div className="font-medium text-foreground">{r.display || r.id.slice(0, 8)}</div>
                <div className="text-xs text-muted-foreground">DOB {r.dob || "—"}</div>
              </div>
              <ChevronRight className="h-4 w-4 text-muted-foreground/70 group-hover:text-primary" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
