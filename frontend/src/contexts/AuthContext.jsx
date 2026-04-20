import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, formatApiError } from "../api/client";

const AuthContext = createContext(null);

const IDLE_TIMEOUT_MS = 15 * 60 * 1000; // 15 minute auto-logoff
const IDLE_WARN_MS = IDLE_TIMEOUT_MS - 60_000;

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined);
  const [mfaContext, setMfaContext] = useState(null); // {mfa_ticket, password_rotation_due}
  const [idleWarning, setIdleWarning] = useState(false);
  const idleTimer = useRef(null);
  const warnTimer = useRef(null);

  const fetchMe = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me");
      setUser(data);
      return data;
    } catch {
      setUser(null);
      return null;
    }
  }, []);

  useEffect(() => {
    fetchMe();
  }, [fetchMe]);

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } catch {
      /* ignore */
    }
    setUser(null);
    setMfaContext(null);
  }, []);

  /** Idle timeout — only active while authenticated. */
  useEffect(() => {
    if (!user) return undefined;

    const reset = () => {
      setIdleWarning(false);
      if (warnTimer.current) clearTimeout(warnTimer.current);
      if (idleTimer.current) clearTimeout(idleTimer.current);
      warnTimer.current = setTimeout(() => setIdleWarning(true), IDLE_WARN_MS);
      idleTimer.current = setTimeout(() => {
        setIdleWarning(false);
        logout();
      }, IDLE_TIMEOUT_MS);
    };
    const events = ["mousedown", "keydown", "touchstart", "scroll"];
    events.forEach((e) => window.addEventListener(e, reset, { passive: true }));
    reset();
    return () => {
      events.forEach((e) => window.removeEventListener(e, reset));
      if (warnTimer.current) clearTimeout(warnTimer.current);
      if (idleTimer.current) clearTimeout(idleTimer.current);
    };
  }, [user, logout]);

  const login = useCallback(async (email, password) => {
    const { data } = await api.post("/auth/login", { email, password });
    if (data?.mfa_required) {
      setMfaContext({
        mfa_ticket: data.mfa_ticket,
        password_rotation_due: data.password_rotation_due,
      });
      return { mfa_required: true };
    }
    setUser(data.user);
    setMfaContext(
      data.password_rotation_due ? { password_rotation_due: true } : null
    );
    return { user: data.user };
  }, []);

  const verifyMfa = useCallback(async (code) => {
    if (!mfaContext?.mfa_ticket) throw new Error("No pending MFA challenge");
    const { data } = await api.post("/auth/mfa/challenge", {
      mfa_ticket: mfaContext.mfa_ticket,
      code,
    });
    setUser(data);
    setMfaContext(null);
    return data;
  }, [mfaContext]);

  const register = useCallback(async (payload) => {
    const { data } = await api.post("/auth/register", payload);
    setUser(data);
    return data;
  }, []);

  const reauth = useCallback(async (password) => {
    const { data } = await api.post("/auth/reauth", { password });
    return data;
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        mfaContext,
        idleWarning,
        login,
        verifyMfa,
        register,
        logout,
        reauth,
        refresh: fetchMe,
        formatApiError,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
