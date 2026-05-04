import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Stethoscope, ShieldCheck } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { googleAvailability } from "../api/integrations";

function MfaStep() {
  const { verifyMfa, formatApiError, logout } = useAuth();
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await verifyMfa(code);
      toast.success("Authentication complete");
      navigate("/");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-md space-y-6">
      <div className="flex items-center gap-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-sm bg-primary/10 text-primary">
          <ShieldCheck className="h-5 w-5" />
        </span>
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Second factor
          </div>
          <h2 className="font-display text-2xl font-medium">Enter your authenticator code</h2>
        </div>
      </div>
      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="mfa">6-digit code (or backup code)</Label>
          <Input
            id="mfa"
            data-testid="mfa-code-input"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoFocus
            autoComplete="one-time-code"
            inputMode="numeric"
            required
            className="h-11 rounded-sm border-border tracking-widest"
          />
        </div>
        <Button
          type="submit"
          data-testid="mfa-submit-btn"
          disabled={submitting || !code.trim()}
          className="h-11 w-full rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
        >
          {submitting ? "Verifying…" : "Verify & continue"}
        </Button>
        <button
          type="button"
          onClick={logout}
          className="w-full text-center text-xs text-muted-foreground hover:text-foreground"
        >
          Cancel and start over
        </button>
      </form>
    </div>
  );
}

function GoogleSignInButton() {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    googleAvailability()
      .then((r) => setAvailable(!!r?.enabled))
      .catch(() => setAvailable(false));
  }, []);

  if (!available) return null;

  function handleClick() {
    // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS,
    // THIS BREAKS THE AUTH.
    const redirect = window.location.origin + "/auth/google/callback";
    window.location.href =
      "https://auth.emergentagent.com/?redirect=" + encodeURIComponent(redirect);
  }

  return (
    <div className="space-y-3">
      <button
        type="button"
        onClick={handleClick}
        data-testid="login-google-btn"
        className="flex h-11 w-full items-center justify-center gap-2 rounded-sm border border-border bg-card px-4 text-sm font-medium text-foreground transition hover:bg-muted active:scale-[0.99]"
      >
        <svg viewBox="0 0 48 48" className="h-5 w-5" aria-hidden="true">
          <path fill="#FFC107" d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z"/>
          <path fill="#FF3D00" d="M6.306 14.691l6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z"/>
          <path fill="#4CAF50" d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238C29.211 35.091 26.715 36 24 36c-5.202 0-9.619-3.317-11.283-7.946l-6.522 5.025C9.505 39.556 16.227 44 24 44z"/>
          <path fill="#1976D2" d="M43.611 20.083H42V20H24v8h11.303c-.792 2.237-2.231 4.166-4.087 5.571.001-.001.002-.001.003-.002l6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z"/>
        </svg>
        Sign in with Google
      </button>
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="h-px flex-1 bg-border" />
        <span>or with your email</span>
        <span className="h-px flex-1 bg-border" />
      </div>
    </div>
  );
}

function LoginForm() {
  const { login, formatApiError } = useAuth();
  const [email, setEmail] = useState("admin@ccms.app");
  const [password, setPassword] = useState("Admin@ComplianceClinic1");
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  async function onSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const res = await login(email.trim(), password);
      if (res.mfa_required) {
        toast.info("Two-factor authentication required");
        return;
      }
      toast.success("Welcome back");
      navigate(location.state?.from?.pathname || "/");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-md">
      <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
        Sign in
      </span>
      <h1 className="mt-3 font-display text-4xl font-medium leading-none tracking-tight text-foreground sm:text-5xl">
        Your clinic,
        <br />
        in one calm place.
      </h1>
      <p className="mt-6 text-base leading-relaxed text-muted-foreground">
        Cookie-based JWT auth, TOTP MFA, and a full audit trail. PHI is masked
        by default and encrypted at rest.
      </p>

      <form onSubmit={onSubmit} className="mt-10 space-y-5">
        <GoogleSignInButton />
        <div className="space-y-2">
          <Label htmlFor="email" className="text-muted-foreground">Email</Label>
          <Input
            id="email"
            data-testid="login-email-input"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="h-11 rounded-sm border-border"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password" className="text-muted-foreground">Password</Label>
          <Input
            id="password"
            data-testid="login-password-input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="h-11 rounded-sm border-border"
          />
        </div>

        <Button
          type="submit"
          data-testid="login-submit-button"
          disabled={submitting}
          className="h-11 w-full rounded-sm bg-primary px-6 font-medium text-primary-foreground transition-colors hover:bg-[var(--primary-hover)] active:scale-[0.99]"
        >
          {submitting ? "Signing in…" : "Sign in"}
        </Button>

        <p className="text-center text-sm text-muted-foreground">
          <Link
            to="/password-reset"
            data-testid="login-forgot-password-link"
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            Forgot your password?
          </Link>
        </p>

        <p className="text-center text-sm text-muted-foreground">
          New patient?{" "}
          <Link
            to="/register"
            data-testid="login-to-register-link"
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            Create an account
          </Link>
        </p>
      </form>

      <div
        data-testid="login-demo-credentials"
        className="mt-10 rounded-sm border border-border bg-card p-4 text-xs text-muted-foreground"
      >
        <div className="mb-1 font-semibold uppercase tracking-[0.15em] text-foreground">
          Demo clinic sign-in
        </div>
        <p className="mb-3 text-[11px] leading-relaxed">
          Accounts are fictional staff + patients at{" "}
          <span className="font-medium text-foreground">
            Riverbend Chiropractic &amp; Wellness
          </span>
          . Click a role to auto-fill credentials.
        </p>
        <div className="grid gap-1.5">
          {[
            {
              role: "Administrator",
              person: "Ava Bennett",
              email: "admin@ccms.app",
              password: "Admin@ComplianceClinic1",
            },
            {
              role: "Chiropractor",
              person: "Dr. Noah Carter, DC",
              email: "doctor@ccms.app",
              password: "Doctor@ComplianceClinic1",
            },
            {
              role: "Front desk",
              person: "Mia Ramirez",
              email: "staff@ccms.app",
              password: "Staff@ComplianceClinic1",
            },
            {
              role: "Patient portal",
              person: "Ethan Parker",
              email: "patient@ccms.app",
              password: "Patient@ComplianceClinic1",
            },
          ].map((d) => (
            <button
              key={d.email}
              type="button"
              data-testid={`login-demo-${d.role.toLowerCase().replace(/\s+/g, "-")}`}
              onClick={() => { setEmail(d.email); setPassword(d.password); }}
              className="grid grid-cols-[6.5rem_1fr] items-baseline gap-x-4 rounded-sm px-1 py-0.5 text-left transition-colors hover:bg-muted/60"
            >
              <span className="font-sans text-[11px] font-semibold uppercase tracking-[0.12em] text-foreground">
                {d.role}
              </span>
              <span className="flex flex-col font-mono text-[11px] leading-tight">
                <span className="font-sans text-foreground">{d.person}</span>
                <span className="text-muted-foreground">{d.email}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function Login() {
  const { user, mfaContext } = useAuth();
  if (user) return <Navigate to="/" replace />;

  return (
    <div
      data-testid="login-page"
      className="grid min-h-screen grid-cols-1 bg-background lg:grid-cols-[1.05fr_1fr]"
    >
      <div className="flex flex-col justify-center px-8 py-12 md:px-16 lg:px-24">
        <Link to="/login" className="mb-10 flex items-center gap-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-sm bg-primary text-primary-foreground">
            <Stethoscope className="h-5 w-5" />
          </span>
          <span className="font-display text-lg font-medium text-foreground">
            CCMS
          </span>
        </Link>

        {mfaContext?.mfa_ticket ? <MfaStep /> : <LoginForm />}
      </div>

      <div className="relative hidden overflow-hidden border-l border-border lg:block" aria-hidden="true">
        <img
          src="https://images.pexels.com/photos/8459996/pexels-photo-8459996.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"
          alt=""
          className="absolute inset-0 h-full w-full object-cover"
        />
        <div className="absolute inset-0 bg-gradient-to-br from-background/40 via-background/10 to-primary/20" />
        <div className="relative z-10 flex h-full flex-col justify-end p-12 text-foreground">
          <blockquote className="max-w-md font-display text-2xl font-medium leading-tight">
            “The calm scheduling desk we always wished we had.”
          </blockquote>
          <cite className="mt-4 block text-sm not-italic text-muted-foreground">
            Dr. A. Monroe — Chiropractic Clinic, Portland
          </cite>
        </div>
      </div>
    </div>
  );
}
