/**
 * U.S. phone number utilities — canonical form `(XXX) XXX-XXXX`.
 *
 * Mirrors `backend/core/phone.py`. US-only by design; international
 * numbers and extension handling live elsewhere. Display is
 * permissive — only 10-digit values get reformatted, everything else
 * echoes back unchanged so legacy data never gets mangled.
 */

const DIGITS_ONLY_RE = /\D+/g;

function digitsOnly(value) {
  if (value == null) return "";
  return String(value).replace(DIGITS_ONLY_RE, "");
}

/**
 * Return the canonical 10-digit string (or null for empty input).
 * Returns null when the input is non-empty but doesn't look like a
 * US phone — call sites can treat that as "still typing / invalid"
 * without throwing.
 */
export function normalizePhone(value) {
  if (value == null) return null;
  const raw = String(value).trim();
  if (raw === "") return null;
  let digits = digitsOnly(raw);
  if (digits.length === 11 && digits[0] === "1") digits = digits.slice(1);
  return digits.length === 10 ? digits : null;
}

/**
 * True iff the value is empty OR normalises to exactly 10 digits.
 * Matches the backend's `is_valid_us_phone` semantics.
 */
export function isValidPhone(value) {
  if (value == null) return true;
  const raw = String(value).trim();
  if (raw === "") return true;
  return normalizePhone(raw) !== null;
}

/**
 * Format a stored phone value for display. 10-digit canonical → pretty
 * `(XXX) XXX-XXXX`. Anything else echoes the original value unchanged,
 * so legacy formats (`+1-555-0102`) render safely.
 */
export function formatPhoneDisplay(value) {
  if (value == null) return "";
  const raw = String(value);
  if (raw.trim() === "") return "";
  const digits = normalizePhone(raw);
  if (!digits) return raw;
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6, 10)}`;
}

/**
 * Strip formatting for search APIs. No length enforcement here — the
 * caller decides what counts as a real query.
 */
export function searchNormalize(value) {
  return digitsOnly(value);
}

/**
 * Pretty-format an in-progress value as the user types: `(XXX)`,
 * `(XXX) XXX`, `(XXX) XXX-XXXX`. Always displays at most 10 digits of
 * input. Designed to be wired into `onChange` via:
 *
 *   const handleChange = (e) => setPhone(formatAsTyped(e.target.value));
 *
 * Storage should still use `normalizePhone` before submitting.
 */
export function formatAsTyped(value) {
  const digits = digitsOnly(value).slice(0, 10);
  if (digits.length === 0) return "";
  if (digits.length <= 3) return `(${digits}`;
  if (digits.length <= 6) return `(${digits.slice(0, 3)}) ${digits.slice(3)}`;
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
}
