/**
 * NPI (National Provider Identifier) validator — frontend mirror of
 * `backend/core/npi.py`. Kept in sync so users get an inline error
 * *before* the round-trip, but the backend remains the source of truth.
 *
 * Checksum: CMS spec. The 10th digit of the NPI is a Luhn check digit
 * computed over the implicit `80840` prefix + the first 9 digits of
 * the NPI. The prefix contributes a fixed 24 to the Luhn sum, so we
 * only need to Luhn-score the 9-digit body and add 24.
 *
 * Passing validation guarantees the NPI is STRUCTURALLY valid only —
 * it does not prove the NPI is registered with NPPES, active, or
 * belongs to the submitter. Surface that caveat in help text.
 */

const PREFIX_CONTRIBUTION = 24; // Luhn sum contribution of the literal "80840".

function luhnBodySum(body9) {
  let total = 0;
  for (let i = 0; i < body9.length; i += 1) {
    const digit = body9.charCodeAt(i) - 48;
    if (i % 2 === 0) {
      const doubled = digit * 2;
      total += doubled > 9 ? doubled - 9 : doubled;
    } else {
      total += digit;
    }
  }
  return total;
}

/**
 * Return the expected NPI check digit (0–9) for a 9-digit body string.
 * Returns null if the input isn't exactly 9 digits.
 */
export function computeCheckDigit(body9) {
  if (typeof body9 !== "string" || !/^\d{9}$/.test(body9)) return null;
  const total = luhnBodySum(body9) + PREFIX_CONTRIBUTION;
  return (10 - (total % 10)) % 10;
}

/**
 * Return true if `value` is a structurally-valid NPI (10 digits +
 * matching Luhn check digit). Leading/trailing whitespace is trimmed;
 * any non-digit characters cause rejection.
 */
export function isValidNpi(value) {
  if (value == null) return false;
  const v = String(value).trim();
  if (!/^\d{10}$/.test(v)) return false;
  const expected = computeCheckDigit(v.slice(0, 9));
  return expected !== null && expected === Number(v[9]);
}

/**
 * Describe the *most specific* problem with an NPI value, or null if
 * it's valid. Returned strings are safe to render directly in inline
 * form hints (no secrets / user input echoed back).
 */
export function describeNpiError(value) {
  const v = value == null ? "" : String(value).trim();
  if (v === "") return "NPI is required.";
  if (!/^\d+$/.test(v)) {
    return "NPI must contain digits only (no dashes, spaces, or letters).";
  }
  if (v.length !== 10) return "NPI must be exactly 10 digits.";
  if (!isValidNpi(v)) {
    return "NPI failed checksum validation. Double-check the number you entered.";
  }
  return null;
}

/**
 * Standard helper text to display alongside the field so the UX is
 * honest about what checksum validation does (and does not) prove.
 * Kept as a constant so copy stays consistent everywhere NPI is
 * collected.
 */
export const NPI_CHECKSUM_DISCLAIMER =
  "Format and checksum are validated locally — this does not confirm NPPES registration status.";
