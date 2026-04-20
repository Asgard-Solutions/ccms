/**
 * Pure-JS business logic for the New Patient intake wizard.
 *
 * Exported as CommonJS so it works unchanged under both Create-React-App's
 * Webpack/Babel pipeline and Node's built-in `--test` runner (Node 20+).
 *
 * Keep this module free of React / DOM / i18n helpers so the tests can
 * exercise it directly.
 */

// ---------------------------------------------------------------------------
// Chiropractic-specific option lists (used by the wizard UI AND validated by
// the Node test suite as a regression against accidental breakage).
// ---------------------------------------------------------------------------

const PAIN_AREA_OPTIONS = [
  "Neck",
  "Upper back",
  "Mid back",
  "Lower back",
  "Left shoulder",
  "Right shoulder",
  "Left arm",
  "Right arm",
  "Left elbow",
  "Right elbow",
  "Left wrist / hand",
  "Right wrist / hand",
  "Left hip",
  "Right hip",
  "Left knee",
  "Right knee",
  "Left foot / ankle",
  "Right foot / ankle",
  "Jaw (TMJ)",
  "Headache / head",
  "Sciatica — left",
  "Sciatica — right",
  "Coccyx / tailbone",
];

const SYMPTOM_OPTIONS = [
  "Sharp / stabbing pain",
  "Dull / aching pain",
  "Burning pain",
  "Throbbing pain",
  "Stiffness",
  "Muscle spasm",
  "Numbness",
  "Tingling",
  "Weakness",
  "Radiating pain",
  "Limited range of motion",
  "Swelling",
  "Clicking / popping",
  "Muscle cramps",
  "Dizziness / vertigo",
  "Sleep disturbance",
];

const ONSET_TYPE_OPTIONS = [
  { value: "trauma", label: "Trauma / injury" },
  { value: "sudden", label: "Sudden (non-traumatic)" },
  { value: "gradual", label: "Gradual onset over time" },
  { value: "repetitive_strain", label: "Repetitive strain" },
  { value: "post_surgical", label: "Post-surgical" },
  { value: "recurring", label: "Recurring / flare-up" },
  { value: "unknown", label: "Unknown" },
];

// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------

function cleanStr(v) {
  if (v === undefined || v === null) return undefined;
  const s = String(v).trim();
  return s === "" ? undefined : s;
}

function compactObj(obj) {
  const out = {};
  Object.entries(obj).forEach(([k, v]) => {
    if (v === undefined) return;
    if (typeof v === "string" && v.trim() === "") return;
    if (Array.isArray(v) && v.length === 0) return;
    out[k] = v;
  });
  return Object.keys(out).length ? out : undefined;
}

function splitName(full) {
  const parts = (cleanStr(full) || "").split(/\s+/).filter(Boolean);
  if (!parts.length) return { first_name: undefined, last_name: undefined };
  if (parts.length === 1) return { first_name: parts[0], last_name: undefined };
  return { first_name: parts[0], last_name: parts.slice(1).join(" ") };
}

function csvToList(v) {
  const s = cleanStr(v);
  if (!s) return [];
  return s.split(",").map((x) => x.trim()).filter(Boolean);
}

function mergeList(checked, other) {
  const extras = csvToList(other);
  const merged = [...(checked || []), ...extras];
  // Preserve order + de-dup case-insensitively.
  const seen = new Set();
  const out = [];
  merged.forEach((x) => {
    const k = x.toLowerCase();
    if (!seen.has(k)) {
      seen.add(k);
      out.push(x);
    }
  });
  return out;
}

// ---------------------------------------------------------------------------
// Format validators — "reasonable" regexes. Intentionally permissive so we
// accept international phone numbers, plus-prefixed, and extension suffixes.
// ---------------------------------------------------------------------------

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function isValidEmail(v) {
  const s = cleanStr(v);
  if (!s) return false;
  return EMAIL_RE.test(s);
}

function isValidPhone(v) {
  const s = cleanStr(v);
  if (!s) return false;
  const digits = s.replace(/\D/g, "");
  return digits.length >= 7 && digits.length <= 15;
}

function isValidPostal(v) {
  const s = cleanStr(v);
  if (!s) return false;
  // US ZIP or ZIP+4 primarily; also accept 3-10 alphanumeric for non-US.
  if (/^\d{5}(-\d{4})?$/.test(s)) return true;
  return /^[A-Za-z0-9 \-]{3,10}$/.test(s);
}

// ---------------------------------------------------------------------------
// Date helpers — all DOB logic lives here so tests can stub the reference
// "today" by calling the `_compute*` variants directly.
// ---------------------------------------------------------------------------

function _parseDateOnly(value) {
  const s = cleanStr(value);
  if (!s) return null;
  // Expect YYYY-MM-DD; fall back to Date parser for resiliency.
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (m) {
    const y = Number(m[1]);
    const mo = Number(m[2]);
    const d = Number(m[3]);
    return new Date(Date.UTC(y, mo - 1, d));
  }
  const dt = new Date(s);
  return Number.isNaN(dt.getTime()) ? null : dt;
}

function isFutureDate(value, _today = new Date()) {
  const d = _parseDateOnly(value);
  if (!d) return false;
  const today = new Date(Date.UTC(_today.getUTCFullYear(), _today.getUTCMonth(), _today.getUTCDate()));
  return d.getTime() > today.getTime();
}

function computeAge(dob, _today = new Date()) {
  const d = _parseDateOnly(dob);
  if (!d) return null;
  let age = _today.getUTCFullYear() - d.getUTCFullYear();
  const m = _today.getUTCMonth() - d.getUTCMonth();
  if (m < 0 || (m === 0 && _today.getUTCDate() < d.getUTCDate())) age -= 1;
  return age;
}

function isMinor(dob, _today = new Date()) {
  const age = computeAge(dob, _today);
  if (age === null) return false;
  return age >= 0 && age < 18;
}

// ---------------------------------------------------------------------------
// Conditional visibility — the single source of truth for the UI and the
// validator. If you change a rule here, update the corresponding test.
// ---------------------------------------------------------------------------

function visibilityForForm(form, _today = new Date()) {
  const minor = isMinor(form.dateOfBirth, _today);
  const responsiblePartyDiffers = !form.responsiblePartySameAsPatient;

  // Minors cannot be their own guarantor — we force the guarantor section
  // on whenever the patient is a minor.
  const showGuarantor = minor || responsiblePartyDiffers;
  const requireGuarantor = minor && responsiblePartyDiffers;

  return {
    isMinor: minor,
    showGuarantor,
    requireGuarantor,
    // Insurance is wholly gated by the toggle.
    showInsurance: Boolean(form.hasInsurance),
    // Step 4 blocks
    showAccident: Boolean(form.accidentRelated),
    showWorkComp: Boolean(form.workComp),
    showPersonalInjury: Boolean(form.personalInjury),
    // Consents section is always shown.
    showConsents: true,
  };
}

// ---------------------------------------------------------------------------
// Validation — returns a `{ field: message }` map (empty means step passes).
// Only validates fields that are VISIBLE given the current form state.
// ---------------------------------------------------------------------------

const STEP1_REQUIRED_BASE = {
  firstName: "First name is required",
  lastName: "Last name is required",
  dateOfBirth: "Date of birth is required",
  mobilePhone: "Mobile phone is required",
  addressLine1: "Street address is required",
  city: "City is required",
  state: "State is required",
  postalCode: "Postal code is required",
  emergencyContactName: "Emergency contact name is required",
  emergencyContactRelationship: "Relationship is required",
  emergencyContactPhone: "Emergency contact phone is required",
};

const STEP2_REQUIRED_BASE = {
  assignedProviderId: "Assigned provider is required",
};

function validateStep(step, form, _today = new Date()) {
  const errors = {};
  const vis = visibilityForForm(form, _today);

  if (step === 1) {
    Object.entries(STEP1_REQUIRED_BASE).forEach(([k, msg]) => {
      if (!cleanStr(form[k])) errors[k] = msg;
    });

    // DOB — must not be in the future.
    if (!errors.dateOfBirth && isFutureDate(form.dateOfBirth, _today)) {
      errors.dateOfBirth = "Date of birth cannot be in the future";
    }

    // Format validators — only enforced when a value is present.
    if (cleanStr(form.email) && !isValidEmail(form.email)) {
      errors.email = "Enter a valid email address";
    }
    if (!errors.mobilePhone && cleanStr(form.mobilePhone) && !isValidPhone(form.mobilePhone)) {
      errors.mobilePhone = "Enter a valid phone number";
    }
    if (cleanStr(form.homePhone) && !isValidPhone(form.homePhone)) {
      errors.homePhone = "Enter a valid phone number";
    }
    if (cleanStr(form.workPhone) && !isValidPhone(form.workPhone)) {
      errors.workPhone = "Enter a valid phone number";
    }
    if (!errors.postalCode && cleanStr(form.postalCode) && !isValidPostal(form.postalCode)) {
      errors.postalCode = "Enter a valid postal code";
    }
    if (!errors.emergencyContactPhone && cleanStr(form.emergencyContactPhone) && !isValidPhone(form.emergencyContactPhone)) {
      errors.emergencyContactPhone = "Enter a valid phone number";
    }
    if (cleanStr(form.emergencyContactAltPhone) && !isValidPhone(form.emergencyContactAltPhone)) {
      errors.emergencyContactAltPhone = "Enter a valid phone number";
    }
    if (cleanStr(form.emergencyContactEmail) && !isValidEmail(form.emergencyContactEmail)) {
      errors.emergencyContactEmail = "Enter a valid email address";
    }
  }

  if (step === 2) {
    Object.entries(STEP2_REQUIRED_BASE).forEach(([k, msg]) => {
      if (!cleanStr(form[k])) errors[k] = msg;
    });

    // Guarantor requiredness — only when both the visibility AND the
    // require-flag say so (minor + responsible party differs).
    if (vis.requireGuarantor) {
      if (!cleanStr(form.guarantorFullName)) errors.guarantorFullName = "Guarantor full name is required for minors";
      if (!cleanStr(form.guarantorRelationship)) errors.guarantorRelationship = "Guarantor relationship is required";
      if (!cleanStr(form.guarantorPhone)) errors.guarantorPhone = "Guarantor phone is required";
      if (!errors.guarantorPhone && !isValidPhone(form.guarantorPhone)) {
        errors.guarantorPhone = "Enter a valid phone number";
      }
      if (cleanStr(form.guarantorEmail) && !isValidEmail(form.guarantorEmail)) {
        errors.guarantorEmail = "Enter a valid email address";
      }
    }

    // When hasInsurance is off we don't validate any insurance fields —
    // they're not even visible.
  }

  return errors;
}

function validateAll(form, _today = new Date()) {
  return { ...validateStep(1, form, _today), ...validateStep(2, form, _today) };
}

// ---------------------------------------------------------------------------
// Wizard form → backend grouped payload
// ---------------------------------------------------------------------------

function deriveCaseType(f) {
  if (f.personalInjury) return "personal_injury";
  if (f.workComp) return "workers_comp";
  if (f.accidentRelated) return "auto_accident";
  return undefined;
}

function buildPayload(form, _today = new Date()) {
  const f = form;
  const vis = visibilityForForm(f, _today);

  const demographics = compactObj({
    first_name: cleanStr(f.firstName),
    middle_name: cleanStr(f.middleName),
    last_name: cleanStr(f.lastName),
    preferred_name: cleanStr(f.preferredName),
    date_of_birth: cleanStr(f.dateOfBirth),
    gender: cleanStr(f.genderIdentity),
    sex_at_birth: cleanStr(f.sexAtBirth),
    pronouns: cleanStr(f.pronouns),
    marital_status: cleanStr(f.maritalStatus),
    language: cleanStr(f.preferredLanguage),
    occupation: cleanStr(f.occupation),
    employer: cleanStr(f.employerName),
    employer_phone: cleanStr(f.employerPhone),
  });

  const contact = compactObj({
    phone: cleanStr(f.mobilePhone),
    phone_alt: cleanStr(f.homePhone),
    phone_work: cleanStr(f.workPhone),
    email: cleanStr(f.email),
    preferred_contact_method: cleanStr(f.preferredContactMethod),
    sms_consent: f.smsConsent || undefined,
    email_consent: f.emailConsent || undefined,
    voicemail_consent: f.voicemailConsent || undefined,
  });

  const address = compactObj({
    line1: cleanStr(f.addressLine1),
    line2: cleanStr(f.addressLine2),
    city: cleanStr(f.city),
    state: cleanStr(f.state),
    postal_code: cleanStr(f.postalCode),
    country: cleanStr(f.country),
  });

  const emergencyContact = compactObj({
    name: cleanStr(f.emergencyContactName),
    relationship: cleanStr(f.emergencyContactRelationship),
    phone: cleanStr(f.emergencyContactPhone),
    phone_alt: cleanStr(f.emergencyContactAltPhone),
    email: cleanStr(f.emergencyContactEmail),
  });

  const admin = compactObj({
    primary_provider_id: cleanStr(f.assignedProviderId),
    referral_source: cleanStr(f.referralSource),
  });

  // Guarantor: if the guarantor block is hidden, record only the
  // same_as_patient=true marker. If visible, persist structured fields.
  let guarantor;
  if (!vis.showGuarantor) {
    guarantor = { same_as_patient: true };
  } else {
    const gName = splitName(f.guarantorFullName);
    guarantor = compactObj({
      same_as_patient: false,
      first_name: gName.first_name,
      last_name: gName.last_name,
      relationship: cleanStr(f.guarantorRelationship),
      date_of_birth: cleanStr(f.guarantorDateOfBirth),
      phone: cleanStr(f.guarantorPhone),
      email: cleanStr(f.guarantorEmail),
      address: cleanStr(f.guarantorAddress),
      employer: cleanStr(f.guarantorEmployerName),
      employer_phone: cleanStr(f.guarantorEmployerPhone),
    });
    if (!guarantor) guarantor = { same_as_patient: false };
  }

  // Insurance: only emitted when the toggle is on.
  let insurance;
  if (vis.showInsurance) {
    const primary = compactObj({
      carrier: cleanStr(f.primaryCarrier),
      plan_name: cleanStr(f.primaryPlanName),
      plan_type: cleanStr(f.primaryPlanType),
      member_id: cleanStr(f.primaryMemberId),
      group_number: cleanStr(f.primaryGroupNumber),
      policy_holder_name: cleanStr(f.primaryPolicyHolderName),
      policy_holder_relationship: cleanStr(f.primaryPolicyHolderRelationship),
      policy_holder_dob: cleanStr(f.primaryPolicyHolderDob),
      effective_date: cleanStr(f.primaryEffectiveDate),
      copay: cleanStr(f.primaryCopay),
      deductible: cleanStr(f.primaryDeductible),
    });
    const secondary = compactObj({
      carrier: cleanStr(f.secondaryCarrier),
      plan_name: cleanStr(f.secondaryPlanName),
      plan_type: cleanStr(f.secondaryPlanType),
      member_id: cleanStr(f.secondaryMemberId),
      group_number: cleanStr(f.secondaryGroupNumber),
      policy_holder_name: cleanStr(f.secondaryPolicyHolderName),
      policy_holder_relationship: cleanStr(f.secondaryPolicyHolderRelationship),
    });
    insurance = compactObj({ primary, secondary });
  }

  const painList = mergeList(f.painAreas, f.painAreasOther);
  const symptomList = mergeList(f.symptoms, f.symptomsOther);

  const clinical_intake = compactObj({
    chief_complaint: cleanStr(f.chiefComplaint),
    complaint_onset: cleanStr(f.symptomStartDate),
    onset_type: cleanStr(f.onsetType),
    pain_level:
      cleanStr(f.painScore) !== undefined && !Number.isNaN(Number(f.painScore))
        ? Math.max(0, Math.min(10, Number(f.painScore)))
        : undefined,
    pain_locations: painList.length ? painList : undefined,
    symptoms: symptomList.length ? symptomList : undefined,
    prior_treatments: cleanStr(f.priorTreatment),
    medications: cleanStr(f.medications),
    allergies: cleanStr(f.allergies),
    past_surgical_history: cleanStr(f.surgeries),
    past_medical_history: cleanStr(f.medicalHistory),
    notes: cleanStr(f.providerNotes),
  });

  // Case details — only include the subsets of fields whose flag is set.
  // This keeps stored records clean (no empty `attorney_name` on pure
  // work-comp cases, etc.).
  const caseBase = { case_type: deriveCaseType(f) };
  if (vis.showAccident) {
    Object.assign(caseBase, {
      date_of_injury: cleanStr(f.accidentDate),
      auto_carrier: cleanStr(f.autoCarrier),
      adjuster_name: cleanStr(f.adjusterName),
      adjuster_phone: cleanStr(f.adjusterPhone),
    });
  }
  if (vis.showWorkComp) {
    Object.assign(caseBase, {
      employer_for_claim: cleanStr(f.employerAtInjury),
      work_comp_carrier: cleanStr(f.workCompCarrier),
      // Shared with PI if both toggles are on.
      claim_number: cleanStr(f.claimNumber) || caseBase.claim_number,
      adjuster_name: caseBase.adjuster_name || cleanStr(f.adjusterName),
      adjuster_phone: caseBase.adjuster_phone || cleanStr(f.adjusterPhone),
    });
  }
  if (vis.showPersonalInjury) {
    Object.assign(caseBase, {
      attorney_name: cleanStr(f.attorneyName),
      attorney_phone: cleanStr(f.attorneyPhone),
      attorney_email: cleanStr(f.attorneyEmail),
      claim_number: cleanStr(f.claimNumber) || caseBase.claim_number,
    });
  }
  const case_details = compactObj(caseBase);

  const sigName = cleanStr(f.signatureName);
  const sigDate = cleanStr(f.signatureDate);
  const mkConsent = (type, accepted) =>
    accepted
      ? compactObj({
          type,
          accepted: true,
          signature_name: sigName,
          signed_at: sigDate,
        })
      : undefined;
  const additional = [
    f.assignmentOfBenefits ? mkConsent("assignment_of_benefits", true) : null,
    f.releaseOfInformation ? mkConsent("release_of_information", true) : null,
  ].filter(Boolean);
  const consents = compactObj({
    hipaa: mkConsent("hipaa", f.hipaaAcknowledged),
    treatment: mkConsent("treatment", f.consentToTreat),
    financial: mkConsent("financial", f.financialPolicyAccepted),
    additional: additional.length ? additional : undefined,
  });

  const payload = {
    location_id: cleanStr(f.preferredLocationId),
    demographics,
    contact,
    address,
    emergency_contact: emergencyContact,
    admin,
    guarantor,
    insurance,
    clinical_intake,
    case_details,
    consents,
  };
  Object.keys(payload).forEach((k) => payload[k] === undefined && delete payload[k]);
  return payload;
}

module.exports = {
  // Constants
  PAIN_AREA_OPTIONS,
  SYMPTOM_OPTIONS,
  ONSET_TYPE_OPTIONS,
  STEP1_REQUIRED_BASE,
  STEP2_REQUIRED_BASE,
  // Utilities
  cleanStr,
  compactObj,
  splitName,
  csvToList,
  mergeList,
  // Format validators
  isValidEmail,
  isValidPhone,
  isValidPostal,
  // Date helpers
  isFutureDate,
  computeAge,
  isMinor,
  // Conditional logic
  visibilityForForm,
  validateStep,
  validateAll,
  deriveCaseType,
  buildPayload,
};
