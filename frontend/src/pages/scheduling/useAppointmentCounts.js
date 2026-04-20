import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { visibleRange } from "./dateHelpers";

/**
 * Lightweight appointment-count aggregation for Week/Month/Year views.
 *
 * Calls `GET /appointments/counts?from=&to=&tz=&include_samples=&...` which
 * returns one row per local-date bucket with a count and up to N sample
 * appointments (hydrated with patient/provider names). The full detail list
 * is never loaded for these views — this is the big perf win over the old
 * "fetch every appointment detail" approach.
 *
 * Caching: 30s TTL on the server; we keep a small in-memory LRU-ish Map
 * on the client keyed by `${view}|${start}|${end}|${tz}|${samples}|${providerId}`.
 *
 * Returns a plain object { [YYYY-MM-DD]: { count, samples[] } } plus
 * loading/error state.
 */
export function useAppointmentCounts({
  view,
  date,
  providerId = null,
  tz = null,
  enabled = true,
} = {}) {
  const samples = view === "week" ? 3 : view === "month" ? 2 : 0;
  const effectiveTz = tz || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

  const [map, setMap] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const range = useMemo(() => visibleRange(view, date), [view, date]);
  const cacheRef = useRef(new Map());
  const reqIdRef = useRef(0);

  const fetchCounts = useCallback(async () => {
    if (!enabled) {
      setMap({});
      return;
    }
    const key = `${view}|${range.start.toISOString()}|${range.end.toISOString()}|${effectiveTz}|${samples}|${providerId || "all"}`;
    if (cacheRef.current.has(key)) {
      setMap(cacheRef.current.get(key));
      return;
    }
    const myReq = ++reqIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const params = {
        from: range.start.toISOString(),
        to: range.end.toISOString(),
        tz: effectiveTz,
        include_samples: samples,
      };
      if (providerId) params.provider_id = providerId;
      const { data } = await api.get("/appointments/counts", { params });
      if (myReq !== reqIdRef.current) return;
      const next = {};
      for (const row of data || []) {
        next[row.date] = { count: row.count, samples: row.samples || [] };
      }
      cacheRef.current.set(key, next);
      setMap(next);
    } catch (e) {
      if (myReq === reqIdRef.current) {
        setError(e);
        setMap({});
      }
    } finally {
      if (myReq === reqIdRef.current) setLoading(false);
    }
  }, [enabled, view, range.start, range.end, effectiveTz, samples, providerId]);

  useEffect(() => {
    fetchCounts();
  }, [fetchCounts]);

  const invalidate = useCallback(() => {
    cacheRef.current.clear();
    fetchCounts();
  }, [fetchCounts]);

  return { countsByDate: map, loading, error, invalidate };
}
