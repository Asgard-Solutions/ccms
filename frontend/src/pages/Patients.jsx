import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
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
};

// -----------------------------------------------------------------------
// Small field helpers
// -----------------------------------------------------------------------

function Field({ label, htmlFor, required, error, errorTestId, children, className = "" }) {
  return (
    <div className={`space-y-1.5 ${className}`}>
      <Label htmlFor={htmlFor} className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wider text-[#5C6A61]">
        {label}
        {required && <span className="text-[#C76D54]" aria-label="required">*</span>}
      </Label>
      {children}
      {error && (
        <p className="text-xs text-[#C76D54]" data-testid={errorTestId || `${htmlFor}-error`}>{error}</p>
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
      className="h-10 rounded-sm border-stone-300 bg-white text-sm"
    />
  );
}

function SelectField({ id, value, onChange, options, placeholder = "Select…", testId }) {
  return (
    <Select value={value || undefined} onValueChange={(v) => onChange(v)}>
      <SelectTrigger id={id} data-testid={testId} className="h-10 rounded-sm border-stone-300 bg-white text-sm">
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
        "flex items-start gap-3 rounded-sm border border-stone-200 bg-white px-3 py-2.5 text-sm " +
        (disabled
          ? "cursor-not-allowed text-[#A3AFA7]"
          : "cursor-pointer text-[#1F2924] hover:border-[#7B9A82]")
      }
    >
      <Checkbox
        id={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={(v) => onChange(Boolean(v))}
        data-testid={testId}
        className="mt-0.5 border-stone-400 data-[state=checked]:border-[#7B9A82] data-[state=checked]:bg-[#7B9A82]"
      />
      <span className="leading-snug">{label}</span>
    </label>
  );
}

function SectionTitle({ children, hint }) {
  return (
    <div className="col-span-full mt-2 mb-1 border-b border-stone-200 pb-1">
      <h3 className="font-['Outfit'] text-base font-medium text-[#1F2924]">{children}</h3>
      {hint && <p className="mt-0.5 text-xs text-[#5C6A61]">{hint}</p>}
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
            className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1 text-sm text-[#1F2924] hover:bg-[#EDF2EE]"
          >
            <Checkbox
              id={id}
              checked={checked}
              onCheckedChange={() => toggle(opt)}
              data-testid={`${testId}-opt-${opt.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`}
              className="border-stone-400 data-[state=checked]:border-[#7B9A82] data-[state=checked]:bg-[#7B9A82]"
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
          <div className="col-span-full -mb-1 mt-2 text-xs font-semibold uppercase tracking-wider text-[#5C6A61]">
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

          <div className="col-span-full -mb-1 mt-4 text-xs font-semibold uppercase tracking-wider text-[#5C6A61]">
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
          className="min-h-[90px] rounded-sm border-stone-300 bg-white text-sm"
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
          className="min-h-[70px] rounded-sm border-stone-300 bg-white text-sm"
          value={form.priorTreatment} onChange={(e) => set("priorTreatment")(e.target.value)} />
      </Field>
      <Field label="Current medications" htmlFor="medications" className="col-span-full">
        <Textarea id="medications" data-testid="w-medications"
          className="min-h-[60px] rounded-sm border-stone-300 bg-white text-sm"
          value={form.medications} onChange={(e) => set("medications")(e.target.value)} />
      </Field>
      <Field label="Allergies" htmlFor="allergies" className="col-span-full">
        <TextInput id="allergies" testId="w-allergies" value={form.allergies} onChange={set("allergies")} placeholder="penicillin, latex…" />
      </Field>
      <Field label="Prior surgeries" htmlFor="surgeries" className="col-span-full">
        <Textarea id="surgeries" data-testid="w-surgeries"
          className="min-h-[60px] rounded-sm border-stone-300 bg-white text-sm"
          value={form.surgeries} onChange={(e) => set("surgeries")(e.target.value)} />
      </Field>
      <Field label="Past medical history" htmlFor="medicalHistory" className="col-span-full">
        <Textarea id="medicalHistory" data-testid="w-medical-history"
          className="min-h-[70px] rounded-sm border-stone-300 bg-white text-sm"
          value={form.medicalHistory} onChange={(e) => set("medicalHistory")(e.target.value)} />
      </Field>
      <Field label="Provider notes (internal)" htmlFor="providerNotes" className="col-span-full">
        <Textarea id="providerNotes" data-testid="w-provider-notes"
          className="min-h-[70px] rounded-sm border-stone-300 bg-white text-sm"
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
          className="col-span-full rounded-sm border border-dashed border-stone-300 bg-white px-5 py-6 text-sm text-[#5C6A61]"
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
  patientId,
  initialForm,
  userId,
  tenantId,
}) {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [providers, setProviders] = useState([]);
  const [locations, setLocations] = useState([]);
  const [draftPrompt, setDraftPrompt] = useState(null); // {savedAt, form}
  const [draftNotice, setDraftNotice] = useState(false); // "Draft saved"

  const visibility = useMemo(() => visibilityForForm(form), [form]);
  const isEdit = mode === "edit";
  const draftKey = useMemo(
    () => (isEdit ? null : draftStorageKey(userId, tenantId)),
    [isEdit, userId, tenantId]
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
      setStep(1);
      setForm({ ...EMPTY_FORM, ...(initialForm || {}) });
      setDraftPrompt(null);
    } else {
      // Start from a clean form unless the user decides to resume.
      setForm(EMPTY_FORM);
      setStep(1);
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
  }, [open, isEdit, initialForm, draftKey]);

  const clearDraft = () => {
    if (!draftKey) return;
    try { window.localStorage.removeItem(draftKey); } catch { /* ignore */ }
  };

  const resumeDraft = () => {
    if (!draftPrompt) return;
    setForm({ ...EMPTY_FORM, ...(draftPrompt.form || {}) });
    setStep(Math.min(4, Math.max(1, Number(draftPrompt.step) || 1)));
    setDraftPrompt(null);
  };

  const discardDraft = () => {
    clearDraft();
    setDraftPrompt(null);
    setForm(EMPTY_FORM);
    setStep(1);
  };

  const goNext = () => {
    const errs = validateStep(step, form);
    if (Object.keys(errs).length) {
      setErrors(errs);
      toast.error("Please complete the required fields on this step.");
      return;
    }
    setErrors({});
    setStep((s) => Math.min(4, s + 1));
  };

  const goBack = () => {
    setErrors({});
    setStep((s) => Math.max(1, s - 1));
  };

  async function submit() {
    const errs = validateAll(form);
    if (Object.keys(errs).length) {
      setErrors(errs);
      const hasStep1 = Object.keys(errs).some((k) => validateStep(1, form)[k]);
      setStep(hasStep1 ? 1 : 2);
      toast.error("Some required fields are missing.");
      return;
    }

    setSubmitting(true);
    try {
      const payload = buildPayload(form);
      let data;
      if (isEdit) {
        const resp = await api.put(`/patients/${patientId}`, payload);
        data = resp.data;
        toast.success("Patient intake updated");
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

  const current = STEPS.find((s) => s.id === step);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !submitting && onClose()}>
      <DialogContent
        data-testid="patient-wizard-dialog"
        className="max-h-[92vh] max-w-5xl overflow-hidden rounded-sm border-stone-200 bg-[#FAF9F6] p-0"
      >
        <DialogHeader className="border-b border-stone-200 bg-white px-8 py-5">
          <DialogTitle className="font-['Outfit'] text-2xl font-medium tracking-tight text-[#1F2924]">
            {isEdit ? "Edit patient intake" : "New patient intake"}
          </DialogTitle>
          <DialogDescription className="text-sm text-[#5C6A61]">
            Step {step} of 4 — {current.label}. All PHI is encrypted at rest and every save is audited.
            {!isEdit && (
              <span
                data-testid="wizard-draft-autosave-indicator"
                className={
                  "ml-3 text-xs " +
                  (draftNotice ? "text-[#7B9A82] opacity-100" : "opacity-0")
                }
                aria-live="polite"
              >
                Draft autosaved.
              </span>
            )}
          </DialogDescription>

          <ol className="mt-4 grid grid-cols-4 gap-2" data-testid="wizard-steps">
            {STEPS.map((s) => {
              const state = s.id < step ? "done" : s.id === step ? "active" : "todo";
              return (
                <li key={s.id} data-testid={`wizard-step-${s.id}`} data-state={state} className="flex items-start gap-3">
                  <div
                    className={
                      "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold " +
                      (state === "done"
                        ? "border-[#7B9A82] bg-[#7B9A82] text-white"
                        : state === "active"
                        ? "border-[#1F2924] bg-white text-[#1F2924]"
                        : "border-stone-300 bg-white text-[#A3AFA7]")
                    }
                  >
                    {state === "done" ? <Check className="h-4 w-4" /> : s.id}
                  </div>
                  <div className="min-w-0">
                    <div
                      className={
                        "truncate text-sm font-medium " +
                        (state === "todo" ? "text-[#A3AFA7]" : "text-[#1F2924]")
                      }
                    >
                      {s.label}
                    </div>
                    <div className="truncate text-xs text-[#5C6A61]">{s.sub}</div>
                  </div>
                </li>
              );
            })}
          </ol>
        </DialogHeader>

        {draftPrompt && (
          <div
            data-testid="wizard-draft-prompt"
            className="flex items-center justify-between gap-4 border-b border-[#E5D9A4] bg-[#FAF5DC] px-8 py-3 text-sm text-[#5B4F1F]"
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
                className="rounded-sm text-[#5B4F1F] hover:bg-[#F1E7B0]"
              >
                Discard
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={resumeDraft}
                data-testid="wizard-draft-resume"
                className="rounded-sm bg-[#7B9A82] hover:bg-[#65826C]"
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

        <DialogFooter className="flex flex-row items-center justify-between border-t border-stone-200 bg-white px-8 py-4 sm:justify-between">
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
              disabled={step === 1 || submitting}
              className="rounded-sm text-[#526B58] hover:bg-[#EDF2EE]"
              data-testid="wizard-back-btn"
            >
              <ChevronLeft className="mr-1 h-4 w-4" /> Back
            </Button>
            {step < 4 ? (
              <Button
                type="button"
                onClick={goNext}
                className="h-10 rounded-sm bg-[#7B9A82] px-5 hover:bg-[#65826C]"
                data-testid="wizard-next-btn"
              >
                Next <ChevronRight className="ml-1 h-4 w-4" />
              </Button>
            ) : (
              <Button
                type="button"
                onClick={submit}
                disabled={submitting}
                className="h-10 rounded-sm bg-[#7B9A82] px-6 hover:bg-[#65826C]"
                data-testid="wizard-save-btn"
              >
                {submitting ? "Saving…" : isEdit ? "Save changes" : "Save patient"}
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

export default function Patients() {
  const { user } = useAuth();
  const canCreate = STAFF_ROLES.includes(user.role);
  const canUnmask = user.role === "admin";
  const [patients, setPatients] = useState(null);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [unmask, setUnmask] = useState(false);

  async function load(u = unmask) {
    setPatients(null);
    try {
      const { data } = await api.get("/patients", {
        params: u ? { unmask: true } : {},
      });
      setPatients(data);
    } catch {
      setPatients([]);
    }
  }

  useEffect(() => {
    load(unmask);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unmask]);

  const filtered = useMemo(() => {
    if (!patients) return null;
    const q = search.trim().toLowerCase();
    if (!q) return patients;
    return patients.filter((p) =>
      [p.first_name, p.last_name, p.email, p.phone]
        .filter(Boolean)
        .some((v) => v.toString().toLowerCase().includes(q))
    );
  }, [patients, search]);

  return (
    <div data-testid="patients-page" className="space-y-8 animate-in fade-in duration-300">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">Patient directory</span>
          <h1 className="mt-2 font-['Outfit'] text-4xl font-medium tracking-tight text-[#1F2924]">Patients</h1>
          <p className="mt-2 text-sm text-[#5C6A61]">
            PHI is masked by default. Administrators can unmask; every unmasked view is audited.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {canUnmask && (
            <Button
              variant="outline"
              onClick={() => setUnmask((u) => !u)}
              data-testid="patients-unmask-toggle"
              className="rounded-sm"
            >
              {unmask ? <EyeOff className="mr-2 h-4 w-4" /> : <Eye className="mr-2 h-4 w-4" />}
              {unmask ? "Mask PHI" : "Unmask (audited)"}
            </Button>
          )}
          {canCreate && (
            <Button
              data-testid="patients-new-btn"
              onClick={() => setOpen(true)}
              className="h-11 rounded-sm bg-[#7B9A82] px-5 hover:bg-[#65826C]"
            >
              <Plus className="mr-2 h-4 w-4" /> New patient
            </Button>
          )}
        </div>
      </header>

      <div className="relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#A3AFA7]" />
        <Input
          data-testid="patients-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name…"
          className="h-11 rounded-sm border-stone-200 pl-9"
        />
      </div>

      {filtered === null ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-16 rounded-sm" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-sm border border-dashed border-stone-200 bg-white p-16 text-center">
          <User2 className="mx-auto h-10 w-10 text-[#A3AFA7]" />
          <p className="mt-4 font-['Outfit'] text-lg text-[#1F2924]">
            No patients {search ? "match your search" : "yet"}
          </p>
          <p className="mt-1 text-sm text-[#5C6A61]">
            {canCreate && !search && "Start by creating your first patient record."}
          </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-sm border border-stone-200 bg-white">
          <table className="w-full text-left">
            <thead className="border-b border-stone-200 bg-[#FAF9F6]">
              <tr className="text-xs font-semibold uppercase tracking-wider text-[#5C6A61]">
                <th className="px-6 py-3">Name</th>
                <th className="px-6 py-3">Contact</th>
                <th className="px-6 py-3">DOB</th>
                <th className="px-6 py-3">Added</th>
                <th className="px-6 py-3" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((p) => (
                <tr
                  key={p.id}
                  data-testid={`patient-row-${p.id}`}
                  className="border-b border-stone-100 last:border-b-0 hover:bg-[#F5F5F0]/50"
                >
                  <td className="px-6 py-4">
                    <div className="font-medium text-[#1F2924]">
                      {p.unmasked ? `${p.first_name} ${p.last_name}` : p.display_name_masked || "—"}
                    </div>
                    <div className="text-xs text-[#5C6A61]">
                      {p.gender || "—"}
                      {p.status === "deleted" && (
                        <span className="ml-2 rounded-sm bg-[#FBF1EE] px-1.5 py-0.5 text-[10px] font-semibold uppercase text-[#C76D54]">
                          deleted
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-6 py-4 text-sm text-[#5C6A61]">
                    <div>{p.email || "—"}</div>
                    <div className="text-xs">{p.phone || "—"}</div>
                  </td>
                  <td className="px-6 py-4 text-sm text-[#5C6A61]">
                    {p.date_of_birth ? (p.unmasked ? formatDate(p.date_of_birth) : p.date_of_birth) : "—"}
                  </td>
                  <td className="px-6 py-4 text-sm text-[#5C6A61]">{formatDate(p.created_at)}</td>
                  <td className="px-6 py-4 text-right">
                    <Button variant="ghost" asChild className="text-[#526B58] hover:bg-[#EDF2EE]">
                      <Link to={`/patients/${p.id}`} data-testid={`patient-open-${p.id}`}>Open</Link>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {canCreate && (
        <PatientWizardDialog
          open={open}
          onClose={() => setOpen(false)}
          onCreated={(p) => setPatients((xs) => [p, ...(xs || [])])}
          userId={user.id}
          tenantId={user.tenant_id}
        />
      )}
    </div>
  );
}
