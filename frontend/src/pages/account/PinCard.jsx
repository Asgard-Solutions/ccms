/**
 * PinCard — Security-tab section for managing a 6-digit in-app PIN.
 *
 * Integrates with the existing Security page by reusing the same card
 * chrome, Shadcn Dialog + Input + Button components, and AuthContext
 * for the "configured?" signal (surfaced via `/auth/me.pin_configured`).
 *
 * Endpoints (all self-service, auth required):
 *   GET    /api/auth/me/pin/status
 *   POST   /api/auth/me/pin            { current_password, pin }
 *   PATCH  /api/auth/me/pin            { current_password, current_pin, new_pin }
 *   POST   /api/auth/me/pin/reset      { new_pin }   — requires reauth token
 *   DELETE /api/auth/me/pin            { password }
 *
 * The PIN is never echoed back to the client; this UI only surfaces
 * "configured / not configured" + rotation timestamp.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Hash,
  Pencil,
  RotateCcw,
  Trash2,
  ShieldCheck as ShieldCheckIcon,
  AlertTriangle,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { formatDateTime } from "../../utils/time";

const PIN_RE = /^\d{6}$/;

function PinField({ value, onChange, testId, label, autoFocus }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      <Input
        type="password"
        inputMode="numeric"
        autoComplete="off"
        autoFocus={autoFocus}
        // Only accept digits; strip anything else the user pastes.
        value={value}
        onChange={(e) => {
          const digits = e.target.value.replace(/\D/g, "").slice(0, 6);
          onChange(digits);
        }}
        maxLength={6}
        placeholder="● ● ● ● ● ●"
        data-testid={testId}
        className="rounded-sm text-center tracking-[0.5em] font-mono"
      />
    </div>
  );
}

function PasswordConfirmField({ value, onChange }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs uppercase tracking-wider text-muted-foreground">
        Current password
      </Label>
      <Input
        type="password"
        autoComplete="current-password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid="pin-dialog-password"
        className="rounded-sm"
      />
    </div>
  );
}

function CreatePinDialog({ open, onOpenChange, onDone }) {
  const [password, setPassword] = useState("");
  const [pin, setPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) {
      setPassword("");
      setPin("");
      setConfirm("");
    }
  }, [open]);

  const match = pin && pin === confirm;
  const valid = PIN_RE.test(pin) && match && password;

  const submit = async (e) => {
    e.preventDefault();
    if (!PIN_RE.test(pin)) {
      toast.error("PIN must be exactly 6 digits.");
      return;
    }
    if (!match) {
      toast.error("PINs do not match.");
      return;
    }
    setSubmitting(true);
    try {
      await api.post("/auth/me/pin", { current_password: password, pin });
      toast.success("Security PIN created.");
      onDone();
      onOpenChange(false);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !submitting && onOpenChange(v)}>
      <DialogContent
        data-testid="pin-create-dialog"
        className="max-w-md rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Set a 6-digit PIN</DialogTitle>
          <DialogDescription>
            Use your PIN for fast in-app re-verification after you&rsquo;ve
            already signed in. It&rsquo;s hashed and never shown back to you.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <PasswordConfirmField value={password} onChange={setPassword} />
          <PinField value={pin} onChange={setPin} label="New PIN" testId="pin-create-new" autoFocus={false} />
          <PinField value={confirm} onChange={setConfirm} label="Confirm new PIN" testId="pin-create-confirm" />
          {confirm && !match && (
            <p
              data-testid="pin-create-mismatch"
              className="text-[11px] text-destructive"
            >
              PINs do not match.
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={submitting}
              onClick={() => onOpenChange(false)}
              className="rounded-sm"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!valid || submitting}
              data-testid="pin-create-submit"
              className="rounded-sm"
            >
              {submitting ? "Saving…" : "Set PIN"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ChangePinDialog({ open, onOpenChange, onDone }) {
  const [password, setPassword] = useState("");
  const [currentPin, setCurrentPin] = useState("");
  const [newPin, setNewPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) {
      setPassword("");
      setCurrentPin("");
      setNewPin("");
      setConfirm("");
    }
  }, [open]);

  const match = newPin && newPin === confirm;
  const different = newPin && currentPin && newPin !== currentPin;
  const valid =
    PIN_RE.test(currentPin) && PIN_RE.test(newPin) && match && different && password;

  const submit = async (e) => {
    e.preventDefault();
    if (!different) {
      toast.error("New PIN must differ from the current PIN.");
      return;
    }
    if (!match) {
      toast.error("PINs do not match.");
      return;
    }
    setSubmitting(true);
    try {
      await api.patch("/auth/me/pin", {
        current_password: password,
        current_pin: currentPin,
        new_pin: newPin,
      });
      toast.success("Security PIN updated.");
      onDone();
      onOpenChange(false);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !submitting && onOpenChange(v)}>
      <DialogContent
        data-testid="pin-change-dialog"
        className="max-w-md rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Change PIN</DialogTitle>
          <DialogDescription>
            You&rsquo;ll need your password and current PIN to set a new one.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <PasswordConfirmField value={password} onChange={setPassword} />
          <PinField
            value={currentPin}
            onChange={setCurrentPin}
            label="Current PIN"
            testId="pin-change-current"
          />
          <PinField
            value={newPin}
            onChange={setNewPin}
            label="New PIN"
            testId="pin-change-new"
          />
          <PinField
            value={confirm}
            onChange={setConfirm}
            label="Confirm new PIN"
            testId="pin-change-confirm"
          />
          {confirm && !match && (
            <p className="text-[11px] text-destructive" data-testid="pin-change-mismatch">
              PINs do not match.
            </p>
          )}
          {currentPin && newPin && !different && (
            <p className="text-[11px] text-destructive" data-testid="pin-change-same">
              New PIN must differ from the current one.
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={submitting}
              onClick={() => onOpenChange(false)}
              className="rounded-sm"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!valid || submitting}
              data-testid="pin-change-submit"
              className="rounded-sm"
            >
              {submitting ? "Saving…" : "Change PIN"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ResetPinDialog({ open, onOpenChange, onDone }) {
  const [newPin, setNewPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) {
      setNewPin("");
      setConfirm("");
    }
  }, [open]);

  const match = newPin && newPin === confirm;
  const valid = PIN_RE.test(newPin) && match;

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      // The backend requires a recent re-auth token. The global
      // Axios interceptor in ReauthProvider catches the 401, pops the
      // reauth dialog, and transparently retries this request.
      await api.post("/auth/me/pin/reset", { new_pin: newPin });
      toast.success("Security PIN reset.");
      onDone();
      onOpenChange(false);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !submitting && onOpenChange(v)}>
      <DialogContent
        data-testid="pin-reset-dialog"
        className="max-w-md rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Reset PIN</DialogTitle>
          <DialogDescription>
            Use this when you&rsquo;ve forgotten your PIN. We&rsquo;ll ask for
            a recent password confirmation before the new PIN is stored.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <PinField
            value={newPin}
            onChange={setNewPin}
            label="New PIN"
            testId="pin-reset-new"
          />
          <PinField
            value={confirm}
            onChange={setConfirm}
            label="Confirm new PIN"
            testId="pin-reset-confirm"
          />
          {confirm && !match && (
            <p className="text-[11px] text-destructive">
              PINs do not match.
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={submitting}
              onClick={() => onOpenChange(false)}
              className="rounded-sm"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!valid || submitting}
              data-testid="pin-reset-submit"
              className="rounded-sm"
            >
              {submitting ? "Saving…" : "Reset PIN"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RemovePinDialog({ open, onOpenChange, onDone }) {
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) setPassword("");
  }, [open]);

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.delete("/auth/me/pin", { data: { password } });
      toast.success("Security PIN removed.");
      onDone();
      onOpenChange(false);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !submitting && onOpenChange(v)}>
      <DialogContent
        data-testid="pin-remove-dialog"
        className="max-w-md rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">Remove PIN</DialogTitle>
          <DialogDescription>
            You&rsquo;ll need your password to remove your PIN. You can set a
            new one any time.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <PasswordConfirmField value={password} onChange={setPassword} />
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={submitting}
              onClick={() => onOpenChange(false)}
              className="rounded-sm"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!password || submitting}
              variant="outline"
              data-testid="pin-remove-submit"
              className="rounded-sm border-destructive text-destructive hover:bg-destructive-soft"
            >
              {submitting ? "Removing…" : "Remove PIN"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function PinCard() {
  const [status, setStatus] = useState(null);
  const [err, setErr] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [changeOpen, setChangeOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [removeOpen, setRemoveOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me/pin/status");
      setStatus(data);
      setErr(null);
    } catch (e) {
      setErr(formatApiError(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const configured = !!status?.configured;
  const locked = !!status?.locked_until;

  return (
    <div
      data-testid="pin-card"
      className="rounded-sm border border-border bg-card p-6"
    >
      <div className="flex items-center gap-2">
        <Hash className="h-5 w-5 text-primary" />
        <h2 className="font-display text-2xl font-medium">Security PIN</h2>
        {configured && (
          <span
            data-testid="pin-status-enabled"
            className="ml-auto rounded-sm bg-primary/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-primary"
          >
            Configured
          </span>
        )}
        {!configured && status && (
          <span
            data-testid="pin-status-disabled"
            className="ml-auto rounded-sm bg-muted px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground"
          >
            Not set
          </span>
        )}
      </div>
      <p className="mt-2 text-sm text-muted-foreground">
        A 6-digit PIN you can use for fast in-app re-verification (e.g. signing
        a note or viewing sensitive PHI) without retyping your password. PINs
        are hashed and cannot be displayed back to you.
      </p>

      {err && (
        <p className="mt-3 text-xs text-destructive">
          {err}
        </p>
      )}

      {locked && (
        <div
          data-testid="pin-locked-banner"
          className="mt-4 flex items-start gap-2 rounded-sm border border-destructive/40 bg-destructive-soft p-3 text-xs text-destructive"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4" />
          <div>
            <div className="font-semibold uppercase tracking-wider">
              PIN temporarily locked
            </div>
            <div className="mt-0.5">
              Locked until {formatDateTime(status.locked_until)} after{" "}
              {status.failed_attempts} failed attempts. Reset your PIN to
              unlock immediately.
            </div>
          </div>
        </div>
      )}

      {status && configured && (
        <dl
          className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1 rounded-sm border border-border bg-background p-3 text-xs"
          data-testid="pin-meta"
        >
          <dt className="text-muted-foreground">Created</dt>
          <dd className="font-mono">
            {status.created_at ? formatDateTime(status.created_at) : "—"}
          </dd>
          <dt className="text-muted-foreground">Last rotated</dt>
          <dd className="font-mono">
            {status.updated_at ? formatDateTime(status.updated_at) : "—"}
          </dd>
        </dl>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {!configured ? (
          <Button
            data-testid="pin-set-btn"
            onClick={() => setCreateOpen(true)}
            className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
          >
            <ShieldCheckIcon className="mr-1.5 h-4 w-4" />
            Set PIN
          </Button>
        ) : (
          <>
            <Button
              data-testid="pin-change-btn"
              onClick={() => setChangeOpen(true)}
              className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            >
              <Pencil className="mr-1.5 h-4 w-4" />
              Change PIN
            </Button>
            <Button
              data-testid="pin-reset-btn"
              onClick={() => setResetOpen(true)}
              variant="outline"
              className="rounded-sm"
            >
              <RotateCcw className="mr-1.5 h-4 w-4" />
              Reset PIN
            </Button>
            <Button
              data-testid="pin-remove-btn"
              onClick={() => setRemoveOpen(true)}
              variant="outline"
              className="rounded-sm border-destructive text-destructive hover:bg-destructive-soft"
            >
              <Trash2 className="mr-1.5 h-4 w-4" />
              Remove
            </Button>
          </>
        )}
      </div>

      <CreatePinDialog open={createOpen} onOpenChange={setCreateOpen} onDone={load} />
      <ChangePinDialog open={changeOpen} onOpenChange={setChangeOpen} onDone={load} />
      <ResetPinDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        onDone={load}
      />
      <RemovePinDialog open={removeOpen} onOpenChange={setRemoveOpen} onDone={load} />
    </div>
  );
}
