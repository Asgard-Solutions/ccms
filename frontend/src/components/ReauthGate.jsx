import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { api } from "../api/client";
import ReauthDialog from "./ReauthDialog";

/**
 * ReauthGate — global reauth orchestration.
 *
 * Problem: our policy engine requires an MFA "reauth token" for
 * medium/high-sensitivity mutations (post payment, adjust invoice,
 * void invoice, refund, write medical record, delete patient, etc).
 * Users with `step_up_required=true` also get the gate on every
 * permission call. Sprinkling per-feature ReauthDialogs through the
 * app is bug-prone and misses the cases where the 401 slips through.
 *
 * Solution: one axios response interceptor catches 401s that carry
 * `detail == "Re-authentication required for this action."` (or the
 * companion response header), pauses the offending request, opens a
 * singleton dialog, waits for the user to confirm, then replays the
 * original request with the new reauth cookie already on the jar.
 *
 * This file also re-exposes a `useReauth()` hook for components that
 * want to *preemptively* ask for reauth (e.g. to disable a button
 * until reauth is fresh).
 */

const REAUTH_DETAIL = "Re-authentication required for this action.";

const ReauthContext = createContext(null);

export function ReauthProvider({ children }) {
  const [open, setOpen] = useState(false);
  // Each pending promise waits on the same resolver; we resolve `true`
  // when the dialog confirms and `false` if the user cancels.
  const waitersRef = useRef([]);

  const requestReauth = useCallback(() => {
    return new Promise((resolve) => {
      waitersRef.current.push(resolve);
      setOpen(true);
    });
  }, []);

  const drain = useCallback((ok) => {
    const waiters = waitersRef.current;
    waitersRef.current = [];
    waiters.forEach((r) => r(ok));
  }, []);

  useEffect(() => {
    // Response interceptor — when a 401 signals "reauth required" we
    // pop the dialog and retry the request once the user confirms.
    const id = api.interceptors.response.use(
      (r) => r,
      async (error) => {
        const response = error?.response;
        const config = error?.config;
        const detail = response?.data?.detail;
        const isReauthError =
          response?.status === 401 &&
          (detail === REAUTH_DETAIL ||
            response.headers?.["x-reauth-required"] === "1");
        if (!isReauthError || !config || config.__reauthRetried) {
          return Promise.reject(error);
        }
        config.__reauthRetried = true;
        const ok = await requestReauth();
        if (!ok) return Promise.reject(error);
        return api(config);
      },
    );
    return () => api.interceptors.response.eject(id);
  }, [requestReauth]);

  return (
    <ReauthContext.Provider value={{ requestReauth }}>
      {children}
      <ReauthDialog
        open={open}
        title="Confirm it's you"
        description="This action is logged to the audit trail. Please re-enter your password to continue."
        onConfirmed={() => {
          setOpen(false);
          drain(true);
        }}
        onClose={() => {
          setOpen(false);
          drain(false);
        }}
      />
    </ReauthContext.Provider>
  );
}

export function useReauth() {
  const ctx = useContext(ReauthContext);
  if (!ctx) throw new Error("useReauth must be used within ReauthProvider");
  return ctx;
}
