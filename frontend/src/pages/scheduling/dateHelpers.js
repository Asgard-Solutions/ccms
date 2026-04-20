/**
 * Date helpers shared across the Scheduling module.
 * All functions accept/return plain Date objects except helpers that
 * explicitly say ISO. Week is Monday-first to match the previous Calendar UX.
 */

export const VIEWS = ["day", "week", "month", "year"];

export const WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const WEEKDAY_LONG = [
  "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
];
export const MONTH_LONG = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export function startOfDay(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

export function endOfDay(d) {
  const x = new Date(d);
  x.setHours(23, 59, 59, 999);
  return x;
}

/** Start of ISO-week (Monday) at 00:00 local. */
export function startOfWeek(d) {
  const x = startOfDay(d);
  const dayIdx = (x.getDay() + 6) % 7; // 0 = Monday
  x.setDate(x.getDate() - dayIdx);
  return x;
}

export function endOfWeek(d) {
  const x = startOfWeek(d);
  x.setDate(x.getDate() + 7);
  x.setMilliseconds(x.getMilliseconds() - 1);
  return x;
}

export function startOfMonth(d) {
  const x = new Date(d.getFullYear(), d.getMonth(), 1);
  return x;
}

export function endOfMonth(d) {
  const x = new Date(d.getFullYear(), d.getMonth() + 1, 1);
  x.setMilliseconds(x.getMilliseconds() - 1);
  return x;
}

export function startOfYear(d) {
  return new Date(d.getFullYear(), 0, 1);
}

export function endOfYear(d) {
  const x = new Date(d.getFullYear() + 1, 0, 1);
  x.setMilliseconds(x.getMilliseconds() - 1);
  return x;
}

export function addDays(d, n) {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

export function addMonths(d, n) {
  const x = new Date(d);
  x.setMonth(x.getMonth() + n);
  return x;
}

export function addYears(d, n) {
  const x = new Date(d);
  x.setFullYear(x.getFullYear() + n);
  return x;
}

export function sameDay(a, b) {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export function isToday(d) {
  return sameDay(d, new Date());
}

export function isoDateKey(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Visible range for a given view anchored at `date`. Inclusive start, exclusive end. */
export function visibleRange(view, date) {
  if (view === "day") return { start: startOfDay(date), end: endOfDay(date) };
  if (view === "week") return { start: startOfWeek(date), end: endOfWeek(date) };
  if (view === "month") {
    // Expand to full weeks so grid rendering can safely cover leading/trailing days.
    const gridStart = startOfWeek(startOfMonth(date));
    const gridEnd = endOfWeek(endOfMonth(date));
    return { start: gridStart, end: gridEnd };
  }
  if (view === "year") return { start: startOfYear(date), end: endOfYear(date) };
  return { start: startOfDay(date), end: endOfDay(date) };
}

/** Step one unit in either direction for the given view. */
export function stepDate(view, date, direction /* -1 | 1 */) {
  if (view === "day") return addDays(date, direction);
  if (view === "week") return addDays(date, 7 * direction);
  if (view === "month") return addMonths(date, direction);
  if (view === "year") return addYears(date, direction);
  return date;
}

/** Human label for the toolbar (changes per view). */
export function rangeLabel(view, date) {
  if (view === "day") {
    return date.toLocaleDateString("en-US", {
      weekday: "long", month: "long", day: "numeric", year: "numeric",
    });
  }
  if (view === "week") {
    const s = startOfWeek(date);
    const e = addDays(s, 6);
    const sameMonth = s.getMonth() === e.getMonth();
    const sameYear = s.getFullYear() === e.getFullYear();
    if (sameMonth && sameYear) {
      const month = s.toLocaleDateString("en-US", { month: "short" });
      return `${month} ${s.getDate()} – ${e.getDate()}, ${s.getFullYear()}`;
    }
    if (sameYear) {
      const left = s.toLocaleDateString("en-US", { month: "short", day: "numeric" });
      const right = e.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
      return `${left} – ${right}`;
    }
    const left = s.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    const right = e.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    return `${left} – ${right}`;
  }
  if (view === "month") {
    return date.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  }
  if (view === "year") {
    return String(date.getFullYear());
  }
  return "";
}

/** Group appointments into a Map keyed by local-date ISO (YYYY-MM-DD). */
export function groupByDay(appts) {
  const m = new Map();
  for (const a of appts || []) {
    const key = isoDateKey(new Date(a.start_time));
    if (!m.has(key)) m.set(key, []);
    m.get(key).push(a);
  }
  for (const list of m.values()) {
    list.sort((x, y) => new Date(x.start_time) - new Date(y.start_time));
  }
  return m;
}

/** Build a Month grid: array of weeks, each a 7-day array of Date. */
export function buildMonthGrid(date) {
  const { start, end } = visibleRange("month", date);
  const weeks = [];
  let cursor = new Date(start);
  while (cursor <= end) {
    const row = Array.from({ length: 7 }, (_, i) => addDays(cursor, i));
    weeks.push(row);
    cursor = addDays(cursor, 7);
  }
  return weeks;
}
