import { useEffect, useMemo, useRef, useState } from "react";
import { isoDateKey, sameDay } from "./dateHelpers";

/**
 * Day view — vertical timeline with 15-minute slots.
 *
 * Visible hours are a placeholder (07:00 – 20:00) until clinic hours land.
 * Per-appointment block shows patient name, phone, time, provider, reason.
 * Overlapping appointments are laid out side-by-side in computed columns.
 * Clicking an appointment opens reschedule; clicking an empty slot opens the
 * booking dialog pre-filled with that slot's time.
 */

const SLOT_MINUTES = 15;
const SLOT_HEIGHT = 16; // px per 15-minute slot
const DEFAULT_START_HOUR = 7;
const DEFAULT_END_HOUR = 20;
const GUTTER_W = 72; // px

function formatHHMM(d) {
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  });
}

function minutesSince(dayStart, iso) {
  return (new Date(iso).getTime() - dayStart.getTime()) / 60000;
}

/**
 * Assign each appointment a column so that overlapping appointments sit
 * side-by-side. Classic interval scheduling: sort by start, place in first
 * free column whose previous occupant ended at or before our start.
 * Returns [{appt, col, totalCols}] preserving input identity.
 */
function layoutColumns(appts) {
  if (!appts.length) return [];
  const sorted = [...appts].sort(
    (a, b) => new Date(a.start_time) - new Date(b.start_time)
  );

  // Group into overlap clusters (each cluster shares a totalCols width).
  const clusters = [];
  let cluster = [];
  let clusterEnd = 0;
  for (const a of sorted) {
    const s = new Date(a.start_time).getTime();
    const e = new Date(a.end_time).getTime();
    if (cluster.length && s >= clusterEnd) {
      clusters.push(cluster);
      cluster = [];
      clusterEnd = 0;
    }
    cluster.push(a);
    if (e > clusterEnd) clusterEnd = e;
  }
  if (cluster.length) clusters.push(cluster);

  const out = [];
  for (const group of clusters) {
    // Column assignment within this cluster.
    const cols = []; // array of end timestamps
    const assigned = group.map((a) => {
      const s = new Date(a.start_time).getTime();
      const e = new Date(a.end_time).getTime();
      let placed = -1;
      for (let i = 0; i < cols.length; i++) {
        if (cols[i] <= s) {
          cols[i] = e;
          placed = i;
          break;
        }
      }
      if (placed === -1) {
        cols.push(e);
        placed = cols.length - 1;
      }
      return { a, col: placed };
    });
    const totalCols = cols.length;
    for (const { a, col } of assigned) out.push({ appt: a, col, totalCols });
  }
  return out;
}

export default function DayView({
  date,
  appointments,
  canBook,
  onOpenAppointment,
  onCreateAt,
}) {
  const startHour = DEFAULT_START_HOUR;
  const endHour = DEFAULT_END_HOUR;
  const totalSlots = (endHour - startHour) * (60 / SLOT_MINUTES);
  const totalHeight = totalSlots * SLOT_HEIGHT;
  const dayStart = useMemo(() => {
    const d = new Date(date);
    d.setHours(startHour, 0, 0, 0);
    return d;
  }, [date, startHour]);
  const dayEnd = useMemo(() => {
    const d = new Date(date);
    d.setHours(endHour, 0, 0, 0);
    return d;
  }, [date, endHour]);

  const key = isoDateKey(date);
  const visible = useMemo(
    () =>
      (appointments || []).filter((a) => {
        const s = new Date(a.start_time);
        return sameDay(s, date);
      }),
    [appointments, date]
  );

  // Appointments that overlap the visible window at all.
  const inWindow = useMemo(
    () =>
      visible.filter((a) => {
        const s = new Date(a.start_time).getTime();
        const e = new Date(a.end_time).getTime();
        return e > dayStart.getTime() && s < dayEnd.getTime();
      }),
    [visible, dayStart, dayEnd]
  );

  const outsideWindow = useMemo(
    () => visible.length - inWindow.length,
    [visible, inWindow]
  );

  const laid = useMemo(() => layoutColumns(inWindow), [inWindow]);

  // Current time indicator
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(t);
  }, []);
  const showNow =
    sameDay(now, date) &&
    now.getTime() >= dayStart.getTime() &&
    now.getTime() <= dayEnd.getTime();
  const nowTop = showNow
    ? ((now.getTime() - dayStart.getTime()) / 60_000) * (SLOT_HEIGHT / SLOT_MINUTES)
    : 0;

  // Auto-scroll to bring "now" (or 08:00) into view once on mount.
  const scrollRef = useRef(null);
  useEffect(() => {
    if (!scrollRef.current) return;
    const el = scrollRef.current;
    const target = showNow ? Math.max(0, nowTop - 160) : SLOT_HEIGHT * 4;
    el.scrollTop = target;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  function slotStartAt(slotIdx) {
    const d = new Date(dayStart);
    d.setMinutes(dayStart.getMinutes() + slotIdx * SLOT_MINUTES);
    return d;
  }

  return (
    <div data-testid={`scheduling-day-${key}`} className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {date.toLocaleDateString("en-US", { weekday: "long" })}
          </div>
          <div className="font-display text-2xl">
            {date.toLocaleDateString("en-US", {
              month: "long",
              day: "numeric",
              year: "numeric",
            })}
          </div>
        </div>
        <div
          data-testid="scheduling-day-count"
          className="rounded-sm bg-primary/10 px-3 py-1 text-sm font-semibold text-primary"
        >
          {visible.length} {visible.length === 1 ? "appointment" : "appointments"}
        </div>
      </div>

      {outsideWindow > 0 && (
        <div
          data-testid="scheduling-day-outside-window"
          className="rounded-sm border border-border bg-warning-soft px-3 py-2 text-xs text-warning"
        >
          {outsideWindow} appointment{outsideWindow === 1 ? "" : "s"} outside the
          default {startHour}:00–{endHour}:00 window are not shown.
        </div>
      )}

      <div
        ref={scrollRef}
        data-testid="scheduling-day-timeline"
        className="relative max-h-[640px] overflow-y-auto rounded-sm border border-border bg-card"
      >
        <div className="relative" style={{ height: `${totalHeight}px` }}>
          {/* Time gutter: hour labels */}
          {Array.from({ length: endHour - startHour + 1 }, (_, i) => {
            const hour = startHour + i;
            const top = i * (60 / SLOT_MINUTES) * SLOT_HEIGHT;
            return (
              <div
                key={`hr-${hour}`}
                className="absolute left-0 flex w-full items-start"
                style={{ top: `${top}px` }}
              >
                <div
                  className="shrink-0 -translate-y-2 pr-2 text-right text-[11px] font-semibold uppercase tracking-wider text-muted-foreground"
                  style={{ width: `${GUTTER_W}px` }}
                >
                  {new Date(0, 0, 0, hour).toLocaleTimeString("en-US", {
                    hour: "numeric",
                  })}
                </div>
              </div>
            );
          })}

          {/* Slot grid background with clickable empty slots */}
          <div
            className="absolute inset-y-0 right-0"
            style={{ left: `${GUTTER_W}px` }}
          >
            {Array.from({ length: totalSlots }, (_, i) => {
              const slotStart = slotStartAt(i);
              const isHourBoundary = i % 4 === 0;
              const isHalfHour = i % 4 === 2;
              const label = formatHHMM(slotStart);
              return (
                <button
                  key={`slot-${i}`}
                  type="button"
                  data-testid={`scheduling-day-slot-${isoDateKey(date)}-${String(slotStart.getHours()).padStart(2, "0")}-${String(slotStart.getMinutes()).padStart(2, "0")}`}
                  aria-label={`Book appointment at ${label}`}
                  disabled={!canBook}
                  onClick={() => canBook && onCreateAt?.(slotStart)}
                  className={`block w-full border-border text-left transition-colors ${
                    isHourBoundary
                      ? "border-t-2"
                      : isHalfHour
                      ? "border-t border-dashed"
                      : "border-t border-border/40"
                  } ${canBook ? "hover:bg-primary/5" : "cursor-default"}`}
                  style={{ height: `${SLOT_HEIGHT}px` }}
                />
              );
            })}
          </div>

          {/* Appointment blocks */}
          <div
            className="pointer-events-none absolute inset-y-0 right-0"
            style={{ left: `${GUTTER_W}px` }}
          >
            {laid.map(({ appt, col, totalCols }) => {
              const topM = minutesSince(dayStart, appt.start_time);
              const durM = Math.max(
                SLOT_MINUTES,
                minutesSince(dayStart, appt.end_time) - topM
              );
              const top = Math.max(0, topM) * (SLOT_HEIGHT / SLOT_MINUTES);
              const height = durM * (SLOT_HEIGHT / SLOT_MINUTES);
              const widthPct = 100 / totalCols;
              const leftPct = col * widthPct;
              const cancelled = appt.status === "cancelled";
              return (
                <button
                  key={appt.id}
                  type="button"
                  data-testid={`scheduling-day-appt-${appt.id}`}
                  onClick={() => onOpenAppointment?.(appt)}
                  className={`pointer-events-auto absolute overflow-hidden rounded-sm border-l-2 p-2 text-left text-xs shadow-sm transition-colors ${
                    cancelled
                      ? "border-destructive bg-destructive-soft text-destructive line-through"
                      : "border-primary bg-primary/15 text-foreground hover:bg-primary/25"
                  }`}
                  style={{
                    top: `${top}px`,
                    height: `${Math.max(height - 2, SLOT_HEIGHT - 2)}px`,
                    left: `calc(${leftPct}% + 2px)`,
                    width: `calc(${widthPct}% - 4px)`,
                  }}
                  aria-label={`Appointment for ${appt.patient_name} at ${formatHHMM(new Date(appt.start_time))}`}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate font-medium text-primary">
                      {appt.patient_name || "Unknown patient"}
                    </span>
                    <span className="shrink-0 text-[10px] font-mono text-muted-foreground">
                      {formatHHMM(new Date(appt.start_time))}
                    </span>
                  </div>
                  {appt.patient_phone && (
                    <div
                      data-testid={`scheduling-day-appt-phone-${appt.id}`}
                      className="truncate font-mono text-[11px] text-muted-foreground"
                    >
                      {appt.patient_phone}
                    </div>
                  )}
                  {appt.provider_name && height >= SLOT_HEIGHT * 2 && (
                    <div className="mt-1 truncate text-[11px] text-muted-foreground">
                      {appt.provider_name}
                    </div>
                  )}
                  {appt.reason && height >= SLOT_HEIGHT * 3 && (
                    <div className="mt-0.5 truncate text-[11px] italic text-muted-foreground">
                      {appt.reason}
                    </div>
                  )}
                </button>
              );
            })}
          </div>

          {/* Current-time indicator */}
          {showNow && (
            <div
              data-testid="scheduling-day-now-indicator"
              className="pointer-events-none absolute right-0 z-10 flex items-center"
              style={{ top: `${nowTop - 1}px`, left: `${GUTTER_W - 4}px` }}
            >
              <span className="h-2 w-2 shrink-0 rounded-full bg-destructive" />
              <span className="ml-[-1px] block h-[2px] flex-1 bg-destructive" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
