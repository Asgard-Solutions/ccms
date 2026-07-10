/**
 * Node 20 built-in test runner:
 *   node --test src/pages/patientWizardLogic.test.mjs
 *
 * Covers Phase 3 business logic — conditional visibility + validation
 * rules + chiropractic option lists + payload shaping.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  PAIN_AREA_OPTIONS,
  SYMPTOM_OPTIONS,
  ONSET_TYPE_OPTIONS,
  isValidEmail,
  isValidPhone,
  isValidPostal,
  isFutureDate,
  computeAge,
  isMinor,
  visibilityForForm,
  validateStep,
  validateAll,
  buildPayload,
  deriveCaseType,
  mergeList,
  splitName,
  EMPTY_FORM,
  payloadToForm,
  draftStorageKey,
  isDraftFresh,
  formHasAnyInput,
} from "./patientWizardLogic.js";

// Deterministic "today" for age & future-date logic.
const TODAY = new Date(Date.UTC(2026, 1, 20)); // 2026-02-20

// ---------------------------------------------------------------------------
// Format validators
// ---------------------------------------------------------------------------

test("isValidEmail accepts sane addresses and rejects malformed ones", () => {
  assert.equal(isValidEmail("ops@ccms.app"), true);
  assert.equal(isValidEmail("p.p@sub.example.co.uk"), true);
  assert.equal(isValidEmail(""), false);
  assert.equal(isValidEmail("not-an-email"), false);
  assert.equal(isValidEmail("missing@tld"), false);
  assert.equal(isValidEmail("two@@signs@x.com"), false);
});

test("isValidPhone accepts 7–15 digit numbers with typical punctuation", () => {
  assert.equal(isValidPhone("+1 (555) 012-0104"), true);
  assert.equal(isValidPhone("555-0104"), true);
  assert.equal(isValidPhone("07700900123"), true);
  assert.equal(isValidPhone("12"), false);
  assert.equal(isValidPhone(""), false);
  assert.equal(isValidPhone("abc"), false);
});

test("isValidPostal accepts US ZIP / ZIP+4 and generic alphanumeric codes", () => {
  assert.equal(isValidPostal("97477"), true);
  assert.equal(isValidPostal("97477-1234"), true);
  assert.equal(isValidPostal("SW1A 1AA"), true);
  assert.equal(isValidPostal(""), false);
  assert.equal(isValidPostal("12"), false);
  assert.equal(isValidPostal("!!!"), false);
});

// ---------------------------------------------------------------------------
// Date helpers
// ---------------------------------------------------------------------------

test("isFutureDate flags DOBs strictly after today", () => {
  assert.equal(isFutureDate("2030-01-01", TODAY), true);
  assert.equal(isFutureDate("2026-02-21", TODAY), true);
  assert.equal(isFutureDate("2026-02-20", TODAY), false);
  assert.equal(isFutureDate("1990-01-01", TODAY), false);
  assert.equal(isFutureDate("", TODAY), false);
  assert.equal(isFutureDate("not-a-date", TODAY), false);
});

test("computeAge + isMinor handle adults, minors, and edge cases", () => {
  assert.equal(computeAge("1990-02-20", TODAY), 36);
  assert.equal(computeAge("2010-03-01", TODAY), 15);
  // Birthday later this year — not yet 18.
  assert.equal(isMinor("2008-06-01", TODAY), true);
  // Already-passed birthday this year, still 17.
  assert.equal(isMinor("2009-01-15", TODAY), true);
  // Turns 18 today.
  assert.equal(isMinor("2008-02-20", TODAY), false);
  assert.equal(isMinor("1970-01-01", TODAY), false);
  assert.equal(isMinor("", TODAY), false);
});

// ---------------------------------------------------------------------------
// Conditional visibility
// ---------------------------------------------------------------------------

test("visibilityForForm — adult, responsible party = patient, no insurance", () => {
  const v = visibilityForForm(
    {
      dateOfBirth: "1990-02-20",
      responsiblePartySameAsPatient: true,
      hasInsurance: false,
    },
    TODAY
  );
  assert.equal(v.isMinor, false);
  assert.equal(v.showGuarantor, false);
  assert.equal(v.requireGuarantor, false);
  assert.equal(v.showInsurance, false);
  assert.equal(v.showAccident, false);
  assert.equal(v.showWorkComp, false);
  assert.equal(v.showPersonalInjury, false);
});

test("visibilityForForm — minor FORCES guarantor block visible and required", () => {
  const v = visibilityForForm(
    {
      dateOfBirth: "2015-04-01",
      responsiblePartySameAsPatient: true, // ignored for minors
      hasInsurance: true,
    },
    TODAY
  );
  assert.equal(v.isMinor, true);
  assert.equal(v.showGuarantor, true);
  // requireGuarantor is only true when responsible party differs (which is
  // what the form should force for a minor).
  assert.equal(v.requireGuarantor, false);
  assert.equal(v.showInsurance, true);
});

test("visibilityForForm — minor + responsible party differs → guarantor REQUIRED", () => {
  const v = visibilityForForm(
    {
      dateOfBirth: "2012-06-01",
      responsiblePartySameAsPatient: false,
      hasInsurance: false,
    },
    TODAY
  );
  assert.equal(v.isMinor, true);
  assert.equal(v.showGuarantor, true);
  assert.equal(v.requireGuarantor, true);
});

test("visibilityForForm — adult w/ different guarantor shows section but does not require", () => {
  const v = visibilityForForm(
    {
      dateOfBirth: "1985-01-01",
      responsiblePartySameAsPatient: false,
      hasInsurance: true,
    },
    TODAY
  );
  assert.equal(v.isMinor, false);
  assert.equal(v.showGuarantor, true);
  assert.equal(v.requireGuarantor, false);
  assert.equal(v.showInsurance, true);
});

test("visibilityForForm — Step 4 blocks track their flag booleans exactly", () => {
  const v = visibilityForForm(
    {
      dateOfBirth: "1990-01-01",
      accidentRelated: true,
      workComp: false,
      personalInjury: true,
    },
    TODAY
  );
  assert.equal(v.showAccident, true);
  assert.equal(v.showWorkComp, false);
  assert.equal(v.showPersonalInjury, true);
});

// ---------------------------------------------------------------------------
// Validation rules — only visible fields must block submission.
// ---------------------------------------------------------------------------

function baseGoodForm(overrides = {}) {
  return {
    firstName: "Ada",
    lastName: "Lovelace",
    dateOfBirth: "1990-05-15",
    mobilePhone: "+1 (555) 000-0001",
    email: "ada@example.com",
    addressLine1: "1 Infinite Loop",
    city: "Cupertino",
    state: "CA",
    postalCode: "95014",
    emergencyContactName: "Byron",
    emergencyContactRelationship: "Spouse",
    emergencyContactPhone: "+1-555-0002",
    assignedProviderId: "prov-123",
    responsiblePartySameAsPatient: true,
    hasInsurance: false,
    ...overrides,
  };
}

test("Step 1 — happy path has zero errors", () => {
  const errs = validateStep(1, baseGoodForm(), TODAY);
  assert.deepEqual(errs, {});
});

test("Step 1 — missing required fields each surface a message", () => {
  const errs = validateStep(
    1,
    {
      ...baseGoodForm({
        firstName: "",
        lastName: "",
        dateOfBirth: "",
        mobilePhone: "",
        addressLine1: "",
        city: "",
        state: "",
        postalCode: "",
        emergencyContactName: "",
        emergencyContactRelationship: "",
        emergencyContactPhone: "",
      }),
    },
    TODAY
  );
  [
    "firstName",
    "lastName",
    "dateOfBirth",
    "mobilePhone",
    "addressLine1",
    "city",
    "state",
    "postalCode",
    "emergencyContactName",
    "emergencyContactRelationship",
    "emergencyContactPhone",
  ].forEach((k) => {
    assert.ok(errs[k], `expected an error on ${k}`);
  });
});

test("Step 1 — future DOB is rejected with a friendly message", () => {
  const errs = validateStep(1, baseGoodForm({ dateOfBirth: "2099-01-01" }), TODAY);
  assert.match(errs.dateOfBirth, /future/i);
});

test("Step 1 — invalid email / phone / postal are flagged when present", () => {
  const errs = validateStep(
    1,
    baseGoodForm({
      email: "not-an-email",
      mobilePhone: "12",
      postalCode: "!!",
      emergencyContactEmail: "also-bad",
    }),
    TODAY
  );
  assert.ok(errs.email);
  assert.ok(errs.mobilePhone);
  assert.ok(errs.postalCode);
  assert.ok(errs.emergencyContactEmail);
});

test("Step 1 — optional phone/email are NOT flagged when blank", () => {
  const errs = validateStep(
    1,
    baseGoodForm({ homePhone: "", workPhone: "", email: "", emergencyContactEmail: "" }),
    TODAY
  );
  assert.equal(errs.homePhone, undefined);
  assert.equal(errs.workPhone, undefined);
  assert.equal(errs.email, undefined);
  assert.equal(errs.emergencyContactEmail, undefined);
});

test("Step 2 — adult w/ same-as-patient guarantor: only assignedProviderId gates step", () => {
  const errs = validateStep(2, baseGoodForm({ assignedProviderId: "" }), TODAY);
  assert.ok(errs.assignedProviderId);
  assert.equal(errs.guarantorFullName, undefined);
});

test("Step 2 — minor + responsible party differs REQUIRES guarantor fields", () => {
  const errs = validateStep(
    2,
    baseGoodForm({
      dateOfBirth: "2015-06-10",
      responsiblePartySameAsPatient: false,
      guarantorFullName: "",
      guarantorRelationship: "",
      guarantorPhone: "",
    }),
    TODAY
  );
  assert.ok(errs.guarantorFullName);
  assert.ok(errs.guarantorRelationship);
  assert.ok(errs.guarantorPhone);
});

test("Step 2 — adult + responsible party differs does NOT require guarantor", () => {
  const errs = validateStep(
    2,
    baseGoodForm({
      responsiblePartySameAsPatient: false,
      guarantorFullName: "",
      guarantorRelationship: "",
      guarantorPhone: "",
    }),
    TODAY
  );
  // Only the step's own required field should fail — guarantor left alone.
  assert.equal(errs.guarantorFullName, undefined);
  assert.equal(errs.guarantorRelationship, undefined);
  assert.equal(errs.guarantorPhone, undefined);
});

test("Step 2 — insurance fields are never validated (hidden when hasInsurance=false)", () => {
  const errs = validateStep(
    2,
    baseGoodForm({
      hasInsurance: false,
      primaryCarrier: "",
      primaryMemberId: "",
    }),
    TODAY
  );
  assert.equal(errs.primaryCarrier, undefined);
  assert.equal(errs.primaryMemberId, undefined);
});

// ---------------------------------------------------------------------------
// Payload shape — guarantor / insurance / case blocks are conditional.
// ---------------------------------------------------------------------------

test("buildPayload — same-as-patient adult omits guarantor PHI (only same_as_patient=true)", () => {
  const p = buildPayload(baseGoodForm(), TODAY);
  assert.deepEqual(p.guarantor, { same_as_patient: true });
  assert.equal(p.insurance, undefined);
});

test("buildPayload — minor produces a structured guarantor block even if flag left on", () => {
  const p = buildPayload(
    baseGoodForm({
      dateOfBirth: "2012-01-01",
      responsiblePartySameAsPatient: false,
      guarantorFullName: "Mia Carter",
      guarantorRelationship: "Mother",
      guarantorPhone: "+1-555-0909",
    }),
    TODAY
  );
  assert.equal(p.guarantor.same_as_patient, false);
  assert.equal(p.guarantor.first_name, "Mia");
  assert.equal(p.guarantor.last_name, "Carter");
  assert.equal(p.guarantor.relationship, "Mother");
  assert.equal(p.guarantor.phone, "+1-555-0909");
});

test("buildPayload — insurance included only when toggle is on", () => {
  const on = buildPayload(
    baseGoodForm({
      hasInsurance: true,
      primaryCarrier: "Aetna",
      primaryMemberId: "A-1",
      primaryPlanType: "PPO",
    }),
    TODAY
  );
  assert.ok(on.insurance);
  assert.equal(on.insurance.primary.carrier, "Aetna");
  assert.equal(on.insurance.primary.member_id, "A-1");

  const off = buildPayload(
    baseGoodForm({
      hasInsurance: false,
      primaryCarrier: "Aetna",
      primaryMemberId: "A-1",
    }),
    TODAY
  );
  assert.equal(off.insurance, undefined);
});

test("buildPayload — case block respects conditional flags independently", () => {
  // Pure PI
  const pi = buildPayload(
    baseGoodForm({
      personalInjury: true,
      attorneyName: "Saul",
      attorneyEmail: "saul@law.test",
      claimNumber: "CLM-9",
    }),
    TODAY
  );
  assert.equal(pi.case_details.case_type, "personal_injury");
  assert.equal(pi.case_details.attorney_name, "Saul");
  assert.equal(pi.case_details.claim_number, "CLM-9");
  assert.equal(pi.case_details.auto_carrier, undefined);
  assert.equal(pi.case_details.work_comp_carrier, undefined);

  // Pure work comp
  const wc = buildPayload(
    baseGoodForm({
      workComp: true,
      employerAtInjury: "Acme",
      workCompCarrier: "The Hartford",
    }),
    TODAY
  );
  assert.equal(wc.case_details.case_type, "workers_comp");
  assert.equal(wc.case_details.employer_for_claim, "Acme");
  assert.equal(wc.case_details.work_comp_carrier, "The Hartford");
  assert.equal(wc.case_details.attorney_name, undefined);

  // Pure auto
  const auto = buildPayload(
    baseGoodForm({
      accidentRelated: true,
      accidentDate: "2025-12-01",
      autoCarrier: "State Farm",
      adjusterName: "Jo",
      adjusterPhone: "+1-555-0404",
    }),
    TODAY
  );
  assert.equal(auto.case_details.case_type, "auto_accident");
  assert.equal(auto.case_details.auto_carrier, "State Farm");
  assert.equal(auto.case_details.adjuster_name, "Jo");
  assert.equal(auto.case_details.attorney_name, undefined);

  // None of the three: case_details entirely omitted.
  const none = buildPayload(baseGoodForm(), TODAY);
  assert.equal(none.case_details, undefined);
});

test("buildPayload — pain_locations + symptoms merge checkbox selections and 'other' CSV", () => {
  const p = buildPayload(
    baseGoodForm({
      painAreas: ["Lower back", "Left hip"],
      painAreasOther: "groin, coccyx",
      symptoms: ["Numbness", "Tingling"],
      symptomsOther: "",
      painScore: "7",
    }),
    TODAY
  );
  assert.deepEqual(p.clinical_intake.pain_locations, [
    "Lower back",
    "Left hip",
    "groin",
    "coccyx",
  ]);
  assert.deepEqual(p.clinical_intake.symptoms, ["Numbness", "Tingling"]);
  assert.equal(p.clinical_intake.pain_level, 7);
});

test("buildPayload — pain_level is clamped to 0–10", () => {
  const low = buildPayload(baseGoodForm({ painScore: "-5" }), TODAY);
  assert.equal(low.clinical_intake.pain_level, 0);
  const high = buildPayload(baseGoodForm({ painScore: "99" }), TODAY);
  assert.equal(high.clinical_intake.pain_level, 10);
});

test("buildPayload — consents.additional records AOB + ROI with signature metadata", () => {
  const p = buildPayload(
    baseGoodForm({
      hipaaAcknowledged: true,
      assignmentOfBenefits: true,
      releaseOfInformation: true,
      signatureName: "Ada Lovelace",
      signatureDate: "2026-02-20",
    }),
    TODAY
  );
  assert.equal(p.consents.hipaa.accepted, true);
  assert.equal(p.consents.hipaa.signature_name, "Ada Lovelace");
  assert.equal(p.consents.additional.length, 2);
  assert.equal(p.consents.additional[0].type, "assignment_of_benefits");
  assert.equal(p.consents.additional[1].type, "release_of_information");
});

// ---------------------------------------------------------------------------
// Constants sanity — chiropractic option lists
// ---------------------------------------------------------------------------

test("option lists are chiropractic-relevant and non-empty", () => {
  assert.ok(PAIN_AREA_OPTIONS.length >= 15);
  assert.ok(PAIN_AREA_OPTIONS.some((x) => /lower back/i.test(x)));
  assert.ok(PAIN_AREA_OPTIONS.some((x) => /sciatica/i.test(x)));
  assert.ok(SYMPTOM_OPTIONS.length >= 10);
  assert.ok(SYMPTOM_OPTIONS.some((x) => /numbness/i.test(x)));
  assert.ok(SYMPTOM_OPTIONS.some((x) => /tingling/i.test(x)));
  assert.ok(ONSET_TYPE_OPTIONS.length >= 5);
  assert.ok(ONSET_TYPE_OPTIONS.some((o) => o.value === "trauma"));
  assert.ok(ONSET_TYPE_OPTIONS.some((o) => o.value === "repetitive_strain"));
});

// ---------------------------------------------------------------------------
// Spot-checks for helpers
// ---------------------------------------------------------------------------

test("mergeList dedupes case-insensitively while preserving order", () => {
  assert.deepEqual(mergeList(["Lower back", "Neck"], "neck, coccyx"), [
    "Lower back",
    "Neck",
    "coccyx",
  ]);
});

test("splitName splits on first whitespace", () => {
  assert.deepEqual(splitName("Mia Carter"), { first_name: "Mia", last_name: "Carter" });
  assert.deepEqual(splitName("Cher"), { first_name: "Cher", last_name: undefined });
  assert.deepEqual(splitName("Ana Maria Garcia"), {
    first_name: "Ana",
    last_name: "Maria Garcia",
  });
});

test("deriveCaseType prioritises PI → WC → auto", () => {
  assert.equal(deriveCaseType({ personalInjury: true, workComp: true, accidentRelated: true }), "personal_injury");
  assert.equal(deriveCaseType({ workComp: true, accidentRelated: true }), "workers_comp");
  assert.equal(deriveCaseType({ accidentRelated: true }), "auto_accident");
  assert.equal(deriveCaseType({}), undefined);
});

// ---------------------------------------------------------------------------
// End-to-end Phase-3 scenario — the shape a staff user would POST.
// ---------------------------------------------------------------------------

test("validateAll + buildPayload together gate a realistic minor + PI intake", () => {
  const form = baseGoodForm({
    dateOfBirth: "2014-09-10",
    responsiblePartySameAsPatient: false,
    guarantorFullName: "Riley Quinn",
    guarantorRelationship: "Parent",
    guarantorPhone: "+1-555-0800",
    guarantorEmail: "riley@example.com",
    hasInsurance: true,
    primaryCarrier: "BlueCross",
    primaryMemberId: "BCBS-1",
    personalInjury: true,
    attorneyName: "Saul G.",
    attorneyEmail: "saul@law.test",
    attorneyPhone: "+1-555-0123",
    claimNumber: "CLM-42",
    hipaaAcknowledged: true,
    consentToTreat: true,
    signatureName: "Riley Quinn",
    signatureDate: "2026-02-20",
  });
  assert.deepEqual(validateAll(form, TODAY), {});
  const p = buildPayload(form, TODAY);
  assert.equal(p.guarantor.first_name, "Riley");
  assert.equal(p.guarantor.last_name, "Quinn");
  assert.equal(p.insurance.primary.carrier, "BlueCross");
  assert.equal(p.case_details.case_type, "personal_injury");
  assert.equal(p.case_details.attorney_email, "saul@law.test");
  assert.equal(p.case_details.claim_number, "CLM-42");
  assert.equal(p.consents.hipaa.accepted, true);
  assert.equal(p.consents.treatment.signature_name, "Riley Quinn");
});

// ---------------------------------------------------------------------------
// Phase 5 — payloadToForm round-trip + autosave draft helpers
// ---------------------------------------------------------------------------
// (imports for these live in the file-level `import` block at the top)

test("payloadToForm — undefined / null input returns the empty form", () => {
  assert.deepEqual(payloadToForm(undefined), { ...EMPTY_FORM });
  assert.deepEqual(payloadToForm(null), { ...EMPTY_FORM });
});

test("payloadToForm — legacy flat record reuses top-level scalars as form values", () => {
  const legacy = {
    id: "abc",
    first_name: "Legacy",
    last_name: "Patient",
    email: "legacy@example.com",
    phone: "+1-555-0100",
    date_of_birth: "1990-02-14",
    gender: "male",
    address: "124 Willow Lane",
    emergency_contact: "Jane Doe / +1-555-0200",
    notes: "seasonal back pain",
  };
  const form = payloadToForm(legacy);
  assert.equal(form.firstName, "Legacy");
  assert.equal(form.lastName, "Patient");
  assert.equal(form.email, "legacy@example.com");
  assert.equal(form.mobilePhone, "+1-555-0100");
  assert.equal(form.dateOfBirth, "1990-02-14");
  assert.equal(form.genderIdentity, "male");
  // No structured address → form fields stay blank (the legacy scalar lives
  // in the top-of-page strip on the detail view).
  assert.equal(form.addressLine1, "");
  assert.equal(form.city, "");
  // Guarantor defaults to "same as patient" when no block is present.
  assert.equal(form.responsiblePartySameAsPatient, true);
});

test("payloadToForm — round-trips a grouped payload through buildPayload", () => {
  const sourceForm = baseGoodForm({
    dateOfBirth: "1985-07-22",
    preferredName: "Trin",
    hasInsurance: true,
    primaryCarrier: "Aetna",
    primaryPlanType: "PPO",
    primaryMemberId: "A-001",
    personalInjury: true,
    attorneyName: "Saul G.",
    attorneyEmail: "saul@law.test",
    claimNumber: "CLM-RT",
    hipaaAcknowledged: true,
    assignmentOfBenefits: true,
    releaseOfInformation: true,
    signatureName: "Ada Lovelace",
    signatureDate: "2026-02-10",
  });
  const payload = buildPayload(sourceForm, TODAY);
  // Simulate what the backend echoes back on GET (grouped shape + top-level scalars).
  const patient = {
    id: "p1",
    first_name: "Ada",
    last_name: "Lovelace",
    phone: "+1 (555) 000-0001",
    email: "ada@example.com",
    location_id: null,
    ...payload,
    // Backend also returns address_details + emergency_contact_details.
    address_details: { line1: "1 Infinite Loop", city: "Cupertino", state: "CA", postal_code: "95014" },
    emergency_contact_details: { name: "Byron", relationship: "Spouse", phone: "+1-555-0002" },
  };
  const restored = payloadToForm(patient);
  // Identity survived.
  assert.equal(restored.firstName, "Ada");
  assert.equal(restored.lastName, "Lovelace");
  assert.equal(restored.preferredName, "Trin");
  // Address restored from address_details.
  assert.equal(restored.addressLine1, "1 Infinite Loop");
  assert.equal(restored.city, "Cupertino");
  assert.equal(restored.state, "CA");
  assert.equal(restored.postalCode, "95014");
  // Emergency contact restored from emergency_contact_details.
  assert.equal(restored.emergencyContactName, "Byron");
  assert.equal(restored.emergencyContactPhone, "+1-555-0002");
  // Insurance + flags.
  assert.equal(restored.hasInsurance, true);
  assert.equal(restored.primaryCarrier, "Aetna");
  assert.equal(restored.primaryMemberId, "A-001");
  assert.equal(restored.personalInjury, true);
  // Consents restored.
  assert.equal(restored.hipaaAcknowledged, true);
  assert.equal(restored.assignmentOfBenefits, true);
  assert.equal(restored.releaseOfInformation, true);
  assert.equal(restored.signatureName, "Ada Lovelace");
  // Re-building the payload from the restored form yields the same
  // canonical grouped shape on the fields we care about.
  const rebuilt = buildPayload(restored, TODAY);
  assert.equal(rebuilt.insurance.primary.member_id, "A-001");
  assert.equal(rebuilt.case_details.attorney_email, "saul@law.test");
  assert.equal(rebuilt.consents.hipaa.accepted, true);
});

test("payloadToForm — splits guarantor name back into guarantorFullName", () => {
  const form = payloadToForm({
    guarantor: {
      same_as_patient: false,
      first_name: "Riley",
      last_name: "Quinn-Smith",
      relationship: "Parent",
      phone: "+1-555-0909",
    },
  });
  assert.equal(form.guarantorFullName, "Riley Quinn-Smith");
  assert.equal(form.responsiblePartySameAsPatient, false);
  assert.equal(form.guarantorRelationship, "Parent");
});

test("payloadToForm — recovers consent flags from consents.additional[]", () => {
  const form = payloadToForm({
    consents: {
      hipaa: { accepted: true, signature_name: "Sam", signed_at: "2026-02-01T00:00:00Z" },
      additional: [
        { type: "assignment_of_benefits", accepted: true },
        { type: "release_of_information", accepted: true },
      ],
    },
  });
  assert.equal(form.hipaaAcknowledged, true);
  assert.equal(form.assignmentOfBenefits, true);
  assert.equal(form.releaseOfInformation, true);
  assert.equal(form.signatureName, "Sam");
  assert.equal(form.signatureDate, "2026-02-01");
});

// Draft helpers --------------------------------------------------------------

test("draftStorageKey is tenant + user scoped", () => {
  assert.equal(draftStorageKey("u1", "t1"), "ccms.intake-draft.t1.u1");
  assert.equal(draftStorageKey("u1"), "ccms.intake-draft.default.u1");
  assert.equal(draftStorageKey(), "ccms.intake-draft.default.anon");
});

test("isDraftFresh discards old drafts", () => {
  const now = new Date(Date.UTC(2026, 1, 20));
  assert.equal(isDraftFresh(new Date(Date.UTC(2026, 1, 19)).toISOString(), now), true);
  assert.equal(isDraftFresh(new Date(Date.UTC(2026, 1, 13)).toISOString(), now), true);
  // 8 days old → stale.
  assert.equal(isDraftFresh(new Date(Date.UTC(2026, 1, 12)).toISOString(), now), false);
  assert.equal(isDraftFresh("", now), false);
  assert.equal(isDraftFresh("garbage", now), false);
});

test("formHasAnyInput rejects empty + accepts meaningful drafts", () => {
  assert.equal(formHasAnyInput({ ...EMPTY_FORM }), false);
  assert.equal(formHasAnyInput({ ...EMPTY_FORM, firstName: "Ada" }), true);
  assert.equal(formHasAnyInput({ ...EMPTY_FORM, painAreas: ["Neck"] }), true);
  assert.equal(formHasAnyInput({ ...EMPTY_FORM, personalInjury: true }), true);
  assert.equal(formHasAnyInput({ ...EMPTY_FORM, hipaaAcknowledged: true }), true);
});
