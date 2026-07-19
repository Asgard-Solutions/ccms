/**
 * Google OAuth admin settings — toggle on, manage allowed-domain
 * allowlist for auto-staff-provisioning.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Chrome, ShieldCheck, Trash2 } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Switch } from "../../components/ui/switch";
import { Skeleton } from "../../components/ui/skeleton";
import { fetchGoogleSettings, saveGoogleSettings } from "../../api/integrations";

const ROLES = ["staff", "doctor", "admin"];

export default function GoogleAuthSettings() {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [domainInput, setDomainInput] = useState("");

  const load = useCallback(async () => {
    try {
      setSettings(await fetchGoogleSettings());
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function persist(updates) {
    if (!settings) return;
    setSaving(true);
    try {
      const next = { ...settings, ...updates };
      await saveGoogleSettings({
        enabled: next.enabled,
        allowed_domains: next.allowed_domains || [],
        default_role: next.default_role || "staff",
      });
      setSettings(next);
      toast.success("Saved.");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  function addDomain() {
    const dom = domainInput.trim().toLowerCase().replace(/^@/, "");
    if (!dom || !dom.includes(".")) {
      toast.error("Enter a real email domain (e.g. yourclinic.com).");
      return;
    }
    if ((settings.allowed_domains || []).includes(dom)) {
      toast.info("Already on the list.");
      return;
    }
    persist({ allowed_domains: [...(settings.allowed_domains || []), dom].sort() });
    setDomainInput("");
  }

  function removeDomain(d) {
    persist({
      allowed_domains: (settings.allowed_domains || []).filter((x) => x !== d),
    });
  }

  if (loading) return <Skeleton className="h-64 w-full" />;

  return (
    <div data-testid="google-auth-settings-page" className="space-y-6 max-w-3xl">
      <header>
        <h1 className="text-2xl font-display tracking-tight">
          <Chrome className="inline mr-2 h-5 w-5 align-[-2px]" />
          Google sign-in
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Let staff sign in with their Google account (admin / doctor / staff only —
          patients always use SMS OTP).
        </p>
      </header>

      <section
        data-testid="google-auth-toggle-card"
        className="rounded-md border border-border bg-card p-5 flex items-center justify-between"
      >
        <div>
          <p className="font-medium flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-primary" />
            Google sign-in {settings.enabled ? "is enabled" : "is disabled"}
          </p>
          <p className="text-sm text-muted-foreground mt-1">
            When enabled, the "Sign in with Google" button appears on the staff login page.
          </p>
        </div>
        <Switch
          data-testid="google-auth-enabled-toggle"
          checked={!!settings.enabled}
          onCheckedChange={(v) => persist({ enabled: v })}
          disabled={saving}
        />
      </section>

      <section
        data-testid="google-auth-domains-card"
        className="rounded-md border border-border bg-card p-5 space-y-4"
      >
        <header>
          <h2 className="font-medium">Allowlisted email domains</h2>
          <p className="text-sm text-muted-foreground mt-1">
            New users from these domains will be auto-provisioned as
            <span className="mx-1 inline-flex items-center rounded-sm border border-border px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide">
              {settings.default_role || "staff"}
            </span>
            on their first Google sign-in. Existing users can always sign in regardless of domain.
          </p>
        </header>
        <div className="flex gap-2">
          <Input
            data-testid="google-domain-input"
            value={domainInput}
            onChange={(e) => setDomainInput(e.target.value)}
            placeholder="yourclinic.com"
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addDomain(); } }}
          />
          <Button
            onClick={addDomain}
            disabled={saving || !domainInput.trim()}
            data-testid="google-domain-add-btn"
          >
            Add
          </Button>
        </div>
        {(!settings.allowed_domains || settings.allowed_domains.length === 0) ? (
          <p className="text-sm text-muted-foreground">
            No domains yet. Without an allowlist, only invited users (already in the system)
            can sign in via Google.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {settings.allowed_domains.map((d) => (
              <li
                key={d}
                data-testid={`google-domain-row-${d}`}
                className="flex items-center justify-between rounded-sm border border-border/60 px-3 py-2 text-sm"
              >
                <span className="font-mono">@{d}</span>
                <Button
                  size="icon"
                  variant="ghost"
                  className="text-destructive"
                  onClick={() => removeDomain(d)}
                  data-testid={`google-domain-remove-${d}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}

        <div className="pt-2">
          <Label htmlFor="role">Default role for auto-provisioned users</Label>
          <select
            id="role"
            data-testid="google-default-role-select"
            value={settings.default_role || "staff"}
            onChange={(e) => persist({ default_role: e.target.value })}
            className="mt-1.5 rounded-sm border border-input bg-background px-3 py-2 text-sm"
          >
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
      </section>
    </div>
  );
}
