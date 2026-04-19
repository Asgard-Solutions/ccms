import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Stethoscope, ShieldCheck } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

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
        <span className="flex h-9 w-9 items-center justify-center rounded-sm bg-[#EDF2EE] text-[#526B58]">
          <ShieldCheck className="h-5 w-5" />
        </span>
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
            Second factor
          </div>
          <h2 className="font-['Outfit'] text-2xl font-medium">Enter your authenticator code</h2>
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
            className="h-11 rounded-sm border-stone-200 tracking-widest"
          />
        </div>
        <Button
          type="submit"
          data-testid="mfa-submit-btn"
          disabled={submitting || !code.trim()}
          className="h-11 w-full rounded-sm bg-[#7B9A82] hover:bg-[#65826C]"
        >
          {submitting ? "Verifying…" : "Verify & continue"}
        </Button>
        <button
          type="button"
          onClick={logout}
          className="w-full text-center text-xs text-[#5C6A61] hover:text-[#1F2924]"
        >
          Cancel and start over
        </button>
      </form>
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
      <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
        Sign in
      </span>
      <h1 className="mt-3 font-['Outfit'] text-4xl font-medium leading-none tracking-tight text-[#1F2924] sm:text-5xl">
        Your clinic,
        <br />
        in one calm place.
      </h1>
      <p className="mt-6 text-base leading-relaxed text-[#5C6A61]">
        Cookie-based JWT auth, TOTP MFA, and a full audit trail. PHI is masked
        by default and encrypted at rest.
      </p>

      <form onSubmit={onSubmit} className="mt-10 space-y-5">
        <div className="space-y-2">
          <Label htmlFor="email" className="text-[#5C6A61]">Email</Label>
          <Input
            id="email"
            data-testid="login-email-input"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="h-11 rounded-sm border-stone-200"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password" className="text-[#5C6A61]">Password</Label>
          <Input
            id="password"
            data-testid="login-password-input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="h-11 rounded-sm border-stone-200"
          />
        </div>

        <Button
          type="submit"
          data-testid="login-submit-button"
          disabled={submitting}
          className="h-11 w-full rounded-sm bg-[#7B9A82] px-6 font-medium text-white transition-colors hover:bg-[#65826C] active:scale-[0.99]"
        >
          {submitting ? "Signing in…" : "Sign in"}
        </Button>

        <p className="text-center text-sm text-[#5C6A61]">
          New patient?{" "}
          <Link
            to="/register"
            data-testid="login-to-register-link"
            className="font-medium text-[#526B58] underline-offset-4 hover:underline"
          >
            Create an account
          </Link>
        </p>
      </form>

      <div className="mt-10 rounded-sm border border-stone-200 bg-white p-4 text-xs text-[#5C6A61]">
        <div className="mb-2 font-semibold uppercase tracking-[0.15em]">
          Demo credentials
        </div>
        <div className="grid grid-cols-[6rem_1fr] gap-x-4 gap-y-1 font-mono">
          <span className="font-sans">Admin</span><span>admin@ccms.app / Admin@ComplianceClinic1</span>
          <span className="font-sans">Doctor</span><span>doctor@ccms.app / Doctor@ComplianceClinic1</span>
          <span className="font-sans">Staff</span><span>staff@ccms.app / Staff@ComplianceClinic1</span>
          <span className="font-sans">Patient</span><span>patient@ccms.app / Patient@ComplianceClinic1</span>
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
      className="grid min-h-screen grid-cols-1 bg-[#FAF9F6] lg:grid-cols-[1.05fr_1fr]"
    >
      <div className="flex flex-col justify-center px-8 py-12 md:px-16 lg:px-24">
        <Link to="/login" className="mb-10 flex items-center gap-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-sm bg-[#7B9A82] text-white">
            <Stethoscope className="h-5 w-5" />
          </span>
          <span className="font-['Outfit'] text-lg font-medium text-[#1F2924]">
            CCMS
          </span>
        </Link>

        {mfaContext?.mfa_ticket ? <MfaStep /> : <LoginForm />}
      </div>

      <div className="relative hidden overflow-hidden border-l border-stone-200 lg:block" aria-hidden="true">
        <img
          src="https://images.pexels.com/photos/8459996/pexels-photo-8459996.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"
          alt=""
          className="absolute inset-0 h-full w-full object-cover"
        />
        <div className="absolute inset-0 bg-gradient-to-br from-[#FAF9F6]/40 via-[#FAF9F6]/10 to-[#7B9A82]/20" />
        <div className="relative z-10 flex h-full flex-col justify-end p-12 text-[#1F2924]">
          <blockquote className="max-w-md font-['Outfit'] text-2xl font-medium leading-tight">
            “The calm scheduling desk we always wished we had.”
          </blockquote>
          <cite className="mt-4 block text-sm not-italic text-[#5C6A61]">
            Dr. A. Monroe — Chiropractic Clinic, Portland
          </cite>
        </div>
      </div>
    </div>
  );
}
