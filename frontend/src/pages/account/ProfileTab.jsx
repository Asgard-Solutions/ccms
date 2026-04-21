/**
 * ProfileTab — Self-service profile editor for the logged-in user.
 *
 * All fields are optional except email (required by the user record).
 * Email changes trigger a reauth modal + session bounce, mirroring the
 * backend's `require_reauth` gate on PATCH /auth/me/profile.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Loader2, Save, User2 } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { useAuth } from "../../contexts/AuthContext";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { PhoneInput } from "../../components/PhoneInput";
import { normalizePhone, formatAsTyped } from "../../utils/phone";

const TIME_ZONES = [
  { value: "America/New_York", label: "Eastern (America/New_York)" },
  { value: "America/Chicago", label: "Central (America/Chicago)" },
  { value: "America/Denver", label: "Mountain (America/Denver)" },
  { value: "America/Phoenix", label: "Arizona (America/Phoenix)" },
  { value: "America/Los_Angeles", label: "Pacific (America/Los_Angeles)" },
  { value: "America/Anchorage", label: "Alaska (America/Anchorage)" },
  { value: "Pacific/Honolulu", label: "Hawaii (Pacific/Honolulu)" },
  { value: "UTC", label: "UTC" },
  { value: "Europe/London", label: "UK (Europe/London)" },
  { value: "Europe/Berlin", label: "Central Europe (Europe/Berlin)" },
  { value: "Asia/Kolkata", label: "India (Asia/Kolkata)" },
  { value: "Asia/Dubai", label: "Gulf (Asia/Dubai)" },
  { value: "Australia/Sydney", label: "Sydney (Australia/Sydney)" },
];

const NO_TZ_VALUE = "__no_tz__";

const EMPTY_FORM = {
  first_name: "",
  last_name: "",
  display_name: "",
  email: "",
  mobile_phone: "",
  work_phone: "",
  job_title: "",
  credentials_suffix: "",
  preferred_signature_name: "",
  time_zone: "",
};

function normalise(user) {
  if (!user) return EMPTY_FORM;
  return {
    first_name: user.first_name || "",
    last_name: user.last_name || "",
    display_name: user.display_name || "",
    email: user.email || "",
    mobile_phone: formatAsTyped(user.mobile_phone || ""),
    work_phone: formatAsTyped(user.work_phone || ""),
    job_title: user.job_title || "",
    credentials_suffix: user.credentials_suffix || "",
    preferred_signature_name: user.preferred_signature_name || "",
    time_zone: user.time_zone || "",
  };
}

export default function ProfileTab() {
  const { user, refresh } = useAuth();
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setForm(normalise(user));
  }, [user]);

  const original = useMemo(() => normalise(user), [user]);
  const emailChanging =
    form.email.trim().toLowerCase() !== (original.email || "").toLowerCase();
  const dirty = Object.keys(EMPTY_FORM).some(
    (k) => (form[k] || "") !== (original[k] || ""),
  );

  const set = (k) => (e) => {
    const value = typeof e === "string" ? e : e.target.value;
    setForm((f) => ({ ...f, [k]: value }));
  };

  const derivedPreview =
    (form.display_name || "").trim() ||
    `${(form.first_name || "").trim()} ${(form.last_name || "").trim()}`.trim() ||
    original.email;

  async function save(e) {
    e?.preventDefault?.();
    // Build the diff — send only changed keys (backend honours PATCH semantics).
    const PHONE_KEYS = new Set(["mobile_phone", "work_phone"]);
    const body = {};
    for (const key of Object.keys(EMPTY_FORM)) {
      if ((form[key] || "") !== (original[key] || "")) {
        const raw = (form[key] || "").trim();
        if (PHONE_KEYS.has(key)) {
          // Empty clears; otherwise canonicalise to 10 digits. Backend
          // also enforces — fallback keeps form submittable even if the
          // user types a 10-digit partial.
          body[key] = raw === "" ? "" : normalizePhone(raw) || raw;
        } else {
          body[key] = raw;
        }
      }
    }
    if (Object.keys(body).length === 0) {
      toast.info("No changes to save");
      return;
    }
    setSaving(true);
    try {
      await api.patch("/auth/me/profile", body);
      if (emailChanging) {
        toast.success(
          "Email updated. Sign in again with your new email to continue.",
        );
      } else {
        toast.success("Profile updated");
      }
      await refresh();
    } catch (err) {
      if (
        err?.response?.status === 401 &&
        /re-auth/i.test(err.response?.data?.detail || "")
      ) {
        toast.error(
          "Email change requires a recent password confirmation. Re-enter your password from the sidebar and try again.",
        );
      } else {
        toast.error(formatApiError(err));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <section
      data-testid="profile-tab"
      className="space-y-6 animate-in fade-in duration-200"
    >
      <div
        data-testid="profile-summary-card"
        className="flex flex-wrap items-center gap-4 rounded-sm border border-border bg-card p-5"
      >
        <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-primary">
          <User2 className="h-7 w-7" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-display text-xl font-medium tracking-tight">
            {derivedPreview || "Your profile"}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>{user?.email}</span>
            {user?.role && (
              <span className="rounded-sm bg-muted px-1.5 py-0.5 uppercase tracking-wider">
                {user.role}
              </span>
            )}
            {form.job_title && <span>{form.job_title}</span>}
            {form.time_zone && <span>{form.time_zone}</span>}
          </div>
        </div>
      </div>

      <form
        onSubmit={save}
        className="rounded-sm border border-border bg-card p-6 space-y-6"
        data-testid="profile-form"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="font-display text-xl font-medium tracking-tight">
              Personal details
            </h3>
            <p className="mt-1 text-sm text-muted-foreground">
              These update your display name across the chart, audit trail,
              and your provider signature block.
            </p>
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <Field label="First name" testId="profile-first-name">
            <Input
              value={form.first_name}
              onChange={set("first_name")}
              maxLength={80}
              className="rounded-sm"
              data-testid="profile-first-name-input"
            />
          </Field>
          <Field label="Last name" testId="profile-last-name">
            <Input
              value={form.last_name}
              onChange={set("last_name")}
              maxLength={80}
              className="rounded-sm"
              data-testid="profile-last-name-input"
            />
          </Field>
          <Field
            label="Display name"
            testId="profile-display-name"
            hint="Optional. Overrides the auto-derived name in the UI."
          >
            <Input
              value={form.display_name}
              onChange={set("display_name")}
              maxLength={160}
              className="rounded-sm"
              data-testid="profile-display-name-input"
            />
          </Field>
          <Field
            label="Credentials suffix"
            testId="profile-credentials"
            hint="Letters that follow your name — e.g. DC, DACBR, ATC."
          >
            <Input
              value={form.credentials_suffix}
              onChange={set("credentials_suffix")}
              maxLength={40}
              className="rounded-sm"
              data-testid="profile-credentials-input"
            />
          </Field>
          <Field
            label="Preferred signature display name"
            testId="profile-signature"
            hint="Shown when signing clinical notes and addenda."
          >
            <Input
              value={form.preferred_signature_name}
              onChange={set("preferred_signature_name")}
              maxLength={160}
              className="rounded-sm"
              data-testid="profile-signature-input"
            />
          </Field>
          <Field label="Job title" testId="profile-job-title">
            <Input
              value={form.job_title}
              onChange={set("job_title")}
              maxLength={120}
              className="rounded-sm"
              data-testid="profile-job-title-input"
            />
          </Field>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <Field
            label="Email"
            testId="profile-email"
            hint={
              emailChanging
                ? "Email changes require a recent password confirmation; you'll be signed out on save."
                : "The email you sign in with."
            }
          >
            <Input
              type="email"
              value={form.email}
              onChange={set("email")}
              className="rounded-sm"
              data-testid="profile-email-input"
            />
          </Field>
          <Field label="Mobile phone" testId="profile-mobile">
            <PhoneInput
              value={form.mobile_phone}
              onChange={set("mobile_phone")}
              className="rounded-sm"
              data-testid="profile-mobile-input"
            />
          </Field>
          <Field label="Work phone" testId="profile-work-phone">
            <PhoneInput
              value={form.work_phone}
              onChange={set("work_phone")}
              className="rounded-sm"
              data-testid="profile-work-phone-input"
            />
          </Field>
          <Field
            label="Time zone"
            testId="profile-time-zone"
            hint="Used for scheduling and calendar display."
          >
            <Select
              value={form.time_zone || NO_TZ_VALUE}
              onValueChange={(v) =>
                set("time_zone")(v === NO_TZ_VALUE ? "" : v)
              }
            >
              <SelectTrigger
                data-testid="profile-time-zone-trigger"
                className="rounded-sm"
              >
                <SelectValue placeholder="Select a time zone…" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_TZ_VALUE}>
                  Use clinic default
                </SelectItem>
                {TIME_ZONES.map((tz) => (
                  <SelectItem key={tz.value} value={tz.value}>
                    {tz.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <p className="text-xs text-muted-foreground">
            Password, MFA, and sign-in history live in the{" "}
            <strong className="text-foreground">Security</strong> tab.
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={!dirty || saving}
              onClick={() => setForm(original)}
              data-testid="profile-reset-btn"
              className="rounded-sm"
            >
              Discard
            </Button>
            <Button
              type="submit"
              disabled={!dirty || saving}
              data-testid="profile-save-btn"
              className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            >
              {saving ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="mr-1.5 h-3.5 w-3.5" />
              )}
              Save changes
            </Button>
          </div>
        </div>
      </form>
    </section>
  );
}

function Field({ label, hint, testId, children }) {
  return (
    <div className="space-y-1" data-testid={testId}>
      <Label className="text-xs uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}
