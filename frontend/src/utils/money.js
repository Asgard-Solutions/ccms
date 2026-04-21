/**
 * Money utilities — cents <-> display string.
 *
 * The canonical money shape across the billing API is `amount_cents`
 * (integer). The UI never parses floats; it formats cents to USD at
 * render time and parses user input back to cents on submit.
 */

export const DEFAULT_CURRENCY = "USD";

/** Format integer cents as a localised currency string. */
export function formatCents(cents, currency = DEFAULT_CURRENCY) {
  if (cents == null || Number.isNaN(Number(cents))) return "—";
  const n = Number(cents) / 100;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

/**
 * Parse a user-entered dollar string into integer cents.
 * Accepts "12", "12.5", "12.50", "$12.50", "1,200.00".
 * Returns `null` for unparseable input (caller should surface an error).
 */
export function parseDollarsToCents(value) {
  if (value == null) return null;
  const stripped = String(value).replace(/[$,\s]/g, "").trim();
  if (stripped === "" || stripped === "-") return null;
  if (!/^-?\d+(\.\d{1,4})?$/.test(stripped)) return null;
  const n = Number(stripped);
  if (!Number.isFinite(n)) return null;
  // Round to nearest cent to avoid 0.1+0.2 drift.
  return Math.round(n * 100);
}

/** Clamp a cents value to a range, useful for allocation inputs. */
export function clampCents(cents, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  if (cents == null) return min;
  return Math.min(Math.max(cents, min), max);
}

/** Sum an array of objects' amount_cents field. */
export function sumAmountCents(rows) {
  return (rows || []).reduce((acc, r) => acc + (Number(r?.amount_cents) || 0), 0);
}
