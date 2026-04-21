/**
 * SecurityTab — preserves the original /security page contents:
 *   - Password change
 *   - Multi-factor authentication (setup + disable)
 *   - Recent sign-ins
 *   - Session-hardening summary
 *
 * Extracted from the monolithic Security page so Account Settings can
 * show Profile + Security side-by-side without duplicating any flows.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Lock,
  ShieldCheck,
  CheckCircle2,
  Eye,
  EyeOff,
  History,
  Globe,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { useAuth } from "../../contexts/AuthContext";
import { formatDateTime, relativeFromNow } from "../../utils/time";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import PinCard from "./PinCard";

// Mirrors backend/core/password_policy.py. Kept in sync so UI hints
// never diverge from enforcement.
const POLICY_RULES = [
  { id: "length", label: "At least 12 characters", test: (p) => p.length >= 12 },
  { id: "upper", label: "An uppercase letter", test: (p) => /[A-Z]/.test(p) },
  { id: "lower", label: "A lowercase letter", test: (p) => /[a-z]/.test(p) },
  { id: "digit", label: "A digit", test: (p) => /\d/.test(p) },
  {
    id: "symbol",
    label: "A symbol (!@#$…)",
    test: (p) => /[^A-Za-z0-9]/.test(p),
  },
];

function PolicyChecklist({ value }) {
  return (
    <ul
      data-testid="pw-policy-checklist"
      className="grid gap-1.5 rounded-sm border border-border bg-background p-3 text-xs sm:grid-cols-2"
    >
      {POLICY_RULES.map((r) => {
        const ok = value ? r.test(value) : false;
        return (
          <li
            key={r.id}
            data-testid={`pw-policy-${r.id}${ok ? "-ok" : "-todo"}`}
            className={`flex items-center gap-1.5 ${
              ok ? "text-primary" : "text-muted-foreground"
            }`}
          >
            {ok ? (
              <CheckCircle2 className="h-3.5 w-3.5" />
            ) : (
              <span className="inline-block h-3.5 w-3.5 rounded-full border border-muted-foreground/40" />
            )}
            <span>{r.label}</span>
          </li>
        );
      })}
    </ul>
  );
}

function PasswordChangeCard() {
  const { refresh } = useAuth();
  const [form, setForm] = useState({
    current_password: "",
    new_password: "",
    confirm: "",
  });
  const [show, setShow] = useState({
    current_password: false,
    new_password: false,
    confirm: false,
  });
  const [submitting, setSubmitting] = useState(false);
  const update = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const toggleShow = (k) => () => setShow((s) => ({ ...s, [k]: !s[k] }));

  const allRulesOk = POLICY_RULES.every((r) => r.test(form.new_password));
  const matches = form.new_password && form.new_password === form.confirm;
  const sameAsCurrent =
    form.new_password && form.new_password === form.current_password;
  const submittable =
    form.current_password &&
    form.new_password &&
    form.confirm &&
    allRulesOk &&
    matches &&
    !sameAsCurrent &&
    !submitting;

  async function submit(e) {
    e.preventDefault();
    // Local guards — backend re-validates, but these keep the UX sharp.
    if (!allRulesOk) {
      toast.error("Password does not meet the policy rules.");
      return;
    }
    if (!matches) {
      toast.error("Passwords do not match");
      return;
    }
    if (sameAsCurrent) {
      toast.error("Choose a password different from your current one.");
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await api.post("/auth/change-password", {
        current_password: form.current_password,
        new_password: form.new_password,
      });
      toast.success(
        data?.other_sessions_revoked
          ? "Password updated. All other sessions signed out."
          : "Password updated.",
      );
      setForm({ current_password: "", new_password: "", confirm: "" });
      // Refresh /auth/me so `password_changed_at` updates the age banner.
      refresh?.();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      data-testid="password-card"
      className="rounded-sm border border-border bg-card p-6"
    >
      <div className="flex items-center gap-2">
        <Lock className="h-5 w-5 text-primary" aria-hidden="true" />
        <h2 className="font-display text-2xl font-medium">Password</h2>
        <span className="ml-auto rounded-sm bg-muted px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Primary login credential
        </span>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">
        Used to sign in to the clinic system. Must be at least 12 characters
        with upper, lower, digit, and symbol. We remember your last 5 passwords
        — you cannot reuse them. Changing your password signs you out of all{" "}
        <em>other</em> sessions.
      </p>
      <form onSubmit={submit} className="mt-5 space-y-4" aria-label="Change password">
        <PasswordField
          label="Current password"
          testId="pw-current"
          value={form.current_password}
          onChange={update("current_password")}
          show={show.current_password}
          onToggleShow={toggleShow("current_password")}
          autoComplete="current-password"
        />
        <div className="space-y-2">
          <PasswordField
            label="New password"
            testId="pw-new"
            value={form.new_password}
            onChange={update("new_password")}
            show={show.new_password}
            onToggleShow={toggleShow("new_password")}
            autoComplete="new-password"
            minLength={12}
          />
          <PolicyChecklist value={form.new_password} />
          {sameAsCurrent && (
            <p
              data-testid="pw-same-as-current-hint"
              role="alert"
              aria-live="polite"
              className="text-[11px] text-destructive"
            >
              New password must differ from your current password.
            </p>
          )}
        </div>
        <div className="space-y-1">
          <PasswordField
            label="Confirm new password"
            testId="pw-confirm"
            value={form.confirm}
            onChange={update("confirm")}
            show={show.confirm}
            onToggleShow={toggleShow("confirm")}
            autoComplete="new-password"
          />
          {form.confirm && !matches && (
            <p
              data-testid="pw-mismatch-hint"
              role="alert"
              aria-live="polite"
              className="text-[11px] text-destructive"
            >
              Passwords do not match.
            </p>
          )}
        </div>
        <Button
          type="submit"
          disabled={!submittable}
          data-testid="pw-submit-btn"
          className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
        >
          {submitting ? "Updating…" : "Update password"}
        </Button>
      </form>
    </div>
  );
}

function PasswordField({
  label,
  testId,
  value,
  onChange,
  show,
  onToggleShow,
  autoComplete,
  minLength,
}) {
  const inputId = `${testId}-input`;
  const hintId = `${testId}-hint`;
  return (
    <div className="space-y-1">
      <Label htmlFor={inputId}>{label}</Label>
      <div className="relative">
        <Input
          id={inputId}
          type={show ? "text" : "password"}
          data-testid={testId}
          value={value}
          onChange={onChange}
          autoComplete={autoComplete}
          minLength={minLength}
          required
          aria-describedby={hintId}
          className="rounded-sm pr-10 focus-visible:ring-2 focus-visible:ring-primary"
        />
        <button
          type="button"
          onClick={onToggleShow}
          data-testid={`${testId}-toggle`}
          aria-label={show ? "Hide password" : "Show password"}
          aria-pressed={show}
          aria-controls={inputId}
          className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:rounded-sm"
        >
          {show ? (
            <EyeOff className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Eye className="h-4 w-4" aria-hidden="true" />
          )}
        </button>
      </div>
    </div>
  );
}

function MfaCard() {
  const { user, refresh } = useAuth();
  const [setup, setSetup] = useState(null);
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
    <div className="rounded-sm border border-border bg-card p-6" data-testid="mfa-card">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-5 w-5 text-primary" aria-hidden="true" />
        <h2 className="font-display text-2xl font-medium">
          Two-factor authentication
        </h2>
        <span className="ml-auto flex items-center gap-2">
          <span className="rounded-sm bg-muted px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Extra sign-in check
          </span>
          {user?.mfa_enabled && (
            <span
              data-testid="mfa-status-enabled"
              className="rounded-sm bg-primary/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-primary"
            >
              Enabled
            </span>
          )}
        </span>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">
        After you enter your password at sign-in we&rsquo;ll also ask for a
        rotating 6-digit code from your authenticator app. Strongly
        recommended for clinical roles and required for admin, doctor, and
        staff accounts in production. Uses the TOTP standard (Google
        Authenticator, 1Password, Authy, etc.).
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
                  setup.otpauth_url,
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

          <form onSubmit={verify} className="space-y-3" aria-label="Enable multi-factor authentication">
            <div className="space-y-1">
              <Label htmlFor="mfa-verify-code-input">3. Enter the 6-digit code from your app</Label>
              <Input
                id="mfa-verify-code-input"
                data-testid="mfa-verify-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                maxLength={6}
                pattern="\d{6}"
                className="max-w-xs rounded-sm tracking-widest focus-visible:ring-2 focus-visible:ring-primary"
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
        <form
          onSubmit={disable}
          className="mt-4 flex flex-wrap items-end gap-3"
          aria-label="Disable multi-factor authentication"
        >
          <div className="flex-1 space-y-1 min-w-[220px]">
            <Label htmlFor="mfa-disable-password-input">
              Disable MFA — confirm your password
            </Label>
            <Input
              id="mfa-disable-password-input"
              type="password"
              data-testid="mfa-disable-password"
              value={disablePassword}
              onChange={(e) => setDisablePassword(e.target.value)}
              autoComplete="current-password"
              required
              className="rounded-sm focus-visible:ring-2 focus-visible:ring-primary"
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

function RecentSignInsCard() {
  const [data, setData] = useState(null);
  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/auth/sessions", {
          params: { limit: 10 },
        });
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
          <span className="font-semibold uppercase tracking-wider">
            This session
          </span>
        </div>
        <div className="mt-1 space-y-0.5">
          <div>
            IP:{" "}
            <span className="font-mono text-foreground">
              {data.current_session?.ip || "unknown"}
            </span>
          </div>
          <div className="truncate">
            UA:{" "}
            <span className="font-mono">
              {data.current_session?.user_agent || "—"}
            </span>
          </div>
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
                <td
                  colSpan={4}
                  className="px-3 py-4 text-center text-xs text-muted-foreground"
                >
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
                    <div className="text-[11px] text-muted-foreground">
                      {relativeFromNow(e.created_at)}
                    </div>
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

function SecurityPostureStrip({ user, passwordAge }) {
  const mfaOn = !!user?.mfa_enabled;
  const pinOn = !!user?.pin_configured;
  const ageLabel =
    passwordAge === null
      ? "—"
      : `${passwordAge} day${passwordAge === 1 ? "" : "s"}`;
  const ageTone =
    passwordAge === null
      ? "muted"
      : passwordAge >= 90
      ? "warn"
      : "ok";

  const tiles = [
    {
      key: "mfa",
      label: "Two-factor auth",
      value: mfaOn ? "Enabled" : "Off",
      tone: mfaOn ? "ok" : "warn",
      hint: mfaOn
        ? "Login requires an authenticator code."
        : "Recommended — add a second factor below.",
    },
    {
      key: "pin",
      label: "Security PIN",
      value: pinOn ? "Configured" : "Not set",
      tone: pinOn ? "ok" : "muted",
      hint: pinOn
        ? "Used for fast in-app re-verification."
        : "Optional — skip re-typing your password for sensitive actions.",
    },
    {
      key: "pwage",
      label: "Password age",
      value: ageLabel,
      tone: ageTone,
      hint:
        passwordAge === null
          ? "Unknown."
          : passwordAge >= 90
          ? "Rotate — exceeds 90-day window."
          : "Within the 90-day rotation window.",
    },
  ];

  return (
    <section
      data-testid="security-posture-strip"
      aria-label="Security posture summary"
      className="grid gap-3 sm:grid-cols-3"
    >
      {tiles.map((t) => (
        <div
          key={t.key}
          data-testid={`posture-tile-${t.key}`}
          className="rounded-sm border border-border bg-card p-4"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
              {t.label}
            </span>
            <span
              className={`rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
                t.tone === "ok"
                  ? "bg-primary/10 text-primary"
                  : t.tone === "warn"
                  ? "bg-warning-soft text-foreground"
                  : "bg-muted text-muted-foreground"
              }`}
            >
              {t.value}
            </span>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">{t.hint}</p>
        </div>
      ))}
    </section>
  );
}

function SectionHeader({ eyebrow, title, description }) {
  return (
    <header className="space-y-1">
      <span className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
        {eyebrow}
      </span>
      <h2 className="font-display text-xl font-medium tracking-tight">
        {title}
      </h2>
      <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>
    </header>
  );
}

export default function SecurityTab() {
  const { user } = useAuth();
  const [passwordAge, setPasswordAge] = useState(null);

  useEffect(() => {
    if (!user?.password_changed_at) {
      setPasswordAge(null);
      return;
    }
    const ms = Date.now() - new Date(user.password_changed_at).getTime();
    setPasswordAge(Math.floor(ms / 86_400_000));
  }, [user]);

  return (
    <section
      data-testid="security-tab"
      className="space-y-10 animate-in fade-in duration-200"
    >
      <SecurityPostureStrip user={user} passwordAge={passwordAge} />

      <div className="space-y-5">
        <SectionHeader
          eyebrow="Sign-in methods"
          title="How you sign in and verify sensitive actions"
          description="Your password lets you sign in. Two-factor auth adds a second step at sign-in. Your optional Security PIN is a shortcut for re-verifying once you're already signed in — it does not replace your password."
        />
        <div
          data-testid="signin-methods-group"
          className="space-y-6"
          aria-labelledby="signin-methods-heading"
        >
          <PasswordChangeCard />
          <MfaCard />
          <PinCard />
        </div>
      </div>

      <div className="space-y-5">
        <SectionHeader
          eyebrow="Session activity & posture"
          title="Recent sign-ins and protections in effect"
          description="Review where and when your account was used, and see which platform-level safeguards are active. Unexpected sign-in? Update your password to end all other sessions."
        />
        <div
          data-testid="session-activity-group"
          className="space-y-6"
        >
          <RecentSignInsCard />

          <div
            data-testid="session-hardening-card"
            className="rounded-sm border border-border bg-card p-6 text-sm text-muted-foreground"
          >
            <div className="flex items-center gap-2 text-foreground">
              <CheckCircle2 className="h-4 w-4 text-primary" aria-hidden="true" />
              <span className="font-display text-base font-medium">
                Session hardening active
              </span>
            </div>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>15-minute idle auto-logoff; 12-hour absolute session cap.</li>
              <li>Lockout after 5 failed logins (15-minute window).</li>
              <li>
                Password / role / status / MFA changes immediately revoke all
                sessions.
              </li>
              <li>Step-up re-auth required for delete patient + add medical record.</li>
              <li>All PHI accesses recorded in the audit log.</li>
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}
