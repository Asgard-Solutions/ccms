/**
 * DEA registration-number validator — frontend mirror of
 * `backend/core/dea.py`. Keeps inline form validation in sync with the
 * server side; backend remains the source of truth.
 *
 * Structure: 2 letters + 6 digits + 1 check digit (9 chars total).
 *   - char 1  → registrant-type letter (DEA-published set)
 *   - char 2  → any uppercase letter (conventionally last-name initial;
 *               exceptions allowed per DEA, so we only require alpha)
 *   - chars 3-8 → digits
 *   - char 9  → single-digit Luhn-ish checksum
 *
 * Checksum:
 *   odd  = digit[1] + digit[3] + digit[5]
 *   even = (digit[2] + digit[4] + digit[6]) * 2
 *   check = (odd + even) % 10
 *
 * Passing this check means the DEA number is STRUCTURALLY plausible
 * only — it does NOT prove active federal registration.
 */

export const VALID_DEA_REGISTRANT_CODES = "ABCDEFGHJKLMPRSTUX";

export function computeDeaCheckDigit(body6) {
  if (typeof body6 !== "string" || !/^\d{6}$/.test(body6)) return null;
  const odd = Number(body6[0]) + Number(body6[2]) + Number(body6[4]);
  const even = (Number(body6[1]) + Number(body6[3]) + Number(body6[5])) * 2;
  return (odd + even) % 10;
}

/**
 * Returns true iff the input — trimmed + upper-cased — is a
 * structurally-valid DEA number.
 */
export function isValidDea(value) {
  if (value == null) return false;
  const v = String(value).trim().toUpperCase();
  if (!/^[A-Z]{2}\d{7}$/.test(v)) return false;
  if (!VALID_DEA_REGISTRANT_CODES.includes(v[0])) return false;
  const expected = computeDeaCheckDigit(v.slice(2, 8));
  return expected !== null && expected === Number(v[8]);
}

/**
 * Describe the *most specific* problem with a DEA value, or null if
 * it is structurally valid. Strings are safe to render directly in
 * inline form hints.
 */
export function describeDeaError(value) {
  const v = value == null ? "" : String(value).trim().toUpperCase();
  if (v === "") return "DEA number is required.";
  if (v.length !== 9) return "DEA number must be exactly 9 characters.";
  if (!/^[A-Z]{2}/.test(v)) {
    return "DEA number must begin with two letters followed by 7 digits.";
  }
  if (!VALID_DEA_REGISTRANT_CODES.includes(v[0])) {
    return `'${v[0]}' is not a recognised DEA registrant-type code.`;
  }
  if (!/^\d{7}$/.test(v.slice(2))) {
    return "DEA number positions 3-9 must all be digits.";
  }
  if (!isValidDea(v)) {
    return "DEA number failed checksum validation. Double-check the number you entered.";
  }
  return null;
}

/**
 * Returns true when the 2nd char of the DEA matches the first letter
 * of `lastName`. Used only for SOFT, non-blocking warnings — DEA
 * itself allows approved exceptions (legal name changes, institutional
 * DEAs, etc.).
 */
export function matchesLastNameInitial(dea, lastName) {
  if (!dea || !lastName) return false;
  const d = String(dea).trim().toUpperCase();
  const f = String(lastName).trim().slice(0, 1).toUpperCase();
  return d.length >= 2 && f !== "" && d[1] === f;
}

export const DEA_CHECKSUM_DISCLAIMER =
  "Format and checksum are validated locally — this does not confirm federal DEA registration status.";
