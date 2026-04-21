import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

/**
 * Fetches appointment types for the current tenant.
 *
 * Options:
 *   - activeOnly (default true) — used by the booking modal so inactive
 *     types never appear in the dropdown.
 *   - enabled (default true) — skips the fetch entirely when false.
 */
export function useAppointmentTypes({ activeOnly = true, enabled = true } = {}) {
  const [types, setTypes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    setError(null);
    try {
      const params = {};
      if (activeOnly) params.active_only = true;
      const { data } = await api.get("/appointment-types", { params });
      setTypes(data || []);
    } catch (e) {
      setError(e);
      setTypes([]);
    } finally {
      setLoading(false);
    }
  }, [activeOnly, enabled]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { types, loading, error, refresh };
}
