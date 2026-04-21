import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { VIEWS, stepDate, visibleRange } from "./dateHelpers";

/**
 * Centralised scheduling state:
 *  - active view (day|week|month|year)
 *  - selected date
 *  - visible range (derived)
 *  - provider filter (placeholder; not yet exposed in UI)
 *  - range-based appointment fetcher with lightweight cache keyed by
 *    `${view}-${rangeStart}-${rangeEnd}-${providerId ?? 'all'}`
 */
export function useScheduling(initial = {}) {
  const [view, setView] = useState(() => {
    const v = initial.view;
    return VIEWS.includes(v) ? v : "week";
  });
  const [date, setDate] = useState(() => initial.date || new Date());
  const [providerId, setProviderId] = useState(initial.providerId || null);

  const [appointments, setAppointments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const range = useMemo(() => visibleRange(view, date), [view, date]);

  // lightweight in-memory cache so quick view toggles don't refetch.
  const cacheRef = useRef(new Map());
  const reqIdRef = useRef(0);

  // Only the Day view consumes full appointment detail; summary views use
  // the counts aggregation endpoint (see `useAppointmentCounts`). Skipping
  // the detail fetch for non-Day views is a real perf win for year-range
  // loads on busy clinics.
  const [includeCancelled, setIncludeCancelled] = useState(!!initial.includeCancelled);
  const detailEnabled = view === "day";

  const fetchRange = useCallback(async () => {
    if (!detailEnabled) {
      setAppointments([]);
      setLoading(false);
      return;
    }
    const key = `${view}|${range.start.toISOString()}|${range.end.toISOString()}|${providerId || "all"}|${includeCancelled}`;
    if (cacheRef.current.has(key)) {
      setAppointments(cacheRef.current.get(key));
      return;
    }
    const myReq = ++reqIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const params = { from: range.start.toISOString(), to: range.end.toISOString() };
      if (providerId) params.provider_id = providerId;
      params.include_cancelled = includeCancelled;
      const { data } = await api.get("/appointments", { params });
      if (myReq !== reqIdRef.current) return; // stale
      cacheRef.current.set(key, data);
      setAppointments(data);
    } catch (e) {
      if (myReq === reqIdRef.current) {
        setError(e);
        setAppointments([]);
      }
    } finally {
      if (myReq === reqIdRef.current) setLoading(false);
    }
  }, [view, range.start, range.end, providerId, detailEnabled, includeCancelled]);

  useEffect(() => {
    fetchRange();
  }, [fetchRange]);

  const prev = useCallback(() => setDate((d) => stepDate(view, d, -1)), [view]);
  const next = useCallback(() => setDate((d) => stepDate(view, d, +1)), [view]);
  const today = useCallback(() => setDate(new Date()), []);

  const invalidate = useCallback(() => {
    cacheRef.current.clear();
    fetchRange();
  }, [fetchRange]);

  const goToDay = useCallback((d) => {
    setView("day");
    setDate(new Date(d));
  }, []);

  const goToMonth = useCallback((d) => {
    setView("month");
    setDate(new Date(d));
  }, []);

  return {
    view,
    setView,
    date,
    setDate,
    range,
    appointments,
    loading,
    error,
    providerId,
    setProviderId,
    includeCancelled,
    setIncludeCancelled,
    prev,
    next,
    today,
    invalidate,
    goToDay,
    goToMonth,
  };
}
