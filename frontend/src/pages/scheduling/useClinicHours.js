import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";

/**
 * Fetch the clinic hours for the caller's active location.
 *
 * Resolution order:
 *   1. GET /tenancy/me/context → first visible location
 *   2. GET /clinic-profiles/{location_id} → hours[] (7 DayHours entries)
 *
 * Returns:
 *   - `hours`: array of 7 DayHours objects, or null if no profile configured
 *   - `locationId`: the resolved location id (may be null for platform admin)
 *   - `loading`: boolean
 *   - `error`: axios error if the profile call failed with a non-404 status
 *
 * A 404 on the profile is treated as "not configured yet" (null hours, no
 * error) — the Day view then falls back to its own default window.
 */
export function useClinicHours() {
  const [hours, setHours] = useState(null);
  const [locationId, setLocationId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const loaded = useRef(false);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    (async () => {
      try {
        const ctx = await api.get("/tenancy/me/context");
        const locs = ctx.data?.locations || [];
        const firstLoc = locs[0];
        if (!firstLoc) {
          setLoading(false);
          return;
        }
        setLocationId(firstLoc.id);
        try {
          const profile = await api.get(`/clinic-profiles/${firstLoc.id}`);
          setHours(profile.data?.hours || null);
        } catch (err) {
          if (err.response?.status !== 404) setError(err);
          // 404: profile not configured — leave hours null.
        }
      } catch (err) {
        setError(err);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return { hours, locationId, loading, error };
}

/**
 * Given a DayHours list (or null) and a target Date, return:
 *   { isClosed, openMinutes, closeMinutes }
 * where openMinutes/closeMinutes span the earliest open through the latest
 * close across all intervals for that weekday. When hours are missing or
 * the day is closed, returns sensible nulls.
 *
 * Day-of-week mapping: 0 = Monday ... 6 = Sunday (matches backend model).
 */
export function extractDaySpan(hours, date) {
  if (!hours) return { isClosed: false, openMinutes: null, closeMinutes: null };
  const dow = (date.getDay() + 6) % 7;
  const entry = hours.find((h) => h.day_of_week === dow);
  if (!entry) return { isClosed: false, openMinutes: null, closeMinutes: null };
  if (entry.is_closed || !entry.intervals || entry.intervals.length === 0) {
    return { isClosed: true, openMinutes: null, closeMinutes: null };
  }
  const toMin = (hhmm) => {
    const [h, m] = hhmm.split(":").map(Number);
    return h * 60 + m;
  };
  const openMinutes = Math.min(...entry.intervals.map((i) => toMin(i.open_time)));
  const closeMinutes = Math.max(...entry.intervals.map((i) => toMin(i.close_time)));
  return { isClosed: false, openMinutes, closeMinutes };
}
