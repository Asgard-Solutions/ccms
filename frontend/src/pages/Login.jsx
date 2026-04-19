import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Stethoscope } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

export default function Login() {
  const { user, login, formatApiError } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("admin@ccms.app");
  const [password, setPassword] = useState("Admin@123");
  const [submitting, setSubmitting] = useState(false);

  if (user) return <Navigate to={location.state?.from?.pathname || "/"} replace />;

  async function onSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      toast.success("Welcome back");
      navigate("/");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

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
            Manage patients, scheduling, and provider communication through a
            single event-driven workspace.
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
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              <span>Admin</span><span className="font-mono">admin@ccms.app / Admin@123</span>
              <span>Doctor</span><span className="font-mono">doctor@ccms.app / Doctor@123</span>
              <span>Staff</span><span className="font-mono">staff@ccms.app / Staff@123</span>
              <span>Patient</span><span className="font-mono">patient@ccms.app / Patient@123</span>
            </div>
          </div>
        </div>
      </div>

      <div
        className="relative hidden overflow-hidden border-l border-stone-200 lg:block"
        aria-hidden="true"
      >
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
