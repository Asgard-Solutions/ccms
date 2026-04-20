"""
Patient Service — Patient + MedicalRecord domain models (HIPAA-hardened).

Phase 1 intake expansion (backward-compatible):
  * Legacy flat fields (first_name, last_name, date_of_birth, gender, phone,
    email, address, emergency_contact, notes) remain accepted for
    compatibility with existing records and the current frontend modal.
  * Richer grouped/nested intake sections are now also accepted and persisted
    when present: demographics, contact, address (object), emergency_contact
    (object), admin, guarantor, insurance, clinical_intake, case_details,
    consents.
  * `address` and `emergency_contact` accept BOTH a plain string (legacy) and
    a structured object (new). When an object is supplied, the router stores
    the structured form under `address_details` / `emergency_contact_details`
    AND derives a flat string into the legacy key so current UI code keeps
    working without changes.
  * Sensitive columns are stored encrypted at rest via core.crypto.

Schema additions are opt-in: clients that only send the legacy flat payload
continue to work exactly as before.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Gender = Literal["male", "female", "non-binary", "other", "prefer-not-to-say"]
RecordType = Literal["assessment", "treatment", "note", "diagnosis"]
PatientStatus = Literal["active", "deleted"]

# --------------------------------------------------------------------------
# Grouped intake sections (Phase 1 — richer chiropractic intake)
#
# All sections use `extra="ignore"` so unknown future keys sent by newer
# frontend wizards are silently dropped rather than raising validation
# errors. Validation is intentionally conservative in Phase 1: the wizard
# layer will enforce business rules later.
# --------------------------------------------------------------------------


class Demographics(BaseModel):
    model_config = ConfigDict(extra="ignore")
    first_name: str | None = Field(default=None, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    preferred_name: str | None = Field(default=None, max_length=100)
    date_of_birth: str | None = None
    gender: Gender | None = None
    sex_at_birth: str | None = None
    pronouns: str | None = None
    marital_status: str | None = None
    ssn_last4: str | None = Field(default=None, max_length=4)
    language: str | None = None
    race: str | None = None
    ethnicity: str | None = None
    occupation: str | None = None
    employer: str | None = None
    employer_phone: str | None = None


class ContactInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    phone: str | None = None
    phone_alt: str | None = None
    phone_work: str | None = None
    email: EmailStr | None = None
    preferred_contact_method: str | None = None  # phone|email|sms|portal
    best_time_to_call: str | None = None
    ok_to_leave_message: bool | None = None
    sms_consent: bool | None = None
    email_consent: bool | None = None
    voicemail_consent: bool | None = None


class AddressInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class EmergencyContactInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    relationship: str | None = None
    phone: str | None = None
    phone_alt: str | None = None
    email: EmailStr | None = None
    address: str | None = None


class AdminInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    referred_by: str | None = None
    referral_source: str | None = None
    primary_provider_id: str | None = None
    mrn: str | None = None
    tags: list[str] | None = None
    internal_flags: list[str] | None = None


class GuarantorInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    same_as_patient: bool | None = None
    first_name: str | None = None
    last_name: str | None = None
    relationship: str | None = None
    date_of_birth: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    employer: str | None = None
    employer_phone: str | None = None
    ssn_last4: str | None = Field(default=None, max_length=4)


class InsurancePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    carrier: str | None = None
    plan_name: str | None = None
    plan_type: str | None = None  # PPO|HMO|EPO|POS|Medicare|Medicaid|...
    member_id: str | None = None
    group_number: str | None = None
    policy_holder_name: str | None = None
    policy_holder_relationship: str | None = None
    policy_holder_dob: str | None = None
    effective_date: str | None = None
    termination_date: str | None = None
    copay: str | None = None
    deductible: str | None = None


class InsuranceInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    primary: InsurancePlan | None = None
    secondary: InsurancePlan | None = None
    tertiary: InsurancePlan | None = None


class ClinicalIntake(BaseModel):
    model_config = ConfigDict(extra="ignore")
    chief_complaint: str | None = None
    complaint_onset: str | None = None
    onset_type: str | None = None  # sudden|gradual|acute|chronic|other
    pain_level: int | None = Field(default=None, ge=0, le=10)
    pain_description: str | None = None
    pain_locations: list[str] | None = None
    symptoms: list[str] | None = None
    aggravating_factors: str | None = None
    relieving_factors: str | None = None
    prior_treatments: str | None = None
    medications: str | None = None
    allergies: str | None = None
    past_medical_history: str | None = None
    past_surgical_history: str | None = None
    family_history: str | None = None
    social_history: str | None = None
    review_of_systems: dict | None = None
    notes: str | None = None


class CaseDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")
    case_type: str | None = None  # personal_injury|workers_comp|auto_accident|...
    date_of_injury: str | None = None
    injury_description: str | None = None
    accident_location: str | None = None
    police_report_number: str | None = None
    attorney_name: str | None = None
    attorney_phone: str | None = None
    attorney_email: EmailStr | None = None
    claim_number: str | None = None
    adjuster_name: str | None = None
    adjuster_phone: str | None = None
    employer_for_claim: str | None = None
    work_comp_carrier: str | None = None
    auto_carrier: str | None = None
    return_to_work_status: str | None = None
    notes: str | None = None


class ConsentRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str | None = None
    accepted: bool | None = None
    signature_name: str | None = None
    signature_image: str | None = None  # base64 PNG data-URL (encrypted at rest inside `consents` section)
    signed_at: str | None = None
    document_version: str | None = None
    ip_address: str | None = None


class ConsentsInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hipaa: ConsentRecord | None = None
    treatment: ConsentRecord | None = None
    financial: ConsentRecord | None = None
    telehealth: ConsentRecord | None = None
    photo_release: ConsentRecord | None = None
    additional: list[ConsentRecord] | None = None


# --------------------------------------------------------------------------
# Patient create / update / public shapes
# --------------------------------------------------------------------------


class PatientCreate(BaseModel):
    """Accepts either the legacy flat payload or the new grouped intake
    payload. When grouped sections are provided the router normalizes
    legacy top-level fields from them (see `_normalize_patient_payload`)."""

    model_config = ConfigDict(extra="ignore")

    # Legacy flat fields (still accepted, still required for legacy clients).
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    date_of_birth: str | None = None
    gender: Gender | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | AddressInfo | None = None
    emergency_contact: str | EmergencyContactInfo | None = None
    notes: str | None = None
    location_id: str | None = None

    # Grouped intake sections (Phase 1 expansion).
    demographics: Demographics | None = None
    contact: ContactInfo | None = None
    admin: AdminInfo | None = None
    guarantor: GuarantorInfo | None = None
    insurance: InsuranceInfo | None = None
    clinical_intake: ClinicalIntake | None = None
    case_details: CaseDetails | None = None
    consents: ConsentsInfo | None = None


class PatientUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    date_of_birth: str | None = None
    gender: Gender | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | AddressInfo | None = None
    emergency_contact: str | EmergencyContactInfo | None = None
    notes: str | None = None
    location_id: str | None = None

    demographics: Demographics | None = None
    contact: ContactInfo | None = None
    admin: AdminInfo | None = None
    guarantor: GuarantorInfo | None = None
    insurance: InsuranceInfo | None = None
    clinical_intake: ClinicalIntake | None = None
    case_details: CaseDetails | None = None
    consents: ConsentsInfo | None = None


class PatientPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    user_id: str | None = None
    tenant_id: str | None = None
    location_id: str | None = None

    # Legacy flat fields — guaranteed present for backward-compatible UIs.
    first_name: str
    last_name: str
    display_name_masked: str | None = None
    date_of_birth: str | None = None
    gender: Gender | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    emergency_contact: str | None = None
    notes: str | None = None

    # New grouped intake sections (present only if ever stored). Returned
    # unmasked only when the caller is authorized to see unmasked PHI.
    demographics: Demographics | None = None
    contact: ContactInfo | None = None
    address_details: AddressInfo | None = None
    emergency_contact_details: EmergencyContactInfo | None = None
    admin: AdminInfo | None = None
    guarantor: GuarantorInfo | None = None
    insurance: InsuranceInfo | None = None
    clinical_intake: ClinicalIntake | None = None
    case_details: CaseDetails | None = None
    consents: ConsentsInfo | None = None

    status: PatientStatus = "active"
    deleted_at: str | None = None
    retention_until: str | None = None
    unmasked: bool = False
    created_at: str
    updated_at: str


class RecordProcedure(BaseModel):
    """A billable procedure attached to a medical record (for charge capture)."""
    model_config = ConfigDict(extra="forbid")
    code_type: Literal["cpt", "hcpcs", "custom"] = "cpt"
    code: str = Field(min_length=1, max_length=20)
    units: int = Field(default=1, ge=1, le=99)
    modifiers: list[str] = Field(default_factory=list, max_length=4)


class RecordDiagnosis(BaseModel):
    """An ICD-10 diagnosis attached to a medical record."""
    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(ge=1, le=12)
    code: str = Field(min_length=1, max_length=12)


ResponsibilityMode = Literal["self_pay", "insurance", "mixed"]
ChargeStatus = Literal["not_captured", "pending_capture", "captured", "voided"]


class MedicalRecordCoding(BaseModel):
    """Updatable coding payload for a medical record."""
    model_config = ConfigDict(extra="forbid")
    procedures: list[RecordProcedure] = Field(default_factory=list, max_length=20)
    diagnoses: list[RecordDiagnosis] = Field(default_factory=list, max_length=12)
    responsibility: ResponsibilityMode = "self_pay"


class MedicalRecordCreate(BaseModel):
    record_type: RecordType
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    diagnosis: str | None = None
    treatment: str | None = None


class MedicalRecordPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    patient_id: str
    record_type: RecordType
    title: str
    description: str | None = None
    diagnosis: str | None = None
    treatment: str | None = None
    recorded_by: str
    recorded_by_name: str | None = None
    recorded_at: str
    # Charge-capture fields (iteration 25 — Phase 2). All optional so
    # legacy records render unchanged.
    procedures: list[RecordProcedure] = Field(default_factory=list)
    diagnoses: list[RecordDiagnosis] = Field(default_factory=list)
    responsibility: ResponsibilityMode | None = None
    signed_at: str | None = None
    signed_by: str | None = None
    charge_status: ChargeStatus | None = None
    charge_captured_invoice_id: str | None = None
