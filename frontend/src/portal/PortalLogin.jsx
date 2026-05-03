/**
 * Patient-portal login — phone-first SMS OTP flow.
 *
 * Two-step UI:
 *   1) Enter phone → request OTP.
 *   2) Enter 6-digit code → verify, set cookies, redirect to /portal.
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { KeyRound, Phone, ShieldCheck } from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { useAuth } from "../contexts/AuthContext";
import { portalOtpRequest, portalOtpVerify } from "../api/portal";

export default function PortalLogin() {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [step, setStep] = useState("phone");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [devCode, setDevCode] = useState(null);
  const [loading, setLoading] = useState(false);

  async function handleRequest(e) {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await portalOtpRequest({ phone: phone.trim() });
      setDevCode(res.dev_code || null);
      setStep("code");
      if (res.dev_code) {
        toast.info(`Dev-mode OTP: ${res.dev_code}`);
      } else {
        toast.success("Code sent. Check your phone.");
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Couldn't send code.");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerify(e) {
    e.preventDefault();
    setLoading(true);
    try {
      await portalOtpVerify({ phone: phone.trim(), code: code.trim() });
      await refresh();
      toast.success("Welcome back.");
      navigate("/portal", { replace: true });
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Invalid or expired code.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      data-testid="portal-login-page"
      className="min-h-screen bg-gradient-to-b from-background to-muted flex items-center justify-center px-6"
    >
      <div className="w-full max-w-md rounded-md border border-border bg-card shadow-sm p-8">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-sm bg-primary text-primary-foreground">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-xl font-display tracking-tight">Patient Portal</h1>
            <p className="text-sm text-muted-foreground">
              Sign in with your mobile number.
            </p>
          </div>
        </div>

        {step === "phone" ? (
          <form onSubmit={handleRequest} className="space-y-4" data-testid="portal-login-phone-form">
            <div>
              <Label htmlFor="phone">Mobile phone</Label>
              <div className="relative mt-1.5">
                <Phone className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="phone"
                  data-testid="portal-login-phone-input"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="(503) 555-0210"
                  className="pl-9"
                  autoFocus
                  required
                />
              </div>
              <p className="mt-1.5 text-xs text-muted-foreground">
                We'll text you a 6-digit code. Standard message rates apply.
              </p>
            </div>
            <Button
              type="submit"
              className="w-full"
              disabled={loading || phone.trim().length < 7}
              data-testid="portal-login-send-btn"
            >
              Send code
            </Button>
          </form>
        ) : (
          <form onSubmit={handleVerify} className="space-y-4" data-testid="portal-login-code-form">
            <div>
              <Label htmlFor="code">6-digit code</Label>
              <div className="relative mt-1.5">
                <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="code"
                  data-testid="portal-login-code-input"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  maxLength={6}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  placeholder="123456"
                  className="pl-9 tracking-[0.4em] text-center text-lg"
                  autoFocus
                  required
                />
              </div>
              {devCode && (
                <p className="mt-1.5 text-xs text-amber-600">
                  Dev mode — code: <code>{devCode}</code>
                </p>
              )}
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setStep("phone")}
                data-testid="portal-login-back-btn"
              >
                Back
              </Button>
              <Button
                type="submit"
                className="flex-1"
                disabled={loading || code.length !== 6}
                data-testid="portal-login-verify-btn"
              >
                Verify & sign in
              </Button>
            </div>
          </form>
        )}

        <p className="mt-6 text-center text-xs text-muted-foreground">
          Don't have a record yet? Ask the front desk to add you.
        </p>
      </div>
    </div>
  );
}
