import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Stethoscope, KeyRound, ArrowRight, CheckCircle2 } from "lucide-react";
import { api, formatApiError } from "../api/client";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

function RequestStep({ onTokenIssued }) {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const { data } = await api.post("/auth/password-reset/request", { email });
      setSent(true);
      // Dev convenience: the backend returns `dev_token` in non-prod.
      if (data?.dev_token) onTokenIssued(data.dev_token);
      toast.success("If that account exists, a reset link has been issued.");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (sent) {
    return (
      <div
        data-testid="reset-request-sent"
        className="rounded-sm border border-stone-200 bg-white p-5 text-sm text-[#5C6A61]"
      >
        <div className="flex items-center gap-2 text-[#1F2924]">
          <CheckCircle2 className="h-5 w-5 text-[#526B58]" />
          <span className="font-['Outfit'] text-lg font-medium">Request received</span>
        </div>
        <p className="mt-2">
          If <span className="font-mono text-[#1F2924]">{email}</span> is a valid
          CCMS account, a reset link has been sent. The link expires in 15
          minutes and can be used only once.
        </p>
        <p className="mt-3 text-xs">
          Didn’t get anything? Check your spam folder and the email address you
          used — we won’t confirm whether an account exists.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4" data-testid="reset-request-form">
      <div className="space-y-2">
        <Label htmlFor="email">Account email</Label>
        <Input
          id="email"
          data-testid="reset-email-input"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoFocus
          className="h-11 rounded-sm"
        />
      </div>
      <Button
        type="submit"
        data-testid="reset-request-btn"
        disabled={submitting}
        className="h-11 w-full rounded-sm bg-[#7B9A82] hover:bg-[#65826C]"
      >
        {submitting ? "Sending…" : "Send reset link"}
      </Button>
    </form>
  );
}

function ConfirmStep({ tokenParam }) {
  const [token, setToken] = useState(tokenParam || "");
  const [pwd, setPwd] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const navigate = useNavigate();

  async function submit(e) {
    e.preventDefault();
    if (pwd !== confirm) {
      toast.error("Passwords do not match");
      return;
    }
    setSubmitting(true);
    try {
      await api.post("/auth/password-reset/confirm", { token, new_password: pwd });
      toast.success("Password reset — you can now sign in");
      setDone(true);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div data-testid="reset-confirm-done" className="space-y-4 text-sm text-[#5C6A61]">
        <div className="flex items-center gap-2 text-[#1F2924]">
          <CheckCircle2 className="h-5 w-5 text-[#526B58]" />
          <span className="font-['Outfit'] text-lg font-medium">Password reset complete</span>
        </div>
        <p>
          All previous sessions have been revoked. Sign in with your new
          password to continue.
        </p>
        <Button
          data-testid="reset-goto-login"
          onClick={() => navigate("/login")}
          className="rounded-sm bg-[#7B9A82] hover:bg-[#65826C]"
        >
          Go to sign in <ArrowRight className="ml-2 h-4 w-4" />
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4" data-testid="reset-confirm-form">
      <div className="space-y-2">
        <Label htmlFor="token">Reset token</Label>
        <Input
          id="token"
          data-testid="reset-token-input"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          required
          className="h-11 rounded-sm font-mono text-xs"
          placeholder="Paste the token from your email"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="pwd">New password</Label>
        <Input
          id="pwd"
          data-testid="reset-new-password"
          type="password"
          value={pwd}
          onChange={(e) => setPwd(e.target.value)}
          minLength={12}
          required
          className="h-11 rounded-sm"
        />
        <p className="text-[11px] text-[#5C6A61]">
          Minimum 12 characters with upper, lower, digit and symbol. Cannot
          match your last 5 passwords.
        </p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="confirm">Confirm new password</Label>
        <Input
          id="confirm"
          data-testid="reset-confirm-password"
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          minLength={12}
          required
          className="h-11 rounded-sm"
        />
      </div>
      <Button
        type="submit"
        data-testid="reset-confirm-btn"
        disabled={submitting}
        className="h-11 w-full rounded-sm bg-[#7B9A82] hover:bg-[#65826C]"
      >
        {submitting ? "Resetting…" : "Set new password"}
      </Button>
    </form>
  );
}

export default function PasswordReset() {
  const [params] = useSearchParams();
  const tokenParam = params.get("token");
  const [devToken, setDevToken] = useState(null);
  const [mode, setMode] = useState(tokenParam ? "confirm" : "request");

  return (
    <div
      data-testid="password-reset-page"
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
        <div className="max-w-md space-y-6">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-sm bg-[#EDF2EE] text-[#526B58]">
              <KeyRound className="h-5 w-5" />
            </span>
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
                Account recovery
              </div>
              <h1 className="font-['Outfit'] text-3xl font-medium">
                {mode === "request" ? "Reset your password" : "Choose a new password"}
              </h1>
            </div>
          </div>

          <div className="flex gap-2">
            <button
              data-testid="reset-tab-request"
              onClick={() => setMode("request")}
              className={`rounded-sm px-3 py-1.5 text-xs font-medium uppercase tracking-wider transition-colors ${
                mode === "request"
                  ? "bg-[#1F2924] text-white"
                  : "bg-stone-100 text-[#5C6A61] hover:bg-stone-200"
              }`}
            >
              1. Request link
            </button>
            <button
              data-testid="reset-tab-confirm"
              onClick={() => setMode("confirm")}
              className={`rounded-sm px-3 py-1.5 text-xs font-medium uppercase tracking-wider transition-colors ${
                mode === "confirm"
                  ? "bg-[#1F2924] text-white"
                  : "bg-stone-100 text-[#5C6A61] hover:bg-stone-200"
              }`}
            >
              2. Confirm reset
            </button>
          </div>

          {mode === "request" ? (
            <>
              <RequestStep
                onTokenIssued={(t) => {
                  setDevToken(t);
                  setMode("confirm");
                }}
              />
              {devToken && (
                <div
                  data-testid="dev-token-hint"
                  className="rounded-sm border border-[#EDE0C7] bg-[#FDF6ED] p-3 text-xs text-[#8A6C33]"
                >
                  <div className="font-semibold uppercase tracking-wider">Dev token (pre-production only)</div>
                  <div className="mt-1 break-all font-mono">{devToken}</div>
                </div>
              )}
            </>
          ) : (
            <ConfirmStep tokenParam={tokenParam || devToken} />
          )}

          <div className="pt-2 text-sm text-[#5C6A61]">
            <Link
              to="/login"
              data-testid="reset-back-to-login"
              className="font-medium text-[#526B58] underline-offset-4 hover:underline"
            >
              ← Back to sign in
            </Link>
          </div>
        </div>
      </div>
      <div className="relative hidden overflow-hidden border-l border-stone-200 lg:block">
        <img
          src="https://images.pexels.com/photos/4226119/pexels-photo-4226119.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"
          alt=""
          className="absolute inset-0 h-full w-full object-cover"
        />
        <div className="absolute inset-0 bg-gradient-to-br from-[#FAF9F6]/40 via-[#FAF9F6]/10 to-[#7B9A82]/20" />
      </div>
    </div>
  );
}
