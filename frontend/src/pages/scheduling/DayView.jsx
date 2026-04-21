import { useEffect, useMemo, useRef, useState } from "react";
import { isoDateKey, sameDay } from "./dateHelpers";
import { extractDaySpan } from "./useClinicHours";
import { Button } from "../../components/ui/button";

/**
 * Day view — vertical timeline with 15-minute slots.
 *
 * Visible hours are driven by the clinic's configured hours for that
 * weekday (Task 9): open − 2h through close + 2h. Falls back to 07:00–20:00
 * when clinic hours haven't been configured.
 *
 * Per-appointment block shows patient name, phone, time, provider, reason.
 * Overlapping appointments are laid out side-by-side in computed columns.
 * Clicking an appointment opens reschedule; clicking an empty slot opens the
 * booking dialog pre-filled with that slot's time.
 */

const SLOT_MINUTES = 15;
const SLOT_HEIGHT = 16; // px per 15-minute slot
const GUTTER_W = 72; // px
const BUFFER_MINUTES = 120; // 2 hours before/after clinic hours
const FALLBACK_OPEN = 7 * 60; // 07:00
const FALLBACK_CLOSE = 20 * 60; // 20:00 (so fallback window = same 07:00–20:00)
const CLOSED_DAY_OPEN = 7 * 60;
const CLOSED_DAY_CLOSE = 19 * 60; // nominal window for exception viewing

function formatHHMM(d) {
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  });
}

function minutesSince(dayStart, iso) {
  return (new Date(iso).getTime() - dayStart.getTime()) / 60000;
}

function layoutColumns(appts) {
  if (!appts.length) return [];
  // Primary sort: start_time asc (so the greedy column fit is chronological).
  // Secondary sort: active appointments BEFORE cancelled ones at the same
  // start. This guarantees active rows claim the leftmost column while
  // cancelled/struck-through rows slide to the right when two appointments
  // collide on the same time slot.
  const sorted = [...appts].sort((a, b) => {
    const ta = new Date(a.start_time) - new Date(b.start_time);
    if (ta !== 0) return ta;
    const aCancelled = a.status === "cancelled" ? 1 : 0;
    const bCancelled = b.status === "cancelled" ? 1 : 0;
    return aCancelled - bCancelled;
  });
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
    const cols = [];
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

/**
 * Compute the default window in minutes-since-midnight for the selected day.
 * Honors `expanded`: when true the window is widened to enclose every
 * appointment on that day (ensuring nothing is silently hidden).
 */
function computeWindow({ hours, date, expanded, inDayAppts }) {
  const span = extractDaySpan(hours, date);
  let startM;
  let endM;
  let isClosed = span.isClosed;

  if (!hours) {
    startM = FALLBACK_OPEN;
    endM = FALLBACK_CLOSE;
  } else if (isClosed) {
    startM = CLOSED_DAY_OPEN;
    endM = CLOSED_DAY_CLOSE;
  } else if (span.openMinutes != null && span.closeMinutes != null) {
    startM = Math.max(0, span.openMinutes - BUFFER_MINUTES);
    endM = Math.min(24 * 60, span.closeMinutes + BUFFER_MINUTES);
  } else {
    startM = FALLBACK_OPEN;
    endM = FALLBACK_CLOSE;
  }

  // Expansion: if user opted in OR the day is closed and has appointments
  // outside the nominal view, widen to cover every in-day appt.
  if (expanded && inDayAppts.length) {
    const dayStart = new Date(date);
    dayStart.setHours(0, 0, 0, 0);
    const minsFromMidnight = (iso) =>
      (new Date(iso).getTime() - dayStart.getTime()) / 60000;
    const earliest = Math.min(...inDayAppts.map((a) => minsFromMidnight(a.start_time)));
    const latest = Math.max(...inDayAppts.map((a) => minsFromMidnight(a.end_time)));
    startM = Math.max(0, Math.floor(Math.min(startM, earliest) / 15) * 15);
    endM = Math.min(24 * 60, Math.ceil(Math.max(endM, latest) / 15) * 15);
  }

  // Snap to 15-minute bounds and enforce a 15-minute minimum window.
  startM = Math.floor(startM / 15) * 15;
  endM = Math.ceil(endM / 15) * 15;
  if (endM <= startM) endM = startM + 15;

  return { startM, endM, isClosed };
}

export default function DayView({
  date,
  appointments,
  canBook,
  hours,
  hoursLoading,
  hoursConfigured,
  includeCancelled = false,
  onOpenAppointment,
  onCreateAt,
}) {
  const [expanded, setExpanded] = useState(false);

  const key = isoDateKey(date);
  const visible = useMemo(
    () =>
      (appointments || []).filter((a) => sameDay(new Date(a.start_time), date)),
    [appointments, date]
  );
  const activeCount = useMemo(
    () => visible.filter((a) => a.status !== "cancelled").length,
    [visible]
  );
  const cancelledCount = visible.length - activeCount;

  const { startM, endM, isClosed } = useMemo(
    () => computeWindow({ hours, date, expanded, inDayAppts: visible }),
    [hours, date, expanded, visible]
  );

  const dayStart = useMemo(() => {
    const d = new Date(date);
    d.setHours(0, 0, 0, 0);
    d.setMinutes(startM);
    return d;
  }, [date, startM]);
  const dayEnd = useMemo(() => {
    const d = new Date(date);
    d.setHours(0, 0, 0, 0);
    d.setMinutes(endM);
    return d;
  }, [date, endM]);

  const totalSlots = (endM - startM) / SLOT_MINUTES;
  const totalHeight = totalSlots * SLOT_HEIGHT;

  const inWindow = useMemo(
    () =>
      visible.filter((a) => {
        const s = new Date(a.start_time).getTime();
        const e = new Date(a.end_time).getTime();
        return e > dayStart.getTime() && s < dayEnd.getTime();
      }),
    [visible, dayStart, dayEnd]
  );

  const outsideWindow = visible.filter((a) => a.status !== "cancelled").length - inWindow.filter((a) => a.status !== "cancelled").length;

  const laid = useMemo(() => layoutColumns(inWindow), [inWindow]);

  // Current-time indicator
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

  // Auto-scroll to "now" (or a reasonable anchor) once per selected day.
  const scrollRef = useRef(null);
  useEffect(() => {
    if (!scrollRef.current) return;
    const el = scrollRef.current;
    const target = showNow ? Math.max(0, nowTop - 160) : 0;
    el.scrollTop = target;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, startM, endM]);

  function slotStartAt(slotIdx) {
    const d = new Date(dayStart);
    d.setMinutes(dayStart.getMinutes() + slotIdx * SLOT_MINUTES);
    return d;
  }

  const startH = Math.floor(startM / 60);
  const endH = Math.ceil(endM / 60);

  return (
    <div data-testid={`scheduling-day-${key}`} className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
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
        <div className="flex items-center gap-2">
          {isClosed && (
            <span
              data-testid="scheduling-day-closed-badge"
              className="rounded-sm bg-warning-soft px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-warning"
            >
              Clinic closed
            </span>
          )}
          <span
            data-testid="scheduling-day-count"
            className="rounded-sm bg-primary/10 px-3 py-1 text-sm font-semibold text-primary"
          >
            {activeCount} {activeCount === 1 ? "appointment" : "appointments"}
          </span>
          {cancelledCount > 0 && includeCancelled && (
            <span
              data-testid="scheduling-day-cancelled-count"
              className="rounded-sm bg-destructive-soft px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-destructive"
              title={`${cancelledCount} cancelled`}
            >
              {cancelledCount} canceled
            </span>
          )}
        </div>
      </div>

      {!hoursLoading && !hoursConfigured && (
        <div
          data-testid="scheduling-day-no-hours-notice"
          className="rounded-sm border border-border bg-muted px-3 py-2 text-xs text-muted-foreground"
        >
          Clinic hours not configured — showing default 07:00–20:00 window. Admins
          can set hours in Clinic settings.
        </div>
      )}

      {isClosed && !expanded && (
        <div
          data-testid="scheduling-day-closed-banner"
          className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border bg-warning-soft px-3 py-2 text-xs text-warning"
        >
          <span>
            Clinic is marked closed on {date.toLocaleDateString("en-US", { weekday: "long" })}.
            {outsideWindow > 0
              ? ` ${outsideWindow} appointment${outsideWindow === 1 ? "" : "s"} still booked — expand to view.`
              : " Showing a limited window for exceptions."}
          </span>
          {outsideWindow > 0 && (
            <Button
              variant="outline"
              size="sm"
              data-testid="scheduling-day-expand-btn"
              onClick={() => setExpanded(true)}
              className="rounded-sm"
            >
              Show all appointments
            </Button>
          )}
        </div>
      )}

      {!isClosed && outsideWindow > 0 && !expanded && (
        <div
          data-testid="scheduling-day-outside-window"
          className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border bg-warning-soft px-3 py-2 text-xs text-warning"
        >
          <span>
            {outsideWindow} appointment{outsideWindow === 1 ? "" : "s"} outside the
            configured {String(startH).padStart(2, "0")}:00–{String(endH).padStart(2, "0")}:00{" "}
            window {outsideWindow === 1 ? "is" : "are"} hidden.
          </span>
          <Button
            variant="outline"
            size="sm"
            data-testid="scheduling-day-expand-btn"
            onClick={() => setExpanded(true)}
            className="rounded-sm"
          >
            Show all appointments
          </Button>
        </div>
      )}

      {expanded && (
        <button
          type="button"
          data-testid="scheduling-day-collapse-btn"
          onClick={() => setExpanded(false)}
          className="text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground"
        >
          Collapse to clinic hours
        </button>
      )}

      <div
        ref={scrollRef}
        data-testid="scheduling-day-timeline"
        className="relative max-h-[640px] overflow-y-auto rounded-sm border border-border bg-card"
      >
        <div className="relative" style={{ height: `${totalHeight}px` }}>
          {/* Time gutter: hour labels */}
          {Array.from({ length: endH - startH + 1 }, (_, i) => {
            const hour = startH + i;
            // Position of this hour label, in minutes from window start:
            const posMin = hour * 60 - startM;
            if (posMin < 0 || posMin > endM - startM) return null;
            const top = posMin * (SLOT_HEIGHT / SLOT_MINUTES);
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

          {/* Slot grid (buttons for booking) */}
          <div
            className="absolute inset-y-0 right-0"
            style={{ left: `${GUTTER_W}px` }}
          >
            {Array.from({ length: totalSlots }, (_, i) => {
              const slotStart = slotStartAt(i);
              const minuteOfHour = slotStart.getMinutes();
              const isHourBoundary = minuteOfHour === 0;
              const isHalfHour = minuteOfHour === 30;
              const label = formatHHMM(slotStart);
              return (
                <button
                  key={`slot-${i}`}
                  type="button"
                  data-testid={`scheduling-day-slot-${key}-${String(slotStart.getHours()).padStart(2, "0")}-${String(slotStart.getMinutes()).padStart(2, "0")}`}
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
              // Cancelled appointments occupy only the right half of their
              // column so the left half of the same time band remains a
              // fully clickable booking surface. Active blocks still take
              // the full column width.
              const commonStyle = cancelled
                ? {
                    top: `${top}px`,
                    height: `${Math.max(height - 2, SLOT_HEIGHT - 2)}px`,
                    left: `calc(${leftPct + widthPct / 2}% + 2px)`,
                    width: `calc(${widthPct / 2}% - 4px)`,
                  }
                : {
                    top: `${top}px`,
                    height: `${Math.max(height - 2, SLOT_HEIGHT - 2)}px`,
                    left: `calc(${leftPct}% + 2px)`,
                    width: `calc(${widthPct}% - 4px)`,
                  };
              if (cancelled) {
                // Cancelled blocks MUST NOT block slot clicks (the slot is
                // bookable again). Render as a pointer-events:none overlay
                // with a thin dashed border, strikethrough patient name,
                // and a "Canceled" pill. Full details are surfaced via the
                // `title` tooltip so operators get the history context.
                // A small "Open" pill in the top-right corner is explicitly
                // clickable so admins/doctors can still reach the appointment
                // (needed for the Phase-3 exception-launch workflow for
                // same-day documentation against a cancelled appt).
                return (
                  <div
                    key={appt.id}
                    data-testid={`scheduling-day-appt-${appt.id}`}
                    data-cancelled="true"
                    role="presentation"
                    aria-label={`Cancelled appointment for ${appt.patient_name} at ${formatHHMM(new Date(appt.start_time))} — slot is bookable`}
                    title={`Canceled • ${appt.patient_name || "Unknown"} • ${formatHHMM(new Date(appt.start_time))}${appt.reason ? ` • ${appt.reason}` : ""}`}
                    className="pointer-events-none absolute flex flex-col gap-0.5 overflow-hidden rounded-sm border-2 border-dashed border-destructive/60 bg-destructive-soft/70 p-2 text-left text-xs"
                    style={commonStyle}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate font-medium text-destructive line-through">
                        {appt.patient_name || "Unknown patient"}
                      </span>
                      <button
                        type="button"
                        data-testid={`scheduling-day-appt-open-${appt.id}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenAppointment?.(appt);
                        }}
                        title="Open cancelled appointment (exception-launch access)"
                        className="pointer-events-auto shrink-0 rounded-sm bg-destructive/15 px-1.5 py-[1px] text-[9px] font-semibold uppercase tracking-wider text-destructive transition-colors hover:bg-destructive hover:text-destructive-foreground"
                      >
                        Canceled · Open
                      </button>
                    </div>
                    <div className="truncate text-[11px] text-destructive/80">
                      {formatHHMM(new Date(appt.start_time))}
                      {appt.reason ? ` · ${appt.reason}` : ""}
                    </div>
                  </div>
                );
              }
              return (
                <button
                  key={appt.id}
                  type="button"
                  data-testid={`scheduling-day-appt-${appt.id}`}
                  onClick={() => onOpenAppointment?.(appt)}
                  className="pointer-events-auto absolute overflow-hidden rounded-sm border-l-2 border-primary bg-primary/15 p-2 text-left text-xs text-foreground shadow-sm transition-colors hover:bg-primary/25"
                  style={commonStyle}
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
