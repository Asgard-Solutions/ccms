/**
 * Kiosk self check-in — public unauthenticated route.
 *
 * Tablet at the front desk displays this. Patient enters DOB +
 * last name; on success we show a big "You're checked in" confirmation
 * and auto-reset after 10 seconds for the next patient.
 */
import { useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, Fingerprint, Loader2 } from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { kioskCheckIn } from "../api/portal";

export default function Kiosk() {
  const [form, setForm] = useState({ last_name: "", date_of_birth: "" });
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await kioskCheckIn({
        last_name: form.last_name.trim(),
        date_of_birth: form.date_of_birth.trim(),
      });
      setSuccess(res);
      setTimeout(() => {
        setSuccess(null);
        setForm({ last_name: "", date_of_birth: "" });
      }, 10_000);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "We couldn't find your appointment");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      data-testid="kiosk-page"
      className="min-h-screen flex items-center justify-center bg-gradient-to-br from-primary/5 via-background to-muted px-6"
    >
      <div className="w-full max-w-md rounded-md border border-border bg-card shadow-sm p-10">
        {success ? (
          <div className="text-center" data-testid="kiosk-success">
            <CheckCircle2 className="mx-auto h-16 w-16 text-green-600" />
            <h2 className="mt-4 text-2xl font-display tracking-tight">
              You're checked in{success.patient?.first_name ? `, ${success.patient.first_name}` : ""}!
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Please have a seat. A team member will be with you shortly.
            </p>
            <Button
              variant="ghost"
              className="mt-6"
              onClick={() => {
                setSuccess(null);
                setForm({ last_name: "", date_of_birth: "" });
              }}
              data-testid="kiosk-next-patient-btn"
            >
              Next patient
            </Button>
          </div>
        ) : (
          <>
            <header className="mb-6 text-center">
              <div className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-full bg-primary text-primary-foreground">
                <Fingerprint className="h-7 w-7" />
              </div>
              <h1 className="text-2xl font-display tracking-tight">Welcome</h1>
              <p className="text-sm text-muted-foreground mt-1">
                Please check in for your visit.
              </p>
            </header>
            <form onSubmit={submit} className="space-y-4" data-testid="kiosk-form">
              <div>
                <Label htmlFor="last_name">Last name</Label>
                <Input
                  id="last_name"
                  data-testid="kiosk-lastname-input"
                  value={form.last_name}
                  onChange={(e) => setForm((f) => ({ ...f, last_name: e.target.value }))}
                  className="mt-1.5 text-lg"
                  autoFocus
                  required
                />
              </div>
              <div>
                <Label htmlFor="dob">Date of birth</Label>
                <Input
                  id="dob"
                  type="date"
                  data-testid="kiosk-dob-input"
                  value={form.date_of_birth}
                  onChange={(e) => setForm((f) => ({ ...f, date_of_birth: e.target.value }))}
                  className="mt-1.5 text-lg"
                  required
                />
              </div>
              <Button
                type="submit"
                className="w-full h-12 text-base"
                disabled={loading}
                data-testid="kiosk-submit-btn"
              >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Check in"}
              </Button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
