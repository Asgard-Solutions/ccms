import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle, CreditCard, ExternalLink, Lock, ShieldCheck, Trash2, Webhook,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Skeleton } from "../../components/ui/skeleton";
import { Switch } from "../../components/ui/switch";
import { useAuth } from "../../contexts/AuthContext";
import {
  fetchHelcimSettings,
  saveHelcimSettings,
  deleteHelcimSettings,
  testHelcimConnection,
  fetchHelcimWebhookLog,
  fetchStatementAutopaySettings,
  saveStatementAutopaySettings,
} from "../billing/helcim/api";
import { formatDateTime } from "../../utils/time";

const HELCIM_DASHBOARD_URL = "https://app.helcim.com/account/api-access";
const HELCIM_WEBHOOK_DOCS = "https://devdocs.helcim.com/docs/webhooks";

export default function PaymentsSettings() {
  const { user } = useAuth();
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    api_token: "", account_id: "", webhook_verifier_token: "", test_mode: true,
  });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [webhookLog, setWebhookLog] = useState([]);
  const [autopay, setAutopay] = useState({ enabled: false, notes: "" });
  const [autopaySaving, setAutopaySaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await fetchHelcimSettings();
      setSettings(data);
      if (data.configured) {
        try {
          setWebhookLog(await fetchHelcimWebhookLog());
        } catch (_) { /* perms */ }
        try {
          const ap = await fetchStatementAutopaySettings();
          setAutopay({
            enabled: !!ap.enabled,
            notes: ap.notes || "",
          });
        } catch (_) { /* perms or unconfigured */ }
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to load Helcim settings.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onSave = async () => {
    if (!form.api_token || !form.account_id) {
      toast.error("API token and Account ID are required.");
      return;
    }
    setSaving(true);
    try {
      const body = { ...form };
      if (!body.webhook_verifier_token) delete body.webhook_verifier_token;
      const updated = await saveHelcimSettings(body);
      setSettings(updated);
      setShowForm(false);
      setForm({ api_token: "", account_id: "", webhook_verifier_token: "", test_mode: true });
      toast.success("Helcim credentials saved.");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to save credentials.");
    } finally {
      setSaving(false);
    }
  };

  const onTest = async () => {
    setTesting(true);
    try {
      await testHelcimConnection();
      toast.success("Helcim connection OK.");
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Connection test failed.");
      load();
    } finally {
      setTesting(false);
    }
  };

  const onDelete = async () => {
    if (!confirm("Remove Helcim credentials? Card payments will stop working until new credentials are entered.")) return;
    try {
      await deleteHelcimSettings();
      toast.success("Helcim credentials removed.");
      setSettings({ configured: false });
      setWebhookLog([]);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to delete credentials.");
    }
  };

  const onSaveAutopay = async (next) => {
    setAutopaySaving(true);
    try {
      const saved = await saveStatementAutopaySettings({
        enabled: next.enabled, notes: next.notes || null,
      });
      setAutopay({ enabled: !!saved.enabled, notes: saved.notes || "" });
      toast.success(`Statement auto-pay ${saved.enabled ? "enabled" : "disabled"}.`);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to update statement auto-pay.");
    } finally {
      setAutopaySaving(false);
    }
  };

  const webhookEndpoint = settings?.tenant_id
    ? `${process.env.REACT_APP_BACKEND_URL}/api/billing/helcim/webhook/${settings.tenant_id}`
    : "";

  return (
    <div data-testid="payments-settings-page" className="space-y-8 animate-in fade-in duration-300">
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Settings · Payments
        </span>
        <h1 className="mt-2 font-display text-4xl font-medium tracking-tight">
          Payment processor — Helcim
        </h1>
        <p className="mt-3 max-w-3xl text-sm text-muted-foreground">
          Each clinic uses its own Helcim merchant account. The API token is encrypted
          at rest and never displayed in full after entry — only the last 4 characters
          are shown for verification.
        </p>
      </header>

      {loading ? (
        <Skeleton className="h-48" />
      ) : !settings?.configured ? (
        <UnconfiguredCard
          form={form} setForm={setForm}
          showForm={showForm} setShowForm={setShowForm}
          onSave={onSave} saving={saving}
        />
      ) : (
        <ConfiguredCard
          settings={settings}
          onTest={onTest} testing={testing}
          onDelete={onDelete}
          showForm={showForm} setShowForm={setShowForm}
          form={form} setForm={setForm}
          onSave={onSave} saving={saving}
        />
      )}

      {settings?.configured && (
        <section data-testid="webhook-section" className="space-y-3">
          <h2 className="font-display text-lg font-medium">Webhook endpoint</h2>
          <div className="rounded-sm border border-border bg-card p-4 space-y-3">
            <div className="flex items-start gap-2 text-xs text-muted-foreground">
              <Webhook className="mt-0.5 h-4 w-4 flex-none text-primary" />
              <span>
                Paste this URL into your Helcim dashboard under Settings → Webhooks. Helcim signs every
                event with the verifier token above; we reject any payload whose HMAC-SHA256 signature
                does not match (and replay-window check is 5 minutes).
              </span>
            </div>
            <div className="flex items-center gap-2 rounded-sm bg-muted px-3 py-2">
              <code data-testid="webhook-endpoint-url" className="flex-1 break-all font-mono text-xs text-foreground">
                {webhookEndpoint}
              </code>
              <Button
                data-testid="webhook-copy-btn"
                size="sm"
                variant="outline"
                onClick={() => {
                  navigator.clipboard.writeText(webhookEndpoint);
                  toast.success("Webhook URL copied.");
                }}
              >
                Copy
              </Button>
            </div>
            <a
              href={HELCIM_WEBHOOK_DOCS}
              target="_blank" rel="noreferrer"
              data-testid="webhook-docs-link"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
            >
              Helcim webhook docs <ExternalLink className="h-3 w-3" />
            </a>
          </div>

          {webhookLog.length > 0 && (
            <div className="overflow-x-auto rounded-sm border border-border bg-card">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead className="border-b border-border text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3 font-medium">Received</th>
                    <th className="px-4 py-3 font-medium">Event</th>
                    <th className="px-4 py-3 font-medium">Transaction</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {webhookLog.slice(0, 20).map((w) => (
                    <tr key={w.id} data-testid={`webhook-log-row-${w.id}`} className="border-b border-border last:border-0">
                      <td className="px-4 py-3 text-xs text-muted-foreground">{formatDateTime(w.received_at)}</td>
                      <td className="px-4 py-3 text-xs"><code className="font-mono text-[11px] text-foreground">{w.event_type}</code></td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">{w.transaction_id || "—"}</td>
                      <td className="px-4 py-3 text-xs">
                        <span className="inline-flex rounded-sm bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-primary">
                          {w.processed ? "processed" : "queued"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {settings?.configured && (
        <section data-testid="statement-autopay-section" className="space-y-3">
          <h2 className="font-display text-lg font-medium">Statement auto-pay</h2>
          <div className="rounded-sm border border-border bg-card p-4 space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div className="space-y-1">
                <p className="text-sm text-foreground">
                  Auto-charge open balances when patient statements are generated.
                </p>
                <p className="text-xs text-muted-foreground">
                  Each statement triggers a one-shot Helcim charge against the patient&apos;s saved
                  card after a 3-day grace window. Patients must opt in individually from their
                  ledger page, and a saved card on file is required. Failed charges follow the
                  standard 1d/3d/7d retry cadence and surface on the Dashboard.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  data-testid="statement-autopay-toggle"
                  checked={autopay.enabled}
                  disabled={autopaySaving}
                  onCheckedChange={(v) => onSaveAutopay({ ...autopay, enabled: !!v })}
                />
                <Label className="cursor-pointer text-sm">
                  {autopay.enabled ? "Enabled" : "Disabled"}
                </Label>
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

function UnconfiguredCard({ form, setForm, showForm, setShowForm, onSave, saving }) {
  return (
    <section data-testid="helcim-unconfigured" className="rounded-sm border border-border bg-card p-6 space-y-4">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 flex-none text-warning" />
        <div className="space-y-1">
          <h2 className="font-display text-lg">Helcim is not configured for this clinic.</h2>
          <p className="text-sm text-muted-foreground">
            Card payments via the staff checkout and patient portal will be unavailable until you
            enter your Helcim API token and Account ID below.
          </p>
        </div>
      </div>

      <a
        href={HELCIM_DASHBOARD_URL}
        target="_blank" rel="noreferrer"
        data-testid="helcim-dashboard-link"
        className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
      >
        Get your API token from the Helcim dashboard <ExternalLink className="h-3 w-3" />
      </a>

      {!showForm ? (
        <Button data-testid="helcim-configure-btn" onClick={() => setShowForm(true)} className="gap-2">
          <CreditCard className="h-4 w-4" /> Configure Helcim
        </Button>
      ) : (
        <CredentialsForm form={form} setForm={setForm} onSave={onSave} saving={saving}
                         onCancel={() => setShowForm(false)} />
      )}
    </section>
  );
}

function ConfiguredCard({
  settings, onTest, testing, onDelete,
  showForm, setShowForm, form, setForm, onSave, saving,
}) {
  return (
    <section data-testid="helcim-configured" className="space-y-4">
      <div className="rounded-sm border border-primary/30 bg-primary/5 p-6 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-primary" />
              <h2 className="font-display text-lg">Helcim is configured</h2>
              {settings.test_mode && (
                <span data-testid="helcim-test-mode-badge" className="rounded-sm bg-warning-soft px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-warning">
                  Test mode
                </span>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              API token ends in <code data-testid="helcim-token-last4" className="font-mono">****{settings.api_token_last4}</code>
              {" · "}Account <code data-testid="helcim-account-id" className="font-mono">{settings.account_id}</code>
              {" · "}Updated {settings.updated_at ? formatDateTime(settings.updated_at) : "—"} by {settings.updated_by || "—"}
            </p>
            {settings.last_tested_at && (
              <p data-testid="helcim-last-test" className="text-xs text-muted-foreground">
                Last connection test: {formatDateTime(settings.last_tested_at)} — {settings.last_test_outcome}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button data-testid="helcim-test-btn" size="sm" variant="outline" onClick={onTest} disabled={testing}>
              {testing ? "Testing…" : "Test connection"}
            </Button>
            <Button data-testid="helcim-edit-btn" size="sm" variant="outline" onClick={() => setShowForm(!showForm)}>
              {showForm ? "Cancel" : "Update credentials"}
            </Button>
            <Button data-testid="helcim-delete-btn" size="sm" variant="outline" onClick={onDelete} className="text-destructive">
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>

        {showForm && (
          <div className="border-t border-border pt-4">
            <CredentialsForm form={form} setForm={setForm} onSave={onSave} saving={saving}
                             onCancel={() => setShowForm(false)} />
          </div>
        )}
      </div>
    </section>
  );
}

function CredentialsForm({ form, setForm, onSave, saving, onCancel }) {
  return (
    <div data-testid="helcim-credentials-form" className="space-y-4">
      <div>
        <Label htmlFor="hc-api-token" className="flex items-center gap-1">
          <Lock className="h-3 w-3" /> Helcim API token
        </Label>
        <Input
          id="hc-api-token"
          data-testid="helcim-form-api-token"
          type="password"
          value={form.api_token}
          onChange={(e) => setForm({ ...form, api_token: e.target.value })}
          placeholder="Generated in Helcim → Settings → API Access"
          autoComplete="off"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Stored encrypted (AES-256) — only the last 4 characters are displayed back.
        </p>
      </div>
      <div>
        <Label htmlFor="hc-account-id">Account ID</Label>
        <Input
          id="hc-account-id"
          data-testid="helcim-form-account-id"
          value={form.account_id}
          onChange={(e) => setForm({ ...form, account_id: e.target.value })}
          placeholder="Your Helcim merchant account ID"
        />
      </div>
      <div>
        <Label htmlFor="hc-verifier" className="flex items-center gap-1">
          <Webhook className="h-3 w-3" /> Webhook verifier token
        </Label>
        <Input
          id="hc-verifier"
          data-testid="helcim-form-verifier"
          type="password"
          value={form.webhook_verifier_token}
          onChange={(e) => setForm({ ...form, webhook_verifier_token: e.target.value })}
          placeholder="Optional — paste from Helcim webhook setup"
          autoComplete="off"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Required for verified webhook delivery. Without this we can&apos;t accept Helcim webhooks.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Switch
          id="hc-test-mode"
          data-testid="helcim-form-test-mode"
          checked={form.test_mode}
          onCheckedChange={(v) => setForm({ ...form, test_mode: v })}
        />
        <Label htmlFor="hc-test-mode" className="cursor-pointer">Test mode</Label>
      </div>
      <div className="flex justify-end gap-2">
        <Button data-testid="helcim-form-cancel" variant="outline" onClick={onCancel}>Cancel</Button>
        <Button data-testid="helcim-form-save" onClick={onSave} disabled={saving}>
          {saving ? "Saving…" : "Save credentials"}
        </Button>
      </div>
    </div>
  );
}
