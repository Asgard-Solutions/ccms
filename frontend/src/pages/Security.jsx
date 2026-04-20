import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Lock, ShieldCheck, KeyRound, CheckCircle2, History, Globe } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import { formatApiError } from "../api/client";
import { formatDateTime, relativeFromNow } from "../utils/time";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

function PasswordChangeCard() {
  const [form, setForm] = useState({ current_password: "", new_password: "", confirm: "" });
  const [submitting, setSubmitting] = useState(false);
  const update = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    if (form.new_password !== form.confirm) {
      toast.error("Passwords do not match");
      return;
    }
    setSubmitting(true);
    try {
      await api.post("/auth/change-password", {
        current_password: form.current_password,
        new_password: form.new_password,
      });
      toast.success("Password updated");
      setForm({ current_password: "", new_password: "", confirm: "" });
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="rounded-sm border border-border bg-card p-6">
      <div className="flex items-center gap-2">
        <Lock className="h-5 w-5 text-primary" />
        <h2 className="font-display text-2xl font-medium">Password</h2>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">
        Must be at least 12 characters with upper, lower, digit, and symbol.
        We remember your last 5 passwords — you cannot reuse them.
      </p>
      <form onSubmit={submit} className="mt-5 space-y-4">
        <div className="space-y-1">
          <Label>Current password</Label>
          <Input
            type="password"
            data-testid="pw-current"
            value={form.current_password}
            onChange={update("current_password")}
            required
            className="rounded-sm"
          />
        </div>
        <div className="space-y-1">
          <Label>New password</Label>
          <Input
            type="password"
            data-testid="pw-new"
            value={form.new_password}
            onChange={update("new_password")}
            minLength={12}
            required
            className="rounded-sm"
          />
        </div>
        <div className="space-y-1">
          <Label>Confirm new password</Label>
          <Input
            type="password"
            data-testid="pw-confirm"
            value={form.confirm}
            onChange={update("confirm")}
            required
            className="rounded-sm"
          />
        </div>
        <Button
          type="submit"
          disabled={submitting}
          data-testid="pw-submit-btn"
          className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
        >
          {submitting ? "Updating…" : "Update password"}
        </Button>
      </form>
    </div>
  );
}

function MfaCard() {
  const { user, refresh } = useAuth();
  const [setup, setSetup] = useState(null); // { secret, otpauth_url, backup_codes }
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [disablePassword, setDisablePassword] = useState("");

  async function start() {
    try {
      const { data } = await api.post("/auth/mfa/setup");
      setSetup(data);
    } catch (err) {
      toast.error(formatApiError(err));
    }
  }

  async function verify(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.post("/auth/mfa/verify", { code });
      toast.success("Multi-factor authentication enabled");
      setSetup(null);
      setCode("");
      await refresh();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function disable(e) {
    e.preventDefault();
    try {
      await api.post("/auth/mfa/disable", { password: disablePassword });
      toast.success("MFA disabled");
      setDisablePassword("");
      await refresh();
    } catch (err) {
      toast.error(formatApiError(err));
    }
  }

  return (
    <div className="rounded-sm border border-border bg-card p-6">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-5 w-5 text-primary" />
        <h2 className="font-display text-2xl font-medium">Two-factor authentication</h2>
        {user?.mfa_enabled && (
          <span
            data-testid="mfa-status-enabled"
            className="ml-auto rounded-sm bg-primary/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-primary"
          >
            Enabled
          </span>
        )}
      </div>
      <p className="mt-2 text-sm text-muted-foreground">
        Required for admin, doctor and staff accounts in production. Uses the
        TOTP standard — scan the QR code with Google Authenticator, 1Password,
        Authy, or any compatible app.
      </p>

      {!user?.mfa_enabled && !setup && (
        <Button
          onClick={start}
          data-testid="mfa-start-btn"
          className="mt-4 rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
        >
          Begin MFA setup
        </Button>
      )}

      {!user?.mfa_enabled && setup && (
        <div className="mt-5 space-y-4">
          <div className="rounded-sm border border-border bg-background p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
              1. Scan or enter manually
            </div>
            <div className="mt-2 flex flex-col items-start gap-4 md:flex-row md:items-center">
              <img
                src={`https://api.qrserver.com/v1/create-qr-code/?data=${encodeURIComponent(
                  setup.otpauth_url
                )}&size=160x160&margin=2`}
                alt="TOTP QR code"
                className="h-40 w-40 rounded-sm border border-border bg-card p-2"
                data-testid="mfa-qr-image"
              />
              <div className="space-y-2">
                <div className="text-xs uppercase tracking-wider text-muted-foreground">
                  Or copy this secret
                </div>
                <code
                  data-testid="mfa-secret-text"
                  className="block break-all rounded-sm bg-card px-3 py-2 font-mono text-sm"
                >
                  {setup.secret}
                </code>
              </div>
            </div>
          </div>

          <div className="rounded-sm border border-border bg-background p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
              2. Save these backup codes — each can be used once
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
              {setup.backup_codes.map((c) => (
                <code
                  key={c}
                  data-testid={`mfa-backup-${c}`}
                  className="rounded-sm bg-card px-2 py-1 text-center font-mono text-xs"
                >
                  {c}
                </code>
              ))}
            </div>
          </div>

          <form onSubmit={verify} className="space-y-3">
            <div className="space-y-1">
              <Label>3. Enter the 6-digit code from your app</Label>
              <Input
                data-testid="mfa-verify-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                autoComplete="one-time-code"
                required
                className="max-w-xs rounded-sm tracking-widest"
              />
            </div>
            <Button
              type="submit"
              data-testid="mfa-verify-btn"
              disabled={submitting}
              className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            >
              {submitting ? "Verifying…" : "Enable MFA"}
            </Button>
          </form>
        </div>
      )}

      {user?.mfa_enabled && (
        <form onSubmit={disable} className="mt-4 flex flex-wrap items-end gap-3">
          <div className="flex-1 space-y-1 min-w-[220px]">
            <Label>Disable MFA — confirm your password</Label>
            <Input
              type="password"
              data-testid="mfa-disable-password"
              value={disablePassword}
              onChange={(e) => setDisablePassword(e.target.value)}
              required
              className="rounded-sm"
            />
          </div>
          <Button
            type="submit"
            variant="outline"
            data-testid="mfa-disable-btn"
            className="rounded-sm border-destructive text-destructive hover:bg-destructive-soft"
          >
            Disable MFA
          </Button>
        </form>
      )}
    </div>
  );
}

export default function Security() {
  const { user } = useAuth();
  const [passwordAge, setPasswordAge] = useState(null);
  useEffect(() => {
    if (!user?.password_changed_at) {
      setPasswordAge(null);
      return;
    }
    const ms = Date.now() - new Date(user.password_changed_at).getTime();
    const days = Math.floor(ms / 86_400_000);
    setPasswordAge(days);
  }, [user]);

  return (
    <div data-testid="security-page" className="space-y-8 animate-in fade-in duration-300">
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Account security
        </span>
        <h1 className="mt-2 font-display text-4xl font-medium tracking-tight">
          Security
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          Your credentials protect every record in the system. Rotate your
          password every 90 days and enable MFA to meet HIPAA technical
          safeguards.
        </p>
      </header>

      {passwordAge !== null && (
        <div
          data-testid="password-age-banner"
          className={`flex items-start gap-3 rounded-sm border p-4 ${
            passwordAge >= 90
              ? "border-warning bg-warning-soft text-muted-foreground"
              : "border-border bg-card text-muted-foreground"
          }`}
        >
          <KeyRound className="mt-0.5 h-4 w-4" />
          <div className="text-sm">
            <div className="font-medium text-foreground">
              Password age: {passwordAge} day{passwordAge === 1 ? "" : "s"}
            </div>
            <div>
              {passwordAge >= 90
                ? "Rotation recommended — update your password below."
                : "Within rotation window (<90 days)."}
            </div>
          </div>
        </div>
      )}

      <PasswordChangeCard />
      <MfaCard />
      <RecentSignInsCard />

      <div className="rounded-sm border border-border bg-card p-6 text-sm text-muted-foreground">
        <div className="flex items-center gap-2 text-foreground">
          <CheckCircle2 className="h-4 w-4 text-primary" />
          <span className="font-display text-base font-medium">Session hardening active</span>
        </div>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>15-minute idle auto-logoff; 12-hour absolute session cap.</li>
          <li>Lockout after 5 failed logins (15-minute window).</li>
          <li>Password / role / status / MFA changes immediately revoke all sessions.</li>
          <li>Step-up re-auth required for delete patient + add medical record.</li>
          <li>All PHI accesses recorded in the audit log.</li>
        </ul>
      </div>
    </div>
  );
}

function RecentSignInsCard() {
  const [data, setData] = useState(null);
  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/auth/sessions", { params: { limit: 10 } });
        setData(data);
      } catch {
        setData({ current_session: {}, events: [] });
      }
    })();
  }, []);

  if (!data) {
    return (
      <div className="rounded-sm border border-border bg-card p-6 text-sm text-muted-foreground">
        Loading recent sign-ins…
      </div>
    );
  }

  return (
    <div
      data-testid="sessions-card"
      className="rounded-sm border border-border bg-card p-6"
    >
      <div className="flex items-center gap-2">
        <History className="h-5 w-5 text-primary" />
        <h2 className="font-display text-2xl font-medium">Recent sign-ins</h2>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">
        Review recent authentication events on your account. Unexpected
        sign-ins? Change your password immediately — this will revoke every
        active session, including suspicious ones.
      </p>

      <div
        data-testid="current-session-panel"
        className="mt-4 rounded-sm border border-border bg-background p-3 text-xs text-muted-foreground"
      >
        <div className="flex items-center gap-2 text-foreground">
          <Globe className="h-3.5 w-3.5" />
          <span className="font-semibold uppercase tracking-wider">This session</span>
        </div>
        <div className="mt-1 space-y-0.5">
          <div>IP: <span className="font-mono text-foreground">{data.current_session?.ip || "unknown"}</span></div>
          <div className="truncate">UA: <span className="font-mono">{data.current_session?.user_agent || "—"}</span></div>
        </div>
      </div>

      <div className="mt-4 overflow-hidden rounded-sm border border-border">
        <table className="w-full text-left text-sm">
          <thead className="bg-background text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">When</th>
              <th className="px-3 py-2 font-medium">Event</th>
              <th className="px-3 py-2 font-medium">Outcome</th>
              <th className="px-3 py-2 font-medium">IP</th>
            </tr>
          </thead>
          <tbody>
            {data.events.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-3 py-4 text-center text-xs text-muted-foreground">
                  No sign-in events yet.
                </td>
              </tr>
            ) : (
              data.events.map((e, i) => (
                <tr
                  key={`${e.created_at}-${i}`}
                  data-testid={`session-row-${i}`}
                  className="border-t border-border"
                >
                  <td className="px-3 py-2 align-top">
                    <div>{formatDateTime(e.created_at)}</div>
                    <div className="text-[11px] text-muted-foreground">{relativeFromNow(e.created_at)}</div>
                  </td>
                  <td className="px-3 py-2 align-top">
                    <code className="font-mono text-xs">{e.action}</code>
                  </td>
                  <td className="px-3 py-2 align-top">
                    <span
                      className={`rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
                        e.outcome === "success"
                          ? "bg-primary/10 text-primary"
                          : "bg-destructive-soft text-destructive"
                      }`}
                    >
                      {e.outcome || "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2 align-top font-mono text-xs text-muted-foreground">
                    {e.ip || "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
