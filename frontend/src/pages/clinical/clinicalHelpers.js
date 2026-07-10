/**
 * Shared helpers for the Clinical redesign shell (ClinicalTabV2 + its
 * extracted section components).
 *
 * Nothing in here talks to the network — it's pure derivation from
 * already-loaded chart data. That keeps the extracted UI components
 * free of business logic and easy to unit test.
 */

export const NAV_ITEMS = [
  { id: "summary", label: "Summary" },
  { id: "history", label: "History" },
  { id: "diagnoses", label: "Diagnoses" },
  { id: "encounters", label: "Encounters" },
  { id: "care-plan", label: "Care plan" },
  { id: "timeline", label: "Timeline" },
  { id: "imaging", label: "Imaging" },
  { id: "outcomes", label: "Outcomes" },
];

export function getInitials(patient) {
  if (!patient) return "??";
  if (patient.unmasked) {
    const f = (patient.first_name || "").trim();
    const l = (patient.last_name || "").trim();
    const i = `${f.charAt(0)}${l.charAt(0)}`.toUpperCase();
    return i || "??";
  }
  const src = patient.display_name_masked || "";
  const parts = src.replace(/\./g, "").split(/\s+/).filter(Boolean);
  return (parts.map((p) => p.charAt(0)).join("").slice(0, 2) || "??").toUpperCase();
}

export function computeAge(dobIso) {
  if (!dobIso) return null;
  const dob = new Date(dobIso);
  if (Number.isNaN(dob.getTime())) return null;
  const now = new Date();
  let age = now.getFullYear() - dob.getFullYear();
  const m = now.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && now.getDate() < dob.getDate())) age -= 1;
  return age >= 0 && age < 130 ? age : null;
}

export function pickNextAppointment(appointments) {
  if (!Array.isArray(appointments)) return null;
  const now = Date.now();
  return appointments
    .filter(
      (a) =>
        a?.start_time &&
        new Date(a.start_time).getTime() > now &&
        !["cancelled", "canceled", "no_show"].includes(a.status),
    )
    .sort((a, b) => new Date(a.start_time) - new Date(b.start_time))[0];
}

export function pickActiveEpisode(episodes) {
  if (!Array.isArray(episodes)) return null;
  return (
    episodes.find((e) => e.status === "active") ||
    episodes.find((e) => e.status === "on_hold") ||
    null
  );
}

export function pickPrimaryDiagnosis(diagnoses) {
  if (!Array.isArray(diagnoses)) return null;
  return (
    diagnoses.find((d) => d.is_primary && d.status === "active") ||
    diagnoses.find((d) => d.status === "active") ||
    null
  );
}

export function extractRedFlagFindings(history) {
  const rf = history?.red_flag_screening;
  if (!rf || typeof rf !== "object") return { positives: [], hasScreening: false };
  const positives = [];
  for (const [k, v] of Object.entries(rf)) {
    if (v === true) positives.push(k.replace(/_/g, " "));
  }
  return { positives, hasScreening: Object.keys(rf).length > 0 };
}
