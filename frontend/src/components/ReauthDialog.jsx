import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Hash, Lock } from "lucide-react";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Textarea } from "./ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { api, formatApiError } from "../api/client";
import { useAuth } from "../contexts/AuthContext";

/**
 * Reusable step-up verification dialog.
 *
 * Consolidates the password + (optional) reason prompt that previously
 * only knew about password and now ALSO supports a 6-digit Security
 * PIN for users who've configured one (see PinCard). On success, the
 * server sets a 5-minute `reauth_token` cookie — the existing
 * `require_reauth()` middleware + global axios interceptor keep
 * working with zero changes.
 *
 * Behaviour:
 *   - Users with `pin_configured=true` default to the PIN tab with a
 *     "Use password instead" toggle as a migration fallback.
 *   - Users without a PIN see the familiar password form (no toggle).
 *   - `reason` is optional and surfaces only when
 *     `requireReason=true` or the caller passes `defaultReason=...`.
 *
 * This keeps a SINGLE step-up flow across the app. No feature should
 * pop a bespoke password modal for sensitive actions.
 */
export default function ReauthDialog({
  open,
  title,
  description,
  requireReason = false,
  defaultReason = "",
  onConfirmed,
  onClose,
}) {
  const { user, refresh } = useAuth();
  const pinConfigured = !!user?.pin_configured;

  // "pin" or "password"; derived from user state but user can toggle.
  const [mode, setMode] = useState(pinConfigured ? "pin" : "password");
  const [password, setPassword] = useState("");
  const [pin, setPin] = useState("");
  const [reason, setReason] = useState(defaultReason);
  const [submitting, setSubmitting] = useState(false);

  const reasonOk = !requireReason || reason.trim().length >= 8;

  // Reset each time the dialog opens; keep the mode aligned with the
  // freshest user state (important after setting a PIN the first time).
  useEffect(() => {
    if (!open) return;
    setMode(pinConfigured ? "pin" : "password");
    setPassword("");
    setPin("");
    setReason(defaultReason);
  }, [open, pinConfigured, defaultReason]);

  const canSubmit = useMemo(() => {
    if (submitting || !reasonOk) return false;
    if (mode === "pin") return /^\d{6}$/.test(pin);
    return password.length > 0;
  }, [mode, pin, password, submitting, reasonOk]);

  const submit = useCallback(
    async (e) => {
      e?.preventDefault?.();
      if (!canSubmit) return;
      setSubmitting(true);
      try {
        const body = mode === "pin" ? { pin } : { password };
        if (reason.trim()) body.reason = reason.trim();
        const { data } = await api.post("/auth/reauth", body);
        toast.success(
          data?.factor === "pin"
            ? "PIN confirmed — you have 5 minutes."
            : "Identity confirmed — you have 5 minutes.",
        );
        setPassword("");
        setPin("");
        onConfirmed?.();
      } catch (err) {
        toast.error(formatApiError(err));
        // If the PIN got locked, fall back to password automatically.
        if (err?.response?.status === 423) {
          setMode("password");
          await refresh?.();
        }
      } finally {
        setSubmitting(false);
      }
    },
    [mode, pin, password, reason, canSubmit, onConfirmed, refresh],
  );

  const canSwitchToPassword = pinConfigured && mode === "pin";
  const canSwitchToPin = pinConfigured && mode === "password";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !submitting && onClose?.()}>
      <DialogContent
        data-testid="reauth-dialog"
        data-reauth-mode={mode}
        className="max-w-md rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            {title || "Confirm it's you"}
          </DialogTitle>
          <DialogDescription>
            {description ||
              (mode === "pin"
                ? "Enter your 6-digit Security PIN to continue. This action is logged."
                : "This action is logged to the audit trail. Please re-enter your password to continue.")}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-4">
          {mode === "pin" ? (
            <div className="space-y-1">
              <Label htmlFor="reauth-pin" className="flex items-center gap-1">
                <Hash className="h-3.5 w-3.5" />
                Security PIN
              </Label>
              <Input
                id="reauth-pin"
                data-testid="reauth-pin-input"
                type="password"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={pin}
                onChange={(e) => {
                  const digits = e.target.value.replace(/\D/g, "").slice(0, 6);
                  setPin(digits);
                }}
                maxLength={6}
                placeholder="● ● ● ● ● ●"
                autoFocus
                required
                className="rounded-sm text-center tracking-[0.5em] font-mono"
              />
            </div>
          ) : (
            <div className="space-y-1">
              <Label htmlFor="reauth-pw" className="flex items-center gap-1">
                <Lock className="h-3.5 w-3.5" />
                Password
              </Label>
              <Input
                id="reauth-pw"
                data-testid="reauth-password-input"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoFocus
                required
                className="rounded-sm"
              />
            </div>
          )}

          {(requireReason || reason) && (
            <div className="space-y-1">
              <Label htmlFor="reauth-reason">
                Reason{requireReason ? "" : " (optional)"}
              </Label>
              <Textarea
                id="reauth-reason"
                data-testid="reauth-reason-input"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                placeholder={
                  requireReason
                    ? "8+ characters. Recorded to the audit log."
                    : "Optional note for the audit log."
                }
                className="rounded-sm"
              />
              {requireReason && reason.trim().length > 0 && !reasonOk && (
                <p className="text-[11px] text-destructive">
                  Reason must be at least 8 characters.
                </p>
              )}
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
            <div className="text-[11px] text-muted-foreground">
              {canSwitchToPassword && (
                <button
                  type="button"
                  data-testid="reauth-use-password-btn"
                  onClick={() => setMode("password")}
                  className="underline underline-offset-2 hover:text-foreground"
                >
                  Use password instead
                </button>
              )}
              {canSwitchToPin && (
                <button
                  type="button"
                  data-testid="reauth-use-pin-btn"
                  onClick={() => setMode("pin")}
                  className="underline underline-offset-2 hover:text-foreground"
                >
                  Use Security PIN instead
                </button>
              )}
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={onClose}
                disabled={submitting}
                className="rounded-sm"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={!canSubmit}
                data-testid="reauth-confirm-btn"
                className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
              >
                {submitting ? "Confirming…" : "Confirm"}
              </Button>
            </div>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
