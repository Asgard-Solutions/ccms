/**
 * CreateUserDialog — guided 3-step flow for admins.
 *
 * Step 1: Profile (email, name, password, phone)
 * Step 2: Roles (checkboxes of available roles — system + custom)
 * Step 3: Review access (plain-English summary via
 *         POST /authz/roles/preview-effective-permissions on the
 *         aggregate permission key set)
 *
 * On submit:
 *   1. POST /auth/users (creates the user with legacy `role`)
 *   2. For each chosen role_key, POST /authz/users/{id}/roles
 *   3. Closes + refreshes parent
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, ArrowRight, CheckCircle2, ShieldCheck, UserPlus } from "lucide-react";
import { api } from "../../api/client";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Badge } from "../../components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

const STEPS = ["profile", "roles", "review"];

// Role keys that should be hidden from the primary picker (niche/internal).
const ADVANCED_ROLE_KEYS = new Set([
  "integration_account",
  "super_admin",
]);

function mapRoleKeyToLegacy(roleKey) {
  // New users get a legacy `role` string for back-compat with the
  // pre-RBAC deps layer. Best-match from the dominant role selected.
  const table = {
    super_admin: "admin",
    org_owner: "admin",
    clinic_manager: "admin",
    compliance_officer: "admin",
    auditor: "admin",
    provider: "doctor",
    clinical_staff: "staff",
    front_desk: "staff",
    billing_specialist: "staff",
    patient_portal: "patient",
    integration_account: "staff",
  };
  return table[roleKey] || "staff";
}

export default function CreateUserDialog({ open, onClose, onCreated, roles: rolesProp }) {
  const [step, setStep] = useState("profile");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [profile, setProfile] = useState({
    email: "", name: "", password: "", phone: "",
  });
  const [roleKeys, setRoleKeys] = useState([]);
  const [explanation, setExplanation] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  // Lazy-load roles only when the user advances to step 2, to avoid
  // a PIN step-up prompt firing the instant "Add user" is clicked.
  const [roles, setRoles] = useState(rolesProp || []);
  const [rolesLoaded, setRolesLoaded] = useState((rolesProp || []).length > 0);

  useEffect(() => {
    if (open) {
      setStep("profile");
      setProfile({ email: "", name: "", password: "", phone: "" });
      setRoleKeys([]);
      setExplanation(null);
      setShowAdvanced(false);
    }
  }, [open]);

  // When reaching review step, aggregate permission keys from selected
  // roles and POST to the preview endpoint for a plain-English summary.
  useEffect(() => {
    if (step !== "review" || roleKeys.length === 0) {
      setExplanation(null);
      return;
    }
    const keys = new Set();
    for (const rk of roleKeys) {
      const r = roles.find((x) => x.key === rk);
      if (!r) continue;
      for (const g of (r.grants || [])) keys.add(g.permission_key);
    }
    const list = Array.from(keys);
    api.post("/authz/roles/preview-effective-permissions",
             { permission_keys: list })
       .then((res) => setExplanation(res.data.explanation))
       .catch(() => setExplanation(null));
  }, [step, roleKeys, roles]);

  const commonRoles = useMemo(
    () => roles.filter((r) => !ADVANCED_ROLE_KEYS.has(r.key))
                .sort((a, b) => a.name.localeCompare(b.name)),
    [roles],
  );
  const advancedRoles = useMemo(
    () => roles.filter((r) => ADVANCED_ROLE_KEYS.has(r.key))
                .sort((a, b) => a.name.localeCompare(b.name)),
    [roles],
  );

  const profileValid = (
    profile.email.includes("@")
    && profile.name.trim().length > 0
    && profile.password.length >= 12
  );

  function next() {
    if (step === "profile" && profileValid) {
      setStep("roles");
      // Lazy-fetch roles on first advance into step 2.
      if (!rolesLoaded) {
        api.get("/authz/roles", { params: { include_user_counts: true } })
          .then((res) => { setRoles(res.data); setRolesLoaded(true); })
          .catch(() => { setRolesLoaded(true); });
      }
    } else if (step === "roles" && roleKeys.length > 0) {
      setStep("review");
    }
  }
  function back() {
    if (step === "roles") setStep("profile");
    else if (step === "review") setStep("roles");
  }

  async function submit() {
    setSubmitting(true);
    try {
      const primaryRole = roleKeys[0];
      const legacy = mapRoleKeyToLegacy(primaryRole);
      const created = await api.post("/auth/users", {
        email: profile.email.trim(),
        name: profile.name.trim(),
        password: profile.password,
        phone: profile.phone.trim() || null,
        role: legacy,
      });
      const newUserId = created.data.id;
      // Assign each role via authz. Silently ignore duplicates.
      for (const rk of roleKeys) {
        try {
          await api.post(`/authz/users/${newUserId}/roles`,
                         { role_key: rk });
        } catch {
          /* skip duplicates */
        }
      }
      toast.success("User created and access assigned");
      onCreated?.();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to create user");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        data-testid="create-user-dialog"
        className="max-w-2xl max-h-[92vh] overflow-y-auto rounded-sm"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            {step === "profile" && "Add a new user"}
            {step === "roles" && "Assign roles"}
            {step === "review" && "Review access"}
          </DialogTitle>
        </DialogHeader>

        <StepIndicator current={step} />

        {step === "profile" && (
          <div className="space-y-4 py-2">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Full name</Label>
                <Input
                  data-testid="create-user-name"
                  value={profile.name}
                  onChange={(e) => setProfile((p) => ({ ...p, name: e.target.value }))}
                  placeholder="Dr. Alex Rivera"
                  className="rounded-sm"
                />
              </div>
              <div className="space-y-1.5">
                <Label>Email</Label>
                <Input
                  data-testid="create-user-email"
                  type="email"
                  value={profile.email}
                  onChange={(e) => setProfile((p) => ({ ...p, email: e.target.value }))}
                  placeholder="alex@clinic.example"
                  className="rounded-sm"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Phone (optional)</Label>
                <Input
                  data-testid="create-user-phone"
                  value={profile.phone}
                  onChange={(e) => setProfile((p) => ({ ...p, phone: e.target.value }))}
                  placeholder="(555) 123-4567"
                  className="rounded-sm"
                />
              </div>
              <div className="space-y-1.5">
                <Label>Temporary password</Label>
                <Input
                  data-testid="create-user-password"
                  type="password"
                  value={profile.password}
                  onChange={(e) => setProfile((p) => ({ ...p, password: e.target.value }))}
                  placeholder="12+ characters"
                  className="rounded-sm"
                />
                <p className="text-[10px] text-muted-foreground">
                  The user will be required to change this on first sign-in.
                </p>
              </div>
            </div>
          </div>
        )}

        {step === "roles" && (
          <div className="space-y-4 py-2">
            <p className="text-sm text-muted-foreground">
              Pick one or more roles. You can always edit these later.
            </p>
            <div className="space-y-1">
              {commonRoles.map((r) => (
                <RoleRow
                  key={r.key}
                  role={r}
                  checked={roleKeys.includes(r.key)}
                  onToggle={(v) => setRoleKeys((prev) =>
                    v ? [...prev, r.key] : prev.filter((k) => k !== r.key),
                  )}
                />
              ))}
            </div>
            {advancedRoles.length > 0 && (
              <div>
                <button
                  type="button"
                  data-testid="create-user-toggle-advanced-roles"
                  onClick={() => setShowAdvanced((s) => !s)}
                  className="text-xs font-medium text-primary hover:underline"
                >
                  {showAdvanced ? "Hide" : "Show"} advanced / internal roles
                </button>
                {showAdvanced && (
                  <div className="mt-2 space-y-1 rounded-sm border border-dashed border-border p-2">
                    {advancedRoles.map((r) => (
                      <RoleRow
                        key={r.key}
                        role={r}
                        checked={roleKeys.includes(r.key)}
                        onToggle={(v) => setRoleKeys((prev) =>
                          v ? [...prev, r.key] : prev.filter((k) => k !== r.key),
                        )}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {step === "review" && (
          <div className="space-y-4 py-2">
            <div className="rounded-sm border border-border bg-muted/40 p-4">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                User profile
              </h3>
              <p className="mt-1 font-medium">{profile.name}</p>
              <p className="text-sm text-muted-foreground">{profile.email}</p>
            </div>
            <div className="rounded-sm border border-border bg-muted/40 p-4">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Assigned roles ({roleKeys.length})
              </h3>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {roleKeys.map((k) => {
                  const r = roles.find((x) => x.key === k);
                  return (
                    <Badge key={k} variant="outline" className="rounded-sm">
                      <ShieldCheck className="mr-1 h-3 w-3" />
                      {r?.name || k}
                    </Badge>
                  );
                })}
              </div>
            </div>
            <div
              data-testid="create-user-effective-summary"
              className="rounded-sm border border-primary/30 bg-primary/5 p-4"
            >
              <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-primary">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Effective access
              </h3>
              {explanation ? (
                <>
                  <p className="mt-2 text-sm">{explanation.summary}</p>
                  {explanation.sensitive_grants?.length > 0 && (
                    <div className="mt-3">
                      <p className="text-xs font-medium text-muted-foreground">
                        Sensitive permissions included:
                      </p>
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {explanation.sensitive_grants.map((s) => (
                          <Badge
                            key={s}
                            variant="outline"
                            className="rounded-sm text-[10px] text-amber-700 dark:text-amber-300"
                          >
                            {s}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <p className="mt-2 text-sm text-muted-foreground">Computing summary…</p>
              )}
            </div>
          </div>
        )}

        <DialogFooter className="flex-wrap gap-2 sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            onClick={step === "profile" ? onClose : back}
            className="rounded-sm"
            data-testid="create-user-back"
          >
            {step === "profile" ? "Cancel" : (<><ArrowLeft className="mr-1.5 h-4 w-4" />Back</>)}
          </Button>
          {step !== "review" ? (
            <Button
              type="button"
              disabled={step === "profile" ? !profileValid : roleKeys.length === 0}
              onClick={next}
              data-testid="create-user-next"
              className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            >
              Next
              <ArrowRight className="ml-1.5 h-4 w-4" />
            </Button>
          ) : (
            <Button
              type="button"
              disabled={submitting}
              onClick={submit}
              data-testid="create-user-submit"
              className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            >
              <UserPlus className="mr-1.5 h-4 w-4" />
              {submitting ? "Creating…" : "Create user"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function StepIndicator({ current }) {
  const idx = STEPS.indexOf(current);
  return (
    <div className="flex items-center gap-2 py-1">
      {STEPS.map((s, i) => (
        <div key={s} className="flex items-center gap-2">
          <div
            className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold
              ${i === idx
                ? "bg-primary text-primary-foreground"
                : i < idx
                ? "bg-primary/20 text-primary"
                : "bg-muted text-muted-foreground"}`}
          >
            {i + 1}
          </div>
          <span className={`text-xs ${i === idx ? "font-medium" : "text-muted-foreground"}`}>
            {s === "profile" && "Profile"}
            {s === "roles" && "Roles"}
            {s === "review" && "Review"}
          </span>
          {i < STEPS.length - 1 && (
            <span className="mx-1 h-px w-6 bg-border" />
          )}
        </div>
      ))}
    </div>
  );
}

function RoleRow({ role, checked, onToggle }) {
  const topPerms = (role.grants || []).slice(0, 3)
    .map((g) => g.permission_key.split(".")[0])
    .filter((v, i, a) => a.indexOf(v) === i);
  return (
    <label
      data-testid={`create-user-role-${role.key}`}
      className={`flex cursor-pointer items-start gap-3 rounded-sm border px-3 py-2.5 transition
        ${checked ? "border-primary bg-primary/5" : "border-border hover:bg-muted/50"}`}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onToggle(e.target.checked)}
        className="mt-1 h-4 w-4"
      />
      <div className="flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium">{role.name}</p>
          {role.is_system && (
            <Badge variant="outline" className="rounded-sm text-[10px] text-muted-foreground">
              built-in
            </Badge>
          )}
          {role.is_custom && !role.is_system && (
            <Badge variant="outline" className="rounded-sm text-[10px] text-primary">
              custom
            </Badge>
          )}
          {role.privileged && (
            <Badge variant="outline" className="rounded-sm text-[10px] text-amber-700 dark:text-amber-300">
              privileged
            </Badge>
          )}
        </div>
        {role.description && (
          <p className="mt-0.5 text-xs text-muted-foreground">{role.description}</p>
        )}
        {topPerms.length > 0 && (
          <p className="mt-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            Covers: {topPerms.join(" · ")} …
          </p>
        )}
      </div>
    </label>
  );
}
