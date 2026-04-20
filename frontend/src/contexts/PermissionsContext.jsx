import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "./AuthContext";

const PermissionsContext = createContext(null);

/**
 * Fetches the authenticated user's effective permissions from the backend
 * and exposes `can()` + `scope()` helpers. Frontend authz is *always*
 * advisory — the backend is the source of truth. Hiding UI is a UX win,
 * not a security boundary.
 */
export function PermissionsProvider({ children }) {
  const { user } = useAuth();
  const [effective, setEffective] = useState(null); // {permissions, role_keys, location_ids}
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!user) {
      setEffective(null);
      return null;
    }
    setLoading(true);
    try {
      const { data } = await api.get("/authz/me/permissions");
      setEffective(data);
      return data;
    } catch {
      setEffective(null);
      return null;
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const byKey = useMemo(() => {
    const m = new Map();
    (effective?.permissions || []).forEach((p) => m.set(p.key, p));
    return m;
  }, [effective]);

  const can = useCallback(
    (resource, action) => byKey.has(`${resource}.${action}`),
    [byKey]
  );

  const scope = useCallback(
    (resource, action) => byKey.get(`${resource}.${action}`)?.scope || null,
    [byKey]
  );

  const grant = useCallback(
    (resource, action) => byKey.get(`${resource}.${action}`) || null,
    [byKey]
  );

  const hasRole = useCallback(
    (roleKey) => (effective?.role_keys || []).includes(roleKey),
    [effective]
  );

  const value = {
    loading,
    effective,
    can,
    scope,
    grant,
    hasRole,
    refresh,
    roleKeys: effective?.role_keys || [],
    permissions: effective?.permissions || [],
  };

  return <PermissionsContext.Provider value={value}>{children}</PermissionsContext.Provider>;
}

export function usePermissions() {
  const ctx = useContext(PermissionsContext);
  if (!ctx) throw new Error("usePermissions must be used within PermissionsProvider");
  return ctx;
}

/**
 * `<Can resource="audit_log" action="read">children</Can>`
 * Hides children when the caller lacks the permission. A fallback prop
 * renders when denied.
 */
export function Can({ resource, action, fallback = null, children }) {
  const { can } = usePermissions();
  return can(resource, action) ? children : fallback;
}
