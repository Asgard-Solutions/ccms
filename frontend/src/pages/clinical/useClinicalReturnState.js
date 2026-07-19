/**
 * useClinicalReturnState — Phase 3 Slice 1 primitive.
 *
 * Contract (see docs/PHASE3_RETURN_STATE.md and the user's Phase 3 brief):
 *
 *   1. Patient-specific state is scoped to a route *instance* and lives
 *      strictly in-memory + sessionStorage.
 *   2. The route-instance key is an **opaque** token generated on chart
 *      mount and mirrored into `history.state.ccms_route_token`. This
 *      cleanly disentangles state from patient/record IDs so we cannot
 *      leak PHI or apply chart A's state onto chart B.
 *   3. Nothing here writes to `localStorage`. All entries carry a TTL
 *      and are erased on:
 *        - `ccms-session-reset` events (logout, tenant switch,
 *          permission-set refresh),
 *        - TTL expiry,
 *        - Explicit `.clear()` calls.
 *   4. Deep-linked entry (no prior token in `history.state`) yields a
 *      fresh token and empty state — never resurrects stale state.
 *   5. Persistence to `/me/preferences` is *not* handled here. That is
 *      the durable-scope surface reserved for global, non-patient
 *      preferences added in later slices.
 *
 * The transient store lives in a module-scoped Map plus an optional
 * `sessionStorage` mirror. Session storage guarantees state survives a
 * cross-page hop-and-back (chart → scheduling → chart) inside the same
 * tab, but is scrubbed when the tab closes.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

const STORE_KEY = "ccms.clinical.returnState.v1";
const TOKEN_KEY = "ccms_route_token";
const TTL_MS = 30 * 60 * 1000; // 30 min
const SESSION_RESET_EVENT = "ccms-session-reset";

// ------------------------------------------------------------------
// Section slug allow-list. Mirrors the backend telemetry enum. Feeding
// anything else raises in dev so we never invent free-form keys.
// ------------------------------------------------------------------
const KNOWN_SECTIONS = new Set([
  "summary",
  "history",
  "diagnoses",
  "encounters",
  "care-plan",
  "timeline",
  "imaging",
  "outcomes",
  "next-actions",
]);

function assertKnownSection(section) {
  if (!KNOWN_SECTIONS.has(section)) {
    console.warn(`[useClinicalReturnState] unknown section "${section}"`);
  }
}

// ------------------------------------------------------------------
// In-memory store — the primary source of truth. Session storage acts
// as a same-tab mirror so we survive page-level navigation.
// ------------------------------------------------------------------
const memory = new Map(); // fullKey -> { data, exp }

function safeRead() {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.sessionStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function safeWrite(obj) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(STORE_KEY, JSON.stringify(obj));
  } catch {
    /* quota / disabled — memory copy is still authoritative */
  }
}

function hydrateFromSession() {
  const disk = safeRead();
  const now = Date.now();
  for (const [k, entry] of Object.entries(disk)) {
    if (entry && typeof entry.exp === "number" && entry.exp > now) {
      memory.set(k, entry);
    }
  }
}
hydrateFromSession();

function persist() {
  const out = {};
  for (const [k, entry] of memory.entries()) out[k] = entry;
  safeWrite(out);
}

function fullKey(token, section) {
  return `${token}::${section}`;
}

// ------------------------------------------------------------------
// Route-instance token utilities.
// ------------------------------------------------------------------
export function getOrCreateRouteInstanceToken() {
  if (typeof window === "undefined") return null;
  const hs = window.history.state;
  if (hs && typeof hs === "object" && typeof hs[TOKEN_KEY] === "string") {
    return hs[TOKEN_KEY];
  }
  // Fresh token — direct entry, refresh with a lost state, etc.
  const token =
    "r_" +
    Math.random().toString(36).slice(2, 10) +
    Date.now().toString(36);
  const nextState = { ...(hs || {}), [TOKEN_KEY]: token };
  try {
    window.history.replaceState(nextState, "");
  } catch {
    /* very old browsers — token still returned, just non-durable */
  }
  return token;
}

// ------------------------------------------------------------------
// Public clear helpers. Called from AuthContext on logout, permission
// refresh, and tenant switch, and exposed to tests.
// ------------------------------------------------------------------
export function clearAllClinicalReturnState() {
  memory.clear();
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(STORE_KEY);
  } catch {
    /* ignore */
  }
}

export function emitSessionReset() {
  if (typeof window === "undefined") return;
  clearAllClinicalReturnState();
  window.dispatchEvent(new CustomEvent(SESSION_RESET_EVENT));
}

// ------------------------------------------------------------------
// The hook.
// ------------------------------------------------------------------
export function useClinicalReturnState({ section, routeInstanceToken } = {}) {
  if (section) assertKnownSection(section);

  const key = useMemo(
    () => (section && routeInstanceToken ? fullKey(routeInstanceToken, section) : null),
    [section, routeInstanceToken],
  );

  const readInitial = useCallback(() => {
    if (!key) return {};
    const entry = memory.get(key);
    if (!entry) return {};
    if (entry.exp < Date.now()) {
      memory.delete(key);
      persist();
      return {};
    }
    return entry.data || {};
  }, [key]);

  const [state, setState] = useState(readInitial);

  // Re-hydrate when the section/token pair changes (e.g., after a nav).
  useEffect(() => {
    setState(readInitial());
  }, [readInitial]);

  const saveState = useCallback(
    (patch) => {
      if (!key || !patch || typeof patch !== "object") return;
      setState((prev) => {
        const next = { ...prev, ...patch };
        memory.set(key, { data: next, exp: Date.now() + TTL_MS });
        persist();
        return next;
      });
    },
    [key],
  );

  const clear = useCallback(() => {
    if (!key) return;
    memory.delete(key);
    persist();
    setState({});
  }, [key]);

  // Listen for session-wide resets (logout, tenant switch, permission
  // change) and wipe both the store and the current section's copy.
  useEffect(() => {
    const onReset = () => {
      memory.clear();
      if (typeof window !== "undefined") {
        try {
          window.sessionStorage.removeItem(STORE_KEY);
        } catch {
          /* ignore */
        }
      }
      setState({});
    };
    if (typeof window === "undefined") return undefined;
    window.addEventListener(SESSION_RESET_EVENT, onReset);
    return () => window.removeEventListener(SESSION_RESET_EVENT, onReset);
  }, []);

  return { state, saveState, clear };
}

// ------------------------------------------------------------------
// Test-only surface. Not exported from any barrel.
// ------------------------------------------------------------------
export const __internals = {
  memory,
  STORE_KEY,
  TTL_MS,
  SESSION_RESET_EVENT,
  TOKEN_KEY,
};
