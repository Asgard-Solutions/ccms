/**
 * ProvidersContext — Lightweight cache for `/auth/providers`.
 *
 * Several UI surfaces (PatientDetail header, PatientWizard, BookDialog,
 * ProviderFilter, clinical editors) each independently refetched the
 * provider roster on mount. This context hoists the fetch to a single
 * place, exposes `useProviders()`, and lets consumers force a refresh
 * after changes (e.g. adding a new provider user).
 *
 * Frontend-only optimisation; backend remains authoritative.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api } from "../api/client";
import { useAuth } from "./AuthContext";

const ProvidersContext = createContext(null);

export function ProvidersProvider({ children }) {
  const { user } = useAuth();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const inflight = useRef(null);

  const refresh = useCallback(async () => {
    if (!user) {
      setRows([]);
      return [];
    }
    // Dedupe concurrent callers.
    if (inflight.current) return inflight.current;
    setLoading(true);
    const promise = (async () => {
      try {
        const { data } = await api.get("/auth/providers");
        setRows(data || []);
        setError(null);
        return data || [];
      } catch (e) {
        setRows([]);
        setError(e);
        return [];
      } finally {
        setLoading(false);
        inflight.current = null;
      }
    })();
    inflight.current = promise;
    return promise;
  }, [user]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const byId = useMemo(() => {
    const m = new Map();
    rows.forEach((r) => m.set(r.id, r));
    return m;
  }, [rows]);

  const value = {
    providers: rows,
    loading,
    error,
    refresh,
    getProvider: (id) => byId.get(id) || null,
  };

  return (
    <ProvidersContext.Provider value={value}>
      {children}
    </ProvidersContext.Provider>
  );
}

export function useProviders() {
  const ctx = useContext(ProvidersContext);
  if (!ctx)
    throw new Error("useProviders must be used within ProvidersProvider");
  return ctx;
}
