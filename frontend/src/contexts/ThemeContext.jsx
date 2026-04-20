import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";

/**
 * ThemeContext — centralised theme state (light / dark / system).
 *
 * Source of truth is the backend (`users.theme`), but we keep an
 * optimistic local copy in `localStorage` so the very first paint after a
 * cold reload renders in the correct theme (no flash of wrong theme).
 *
 * Order of resolution at boot:
 *   1. localStorage["ccms.theme"] (if set) — ensures zero-flash paint.
 *   2. When /auth/me returns, sync to that — backend wins on conflict.
 *
 * When the user toggles:
 *   - Apply locally + persist to localStorage immediately.
 *   - POST /auth/me/preferences if signed-in; silently fall back to
 *     local-only if not (e.g. during the pre-auth login screen).
 */

const ThemeContext = createContext(null);
const THEMES = ["light", "dark", "system"];
const LS_KEY = "ccms.theme";

function prefersDark() {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function resolveEffective(mode) {
  if (mode === "light" || mode === "dark") return mode;
  return prefersDark() ? "dark" : "light";
}

function applyToDom(effective) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.classList.toggle("dark", effective === "dark");
  root.style.colorScheme = effective;
  root.setAttribute("data-theme", effective);
}

function readStored() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return THEMES.includes(raw) ? raw : "system";
  } catch {
    return "system";
  }
}

export function ThemeProvider({ children }) {
  const [mode, setMode] = useState(readStored);
  const [effective, setEffective] = useState(() => resolveEffective(readStored()));
  const lastPersistedRef = useRef(null);

  // Apply to DOM whenever `mode` changes; subscribe to OS theme changes
  // only while `mode === "system"`.
  useEffect(() => {
    const eff = resolveEffective(mode);
    setEffective(eff);
    applyToDom(eff);
    try { localStorage.setItem(LS_KEY, mode); } catch { /* ignore */ }

    if (mode !== "system" || typeof window === "undefined") return undefined;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      const next = resolveEffective("system");
      setEffective(next);
      applyToDom(next);
    };
    if (mq.addEventListener) mq.addEventListener("change", handler);
    else if (mq.addListener) mq.addListener(handler);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener("change", handler);
      else if (mq.removeListener) mq.removeListener(handler);
    };
  }, [mode]);

  /** Sync from the currently-authenticated user. Called by AuthContext
      after a successful /auth/me, /auth/login, or /auth/mfa/challenge. */
  const syncFromUser = useCallback((user) => {
    if (!user) return;
    const incoming = THEMES.includes(user.theme) ? user.theme : "system";
    lastPersistedRef.current = incoming;
    if (incoming !== mode) setMode(incoming);
  }, [mode]);

  /** User-initiated change — update DOM immediately, try to persist.
      Returns the persisted mode (or the local one on failure). */
  const setTheme = useCallback(async (next) => {
    if (!THEMES.includes(next)) return mode;
    setMode(next);
    // Skip network call if this is just echoing what the backend already has.
    if (next === lastPersistedRef.current) return next;
    try {
      const { data } = await api.patch("/auth/me/preferences", { theme: next });
      lastPersistedRef.current = data?.theme || next;
    } catch {
      /* Unauthenticated or offline — localStorage alone is fine. */
    }
    return next;
  }, [mode]);

  const value = useMemo(() => ({
    mode,
    effective,
    isDark: effective === "dark",
    themes: THEMES,
    setTheme,
    syncFromUser,
  }), [mode, effective, setTheme, syncFromUser]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used inside a <ThemeProvider>");
  }
  return ctx;
}
