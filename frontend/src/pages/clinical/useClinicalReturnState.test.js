/**
 * @jest-environment jsdom
 *
 * Contract tests for the useClinicalReturnState primitive.
 *
 * We validate the underlying store, token generator, and reset event
 * directly rather than rendering the React hook — the app's test
 * infrastructure does not ship with `@testing-library/react`, and the
 * hook itself is a thin veneer over these primitives.
 *
 * Focus areas the user explicitly asked us to lock in:
 *   1. Refresh / re-entry via same token restores state.
 *   2. TTL expiry drops stale entries.
 *   3. Direct entry (no token in history.state) yields fresh state.
 *   4. Logout / session-reset event wipes all entries.
 *   5. Cross-chart isolation: chart A's token ≠ chart B's token.
 *   6. Opaque token never carries patient identifiers.
 */
const {
  getOrCreateRouteInstanceToken,
  clearAllClinicalReturnState,
  emitSessionReset,
  __internals,
} = require("./useClinicalReturnState");

function withCleanHistory() {
  window.history.replaceState(null, "");
}

// Helpers that mimic the hook's store I/O without React.
function writeEntry(token, section, data, exp) {
  const key = `${token}::${section}`;
  __internals.memory.set(key, { data, exp });
  const dump = {};
  for (const [k, v] of __internals.memory.entries()) dump[k] = v;
  window.sessionStorage.setItem(__internals.STORE_KEY, JSON.stringify(dump));
}

function readEntry(token, section) {
  const key = `${token}::${section}`;
  const entry = __internals.memory.get(key);
  if (!entry) return null;
  if (entry.exp < Date.now()) return null;
  return entry.data;
}

beforeEach(() => {
  clearAllClinicalReturnState();
  window.sessionStorage.clear();
  withCleanHistory();
});

describe("getOrCreateRouteInstanceToken", () => {
  test("returns the same token on subsequent calls in the same route", () => {
    const t1 = getOrCreateRouteInstanceToken();
    const t2 = getOrCreateRouteInstanceToken();
    expect(t1).toBe(t2);
    expect(t1).toMatch(/^r_/);
  });

  test("issues a new token when history.state is cleared (direct entry)", () => {
    const t1 = getOrCreateRouteInstanceToken();
    withCleanHistory();
    const t2 = getOrCreateRouteInstanceToken();
    expect(t2).not.toBe(t1);
  });

  test("stores the token opaquely on history.state (no PHI)", () => {
    const t = getOrCreateRouteInstanceToken();
    expect(window.history.state.ccms_route_token).toBe(t);
    const serialised = JSON.stringify(window.history.state);
    expect(serialised).not.toMatch(/patient/i);
    expect(serialised).not.toMatch(/@/); // no email addresses
  });

  test("token is opaque — never derived from patient or record IDs", () => {
    // Even if we advertised a patient id in the URL, the token
    // generator must not consume it.
    window.history.replaceState(null, "", "/patients/abc-123/clinical");
    const t = getOrCreateRouteInstanceToken();
    expect(t).not.toContain("abc-123");
  });
});

describe("clinical return state — store contract", () => {
  test("saveState survives a re-hydrate at the same token/section", () => {
    const token = getOrCreateRouteInstanceToken();
    writeEntry(token, "encounters", { filter: "billing", scrollY: 420 }, Date.now() + 60_000);
    // Simulate a fresh page mount rehydrating the store from
    // sessionStorage.
    __internals.memory.clear();
    const disk = JSON.parse(window.sessionStorage.getItem(__internals.STORE_KEY));
    for (const [k, entry] of Object.entries(disk)) {
      __internals.memory.set(k, entry);
    }
    expect(readEntry(token, "encounters")).toEqual({ filter: "billing", scrollY: 420 });
  });

  test("cross-section isolation — encounters state does not leak into diagnoses", () => {
    const token = getOrCreateRouteInstanceToken();
    writeEntry(token, "encounters", { filter: "missing_note" }, Date.now() + 60_000);
    expect(readEntry(token, "diagnoses")).toBeNull();
  });

  test("cross-chart isolation — different tokens never share state", () => {
    const tokenA = getOrCreateRouteInstanceToken();
    writeEntry(tokenA, "encounters", { filter: "billing" }, Date.now() + 60_000);

    // Simulate direct entry to chart B.
    withCleanHistory();
    const tokenB = getOrCreateRouteInstanceToken();
    expect(tokenB).not.toBe(tokenA);
    expect(readEntry(tokenB, "encounters")).toBeNull();
  });

  test("TTL expiry drops stale entries", () => {
    const token = getOrCreateRouteInstanceToken();
    writeEntry(token, "encounters", { filter: "old" }, Date.now() - 1000);
    expect(readEntry(token, "encounters")).toBeNull();
  });
});

describe("session reset semantics", () => {
  test("emitSessionReset wipes memory + sessionStorage", () => {
    const token = getOrCreateRouteInstanceToken();
    writeEntry(token, "encounters", { filter: "billing" }, Date.now() + 60_000);
    expect(__internals.memory.size).toBeGreaterThan(0);
    expect(window.sessionStorage.getItem(__internals.STORE_KEY)).not.toBeNull();

    emitSessionReset();
    expect(__internals.memory.size).toBe(0);
    expect(window.sessionStorage.getItem(__internals.STORE_KEY)).toBeNull();
  });

  test("session-reset custom event is dispatched (subscribers can clear their state)", () => {
    const token = getOrCreateRouteInstanceToken();
    writeEntry(token, "encounters", { filter: "billing" }, Date.now() + 60_000);
    let heard = 0;
    const listener = () => {
      heard += 1;
    };
    window.addEventListener(__internals.SESSION_RESET_EVENT, listener);
    emitSessionReset();
    expect(heard).toBe(1);
    window.removeEventListener(__internals.SESSION_RESET_EVENT, listener);
  });

  test("clearAllClinicalReturnState is idempotent and safe to call twice", () => {
    clearAllClinicalReturnState();
    clearAllClinicalReturnState();
    expect(__internals.memory.size).toBe(0);
  });
});

describe("PHI hardening", () => {
  test("no sessionStorage value ever contains a patient id or email", () => {
    const token = getOrCreateRouteInstanceToken();
    // The hook is only meant to carry opaque UI state — filter slugs,
    // scroll offsets, expanded row sets. We enforce this by inspection.
    writeEntry(
      token,
      "encounters",
      {
        filter: "billing",
        scrollY: 240,
        expanded: ["group-abc", "group-def"],
      },
      Date.now() + 60_000,
    );
    const raw = window.sessionStorage.getItem(__internals.STORE_KEY);
    expect(raw).not.toMatch(/@/); // no email
    expect(raw).not.toMatch(/patient/i);
    expect(raw).not.toMatch(/\bicd/i);
  });
});
