/* Currency / date helpers for report cells — intentionally tiny */

export function formatCell(value, type) {
  if (value === null || value === undefined || value === "") return "—";
  if (type === "currency") {
    const n = Number(value);
    if (Number.isNaN(n)) return value;
    const sign = n < 0 ? "-" : "";
    const abs = Math.abs(n);
    return `${sign}$${(Math.floor(abs / 100)).toLocaleString()}.${String(abs % 100).padStart(2, "0")}`;
  }
  if (type === "integer" || type === "number") {
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString() : value;
  }
  if (type === "boolean") return value ? "Yes" : "No";
  if (type === "datetime" && typeof value === "string" && value.length >= 10) {
    return value.replace("T", " ").slice(0, 16);
  }
  if (type === "date" && typeof value === "string") {
    return value.slice(0, 10);
  }
  return value;
}

export function readableCategory(cat) {
  return cat;
}
