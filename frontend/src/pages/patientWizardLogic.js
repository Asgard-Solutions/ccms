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

// Strip US phone formatting → 10 digits. Blanks stay blank; values that
// don't normalise cleanly pass through unchanged so the user doesn't
// silently lose a legacy sub-format they typed.
function cleanPhone(v) {
  const s = cleanStr(v);
  if (!s) return undefined;
  const digits = s.replace(/\D+/g, "");
  const trimmed =
    digits.length === 11 && digits[0] === "1" ? digits.slice(1) : digits;
  return trimmed.length === 10 ? trimmed : s;
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
    employer_phone: cleanPhone(f.employerPhone),
  });

  const contact = compactObj({
    phone: cleanPhone(f.mobilePhone),
    phone_alt: cleanPhone(f.homePhone),
    phone_work: cleanPhone(f.workPhone),
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
    phone: cleanPhone(f.emergencyContactPhone),
    phone_alt: cleanPhone(f.emergencyContactAltPhone),
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
      phone: cleanPhone(f.guarantorPhone),
      email: cleanStr(f.guarantorEmail),
      address: cleanStr(f.guarantorAddress),
      employer: cleanStr(f.guarantorEmployerName),
      employer_phone: cleanPhone(f.guarantorEmployerPhone),
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
      adjuster_phone: cleanPhone(f.adjusterPhone),
    });
  }
  if (vis.showWorkComp) {
    Object.assign(caseBase, {
      employer_for_claim: cleanStr(f.employerAtInjury),
      work_comp_carrier: cleanStr(f.workCompCarrier),
      // Shared with PI if both toggles are on.
      claim_number: cleanStr(f.claimNumber) || caseBase.claim_number,
      adjuster_name: caseBase.adjuster_name || cleanStr(f.adjusterName),
      adjuster_phone: caseBase.adjuster_phone || cleanPhone(f.adjusterPhone),
    });
  }
  if (vis.showPersonalInjury) {
    Object.assign(caseBase, {
      attorney_name: cleanStr(f.attorneyName),
      attorney_phone: cleanPhone(f.attorneyPhone),
      attorney_email: cleanStr(f.attorneyEmail),
      claim_number: cleanStr(f.claimNumber) || caseBase.claim_number,
    });
  }
  const case_details = compactObj(caseBase);

  const sigName = cleanStr(f.signatureName);
  const sigDate = cleanStr(f.signatureDate);
  const sigImage = cleanStr(f.signatureImage);
  const mkConsent = (type, accepted) =>
    accepted
      ? compactObj({
          type,
          accepted: true,
          signature_name: sigName,
          signed_at: sigDate,
          signature_image: sigImage,
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

// ---------------------------------------------------------------------------
// Phase 5 — Edit-from-detail (payload → wizard form) + autosave draft helpers
// ---------------------------------------------------------------------------

// Canonical "empty" wizard form — kept in sync with Patients.jsx's EMPTY_FORM
// so a caller can reset to a clean slate without importing the UI file.
const EMPTY_FORM = {
  firstName: "", middleName: "", lastName: "", preferredName: "",
  dateOfBirth: "", sexAtBirth: "", genderIdentity: "", pronouns: "",
  maritalStatus: "", preferredLanguage: "",
  mobilePhone: "", homePhone: "", workPhone: "", email: "",
  preferredContactMethod: "",
  smsConsent: false, emailConsent: false, voicemailConsent: false,
  addressLine1: "", addressLine2: "", city: "", state: "", postalCode: "", country: "USA",
  emergencyContactName: "", emergencyContactRelationship: "",
  emergencyContactPhone: "", emergencyContactAltPhone: "", emergencyContactEmail: "",
  assignedProviderId: "", preferredLocationId: "", referralSource: "",
  occupation: "", employerName: "", employerPhone: "",
  responsiblePartySameAsPatient: true,
  guarantorFullName: "", guarantorRelationship: "", guarantorDateOfBirth: "",
  guarantorPhone: "", guarantorEmail: "", guarantorAddress: "",
  guarantorEmployerName: "", guarantorEmployerPhone: "",
  hasInsurance: false,
  primaryCarrier: "", primaryPlanName: "", primaryPlanType: "",
  primaryMemberId: "", primaryGroupNumber: "", primaryPolicyHolderName: "",
  primaryPolicyHolderRelationship: "", primaryPolicyHolderDob: "",
  primaryEffectiveDate: "", primaryCopay: "", primaryDeductible: "",
  secondaryCarrier: "", secondaryPlanName: "", secondaryPlanType: "",
  secondaryMemberId: "", secondaryGroupNumber: "",
  secondaryPolicyHolderName: "", secondaryPolicyHolderRelationship: "",
  chiefComplaint: "", symptomStartDate: "", onsetType: "",
  accidentRelated: false, workComp: false, personalInjury: false,
  painAreas: [], painAreasOther: "", painScore: "",
  symptoms: [], symptomsOther: "",
  priorTreatment: "", medications: "", allergies: "",
  surgeries: "", medicalHistory: "", providerNotes: "",
  accidentDate: "", claimNumber: "", autoCarrier: "",
  adjusterName: "", adjusterPhone: "",
  attorneyName: "", attorneyPhone: "", attorneyEmail: "",
  employerAtInjury: "", workCompCarrier: "",
  hipaaAcknowledged: false, consentToTreat: false,
  financialPolicyAccepted: false, assignmentOfBenefits: false,
  releaseOfInformation: false,
  signatureName: "", signatureDate: "", signatureImage: null,
};

function _coerceStr(v) {
  return v === null || v === undefined ? "" : String(v);
}

// Display-ready phone coercion used by `payloadToForm` — turns a stored
// 10-digit canonical value into `(XXX) XXX-XXXX` so the user sees pretty
// values the moment the wizard opens. Non-canonical legacy values (e.g.
// `+1-555-0102`) pass through unchanged so the field is still editable.
function _coercePhone(v) {
  const s = _coerceStr(v);
  if (!s) return "";
  const digits = s.replace(/\D+/g, "");
  const d = digits.length === 11 && digits[0] === "1" ? digits.slice(1) : digits;
  return d.length === 10 ? `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}` : s;
}

function _consentAccepted(c) {
  return Boolean(c && c.accepted === true);
}

function _pickSignature(consents) {
  const sources = [
    consents?.hipaa, consents?.treatment, consents?.financial,
    consents?.telehealth, consents?.photo_release,
    ...(Array.isArray(consents?.additional) ? consents.additional : []),
  ];
  let image = null;
  for (const c of sources) {
    if (!image && c?.signature_image) image = c.signature_image;
    if (c?.signature_name || c?.signed_at) {
      return {
        signatureName: _coerceStr(c.signature_name),
        signatureDate: _coerceStr(c.signed_at).slice(0, 10),
        signatureImage: image || c.signature_image || null,
      };
    }
  }
  return { signatureName: "", signatureDate: "", signatureImage: image };
}

/**
 * Convert a patient detail response (grouped shape emitted by the backend)
 * back into the flat wizard form state. Legacy-only records fall back to
 * top-level scalars where possible. Always returns a shape that merges
 * cleanly with EMPTY_FORM.
 */
function payloadToForm(patient) {
  if (!patient || typeof patient !== "object") return { ...EMPTY_FORM };
  const demo = patient.demographics || {};
  const contact = patient.contact || {};
  const addr = patient.address_details || {};
  const ec = patient.emergency_contact_details || {};
  const admin = patient.admin || {};
  const g = patient.guarantor || {};
  const ins = patient.insurance || {};
  const primary = ins.primary || {};
  const secondary = ins.secondary || {};
  const clin = patient.clinical_intake || {};
  const cd = patient.case_details || {};
  const cons = patient.consents || {};
  const { signatureName, signatureDate, signatureImage } = _pickSignature(cons);
  const additional = Array.isArray(cons.additional) ? cons.additional : [];
  const hasConsent = (type) =>
    additional.some((c) => c && c.type === type && c.accepted === true);

  const caseType = cd.case_type || "";
  const out = {
    ...EMPTY_FORM,
    // Demographics / legacy name fallbacks
    firstName: _coerceStr(demo.first_name || patient.first_name),
    middleName: _coerceStr(demo.middle_name),
    lastName: _coerceStr(demo.last_name || patient.last_name),
    preferredName: _coerceStr(demo.preferred_name),
    dateOfBirth: _coerceStr(demo.date_of_birth || patient.date_of_birth),
    sexAtBirth: _coerceStr(demo.sex_at_birth),
    genderIdentity: _coerceStr(demo.gender || patient.gender),
    pronouns: _coerceStr(demo.pronouns),
    maritalStatus: _coerceStr(demo.marital_status),
    preferredLanguage: _coerceStr(demo.language),
    occupation: _coerceStr(demo.occupation),
    employerName: _coerceStr(demo.employer),
    employerPhone: _coercePhone(demo.employer_phone),
    // Contact
    mobilePhone: _coercePhone(contact.phone || patient.phone),
    homePhone: _coercePhone(contact.phone_alt),
    workPhone: _coercePhone(contact.phone_work),
    email: _coerceStr(contact.email || patient.email),
    preferredContactMethod: _coerceStr(contact.preferred_contact_method),
    smsConsent: Boolean(contact.sms_consent),
    emailConsent: Boolean(contact.email_consent),
    voicemailConsent: Boolean(contact.voicemail_consent),
    // Address
    addressLine1: _coerceStr(addr.line1),
    addressLine2: _coerceStr(addr.line2),
    city: _coerceStr(addr.city),
    state: _coerceStr(addr.state),
    postalCode: _coerceStr(addr.postal_code),
    country: _coerceStr(addr.country) || "USA",
    // Emergency contact
    emergencyContactName: _coerceStr(ec.name),
    emergencyContactRelationship: _coerceStr(ec.relationship),
    emergencyContactPhone: _coercePhone(ec.phone),
    emergencyContactAltPhone: _coercePhone(ec.phone_alt),
    emergencyContactEmail: _coerceStr(ec.email),
    // Administrative
    assignedProviderId: _coerceStr(admin.primary_provider_id),
    preferredLocationId: _coerceStr(patient.location_id),
    referralSource: _coerceStr(admin.referral_source),
    // Guarantor
    responsiblePartySameAsPatient:
      g && g.same_as_patient !== undefined
        ? Boolean(g.same_as_patient)
        : !g || Object.keys(g).length === 0,
    guarantorFullName: [g.first_name, g.last_name].filter(Boolean).join(" "),
    guarantorRelationship: _coerceStr(g.relationship),
    guarantorDateOfBirth: _coerceStr(g.date_of_birth),
    guarantorPhone: _coercePhone(g.phone),
    guarantorEmail: _coerceStr(g.email),
    guarantorAddress: _coerceStr(g.address),
    guarantorEmployerName: _coerceStr(g.employer),
    guarantorEmployerPhone: _coercePhone(g.employer_phone),
    // Insurance
    hasInsurance: Boolean(ins && (Object.keys(primary).length || Object.keys(secondary).length)),
    primaryCarrier: _coerceStr(primary.carrier),
    primaryPlanName: _coerceStr(primary.plan_name),
    primaryPlanType: _coerceStr(primary.plan_type),
    primaryMemberId: _coerceStr(primary.member_id),
    primaryGroupNumber: _coerceStr(primary.group_number),
    primaryPolicyHolderName: _coerceStr(primary.policy_holder_name),
    primaryPolicyHolderRelationship: _coerceStr(primary.policy_holder_relationship),
    primaryPolicyHolderDob: _coerceStr(primary.policy_holder_dob),
    primaryEffectiveDate: _coerceStr(primary.effective_date),
    primaryCopay: _coerceStr(primary.copay),
    primaryDeductible: _coerceStr(primary.deductible),
    secondaryCarrier: _coerceStr(secondary.carrier),
    secondaryPlanName: _coerceStr(secondary.plan_name),
    secondaryPlanType: _coerceStr(secondary.plan_type),
    secondaryMemberId: _coerceStr(secondary.member_id),
    secondaryGroupNumber: _coerceStr(secondary.group_number),
    secondaryPolicyHolderName: _coerceStr(secondary.policy_holder_name),
    secondaryPolicyHolderRelationship: _coerceStr(secondary.policy_holder_relationship),
    // Clinical intake
    chiefComplaint: _coerceStr(clin.chief_complaint),
    symptomStartDate: _coerceStr(clin.complaint_onset),
    onsetType: _coerceStr(clin.onset_type),
    painScore:
      typeof clin.pain_level === "number" ? String(clin.pain_level) : "",
    painAreas: Array.isArray(clin.pain_locations) ? [...clin.pain_locations] : [],
    painAreasOther: "",
    symptoms: Array.isArray(clin.symptoms) ? [...clin.symptoms] : [],
    symptomsOther: "",
    priorTreatment: _coerceStr(clin.prior_treatments),
    medications: _coerceStr(clin.medications),
    allergies: _coerceStr(clin.allergies),
    surgeries: _coerceStr(clin.past_surgical_history),
    medicalHistory: _coerceStr(clin.past_medical_history),
    providerNotes: _coerceStr(clin.notes),
    // Case-type flags (derived from case_type + raw presence)
    personalInjury: caseType === "personal_injury" || Boolean(cd.attorney_name || cd.attorney_email),
    workComp: caseType === "workers_comp" || Boolean(cd.work_comp_carrier || cd.employer_for_claim),
    accidentRelated:
      caseType === "auto_accident" || Boolean(cd.auto_carrier || cd.date_of_injury),
    accidentDate: _coerceStr(cd.date_of_injury),
    claimNumber: _coerceStr(cd.claim_number),
    autoCarrier: _coerceStr(cd.auto_carrier),
    adjusterName: _coerceStr(cd.adjuster_name),
    adjusterPhone: _coercePhone(cd.adjuster_phone),
    attorneyName: _coerceStr(cd.attorney_name),
    attorneyPhone: _coercePhone(cd.attorney_phone),
    attorneyEmail: _coerceStr(cd.attorney_email),
    employerAtInjury: _coerceStr(cd.employer_for_claim),
    workCompCarrier: _coerceStr(cd.work_comp_carrier),
    // Consents
    hipaaAcknowledged: _consentAccepted(cons.hipaa),
    consentToTreat: _consentAccepted(cons.treatment),
    financialPolicyAccepted: _consentAccepted(cons.financial),
    assignmentOfBenefits: hasConsent("assignment_of_benefits"),
    releaseOfInformation: hasConsent("release_of_information"),
    signatureName,
    signatureDate,
    signatureImage: signatureImage || null,
  };
  return out;
}

// Draft storage — tenant + user scoped so a staff user can't see another
// staff user's unfinished intake draft on a shared kiosk.
function draftStorageKey(userId, tenantId) {
  return `ccms.intake-draft.${tenantId || "default"}.${userId || "anon"}`;
}

const DRAFT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

function isDraftFresh(savedAtIso, _now = new Date()) {
  if (!savedAtIso) return false;
  const ts = new Date(savedAtIso).getTime();
  if (Number.isNaN(ts)) return false;
  return _now.getTime() - ts <= DRAFT_MAX_AGE_MS;
}

// A draft is "worth restoring" only if the staff user entered at least one
// meaningful field. Empty drafts (e.g. the wizard was opened-and-closed)
// are silently discarded so we don't show a pointless Resume banner.
function formHasAnyInput(form) {
  if (!form || typeof form !== "object") return false;
  const scalarKeys = [
    "firstName", "lastName", "middleName", "preferredName", "dateOfBirth",
    "mobilePhone", "homePhone", "workPhone", "email",
    "addressLine1", "addressLine2", "city", "state", "postalCode",
    "emergencyContactName", "emergencyContactPhone", "emergencyContactEmail",
    "assignedProviderId", "referralSource", "occupation", "employerName",
    "guarantorFullName", "guarantorPhone", "guarantorEmail",
    "primaryCarrier", "primaryMemberId", "secondaryCarrier", "secondaryMemberId",
    "chiefComplaint", "painScore", "painAreasOther", "symptomsOther",
    "medications", "allergies", "surgeries", "medicalHistory", "providerNotes",
    "accidentDate", "claimNumber", "autoCarrier", "adjusterName",
    "attorneyName", "attorneyEmail", "employerAtInjury", "workCompCarrier",
    "signatureName",
  ];
  for (const k of scalarKeys) {
    if (cleanStr(form[k])) return true;
  }
  if (Array.isArray(form.painAreas) && form.painAreas.length) return true;
  if (Array.isArray(form.symptoms) && form.symptoms.length) return true;
  if (form.hasInsurance || form.personalInjury || form.workComp || form.accidentRelated) return true;
  if (form.hipaaAcknowledged || form.consentToTreat || form.financialPolicyAccepted) return true;
  return false;
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
  // Phase 5 — edit-from-detail + autosave
  EMPTY_FORM,
  payloadToForm,
  draftStorageKey,
  isDraftFresh,
  formHasAnyInput,
};
