import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Stethoscope } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";
import { api } from "../api/client";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

const PRIVACY_POLICY_VERSION = "2026-02-v1";

export default function Register() {
  const { user, register, formatApiError } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "", phone: "" });
  const [acceptedPrivacy, setAcceptedPrivacy] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  if (user) return <Navigate to="/" replace />;

  const update = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function onSubmit(e) {
    e.preventDefault();
    if (!acceptedPrivacy) {
      toast.error("Please accept the Privacy Notice to continue");
      return;
    }
    setSubmitting(true);
    try {
      await register({
        name: form.name.trim(),
        email: form.email.trim(),
        password: form.password,
        phone: form.phone.trim() || null,
      });
      // Record versioned consent immediately after registration (fire-and-forget).
      try {
        await api.post("/privacy/consents/accept", {
          policy_type: "privacy_notice",
          policy_version: PRIVACY_POLICY_VERSION,
          action: "accepted",
        });
      } catch { /* non-blocking */ }
      toast.success("Account created");
      navigate("/");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      data-testid="register-page"
      className="flex min-h-screen items-center justify-center surface-app px-6 py-12"
    >
      <div className="w-full max-w-md">
        <Link to="/login" className="mb-8 flex items-center gap-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-sm bg-sage text-white">
            <Stethoscope className="h-5 w-5" />
          </span>
          <span className="font-['Outfit'] text-lg font-medium text-strong">CCMS</span>
        </Link>

        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-strong">
          Create a patient account
        </span>
        <h1 className="mt-3 font-['Outfit'] text-3xl font-medium tracking-tight text-strong">
          Join your clinic portal
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted-strong">
          After registration an intake team member will complete your medical
          profile.
        </p>

        <form onSubmit={onSubmit} className="mt-8 space-y-5 rounded-sm border border-subtle bg-card p-6">
          <div className="space-y-2">
            <Label htmlFor="name" className="text-muted-strong">Full name</Label>
            <Input
              id="name"
              data-testid="register-name-input"
              value={form.name}
              onChange={update("name")}
              required
              className="h-11 rounded-sm border-subtle"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="email" className="text-muted-strong">Email</Label>
            <Input
              id="email"
              data-testid="register-email-input"
              type="email"
              value={form.email}
              onChange={update("email")}
              required
              className="h-11 rounded-sm border-subtle"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="phone" className="text-muted-strong">Phone (optional)</Label>
            <Input
              id="phone"
              data-testid="register-phone-input"
              value={form.phone}
              onChange={update("phone")}
              className="h-11 rounded-sm border-subtle"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password" className="text-muted-strong">Password</Label>
            <Input
              id="password"
              data-testid="register-password-input"
              type="password"
              value={form.password}
              onChange={update("password")}
              required
              minLength={6}
              className="h-11 rounded-sm border-subtle"
            />
          </div>

          <Button
            type="submit"
            data-testid="register-submit-button"
            disabled={submitting || !acceptedPrivacy}
            className="h-11 w-full rounded-sm bg-sage px-6 font-medium text-white hover:bg-sage-hover disabled:opacity-60"
          >
            {submitting ? "Creating…" : "Create account"}
          </Button>

          <label
            htmlFor="privacy-accept"
            className="flex items-start gap-2 rounded-sm border border-subtle surface-app p-3 text-xs text-muted-strong"
          >
            <input
              id="privacy-accept"
              data-testid="register-privacy-checkbox"
              type="checkbox"
              checked={acceptedPrivacy}
              onChange={(e) => setAcceptedPrivacy(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded-sm border-strong accent-primary"
            />
            <span>
              I have read and accept the Privacy Notice (version
              <span className="mx-1 font-mono">{PRIVACY_POLICY_VERSION}</span>)
              and acknowledge how CCMS collects, stores, and processes my data.
              This acceptance is recorded against my account for compliance
              purposes.
            </span>
          </label>

          <p className="text-center text-sm text-muted-strong">
            Already have an account?{" "}
            <Link
              to="/login"
              data-testid="register-to-login-link"
              className="font-medium text-sage-deep underline-offset-4 hover:underline"
            >
              Sign in
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
