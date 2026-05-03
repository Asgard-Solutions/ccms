/**
 * SMS / Twilio settings — admin only.
 *
 * Parallels `/settings/payments`: credentials form + test connection,
 * log-only fallback banner when not enabled, and a small outbound log
 * preview at the bottom.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle, ExternalLink, MessageSquareText, Send, Trash2,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Switch } from "../../components/ui/switch";
import { Skeleton } from "../../components/ui/skeleton";
import {
  deleteSmsSettings, fetchOutboundLog, fetchSmsSettings,
  saveSmsSettings, sendTestSms,
} from "../../api/sms";
import { formatDateTime } from "../../utils/time";

const TWILIO_CONSOLE = "https://console.twilio.com/";

export default function SmsSettings() {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    account_sid: "",
    auth_token: "",
    messaging_service_sid: "",
    from_number: "",
    enabled: false,
  });
  const [testForm, setTestForm] = useState({ to: "", body: "Test from CCMS." });
  const [testing, setTesting] = useState(false);
  const [log, setLog] = useState([]);

  const load = useCallback(async () => {
    try {
      const data = await fetchSmsSettings();
      setSettings(data);
      if (data.configured) {
        setForm({
          account_sid: "",
          auth_token: "",
          messaging_service_sid: data.messaging_service_sid || "",
          from_number: data.from_number || "",
          enabled: data.enabled,
        });
        try {
          setLog(await fetchOutboundLog({ limit: 20 }));
        } catch (_) { /* perms */ }
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load SMS settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function save() {
    if (!form.account_sid || !form.auth_token) {
      toast.error("Account SID and Auth Token are required.");
      return;
    }
    if (!form.messaging_service_sid && !form.from_number) {
      toast.error("Enter either a Messaging Service SID or a from-number.");
      return;
    }
    setSaving(true);
    try {
      const body = {
        account_sid: form.account_sid.trim(),
        auth_token: form.auth_token.trim(),
        messaging_service_sid: form.messaging_service_sid.trim() || null,
        from_number: form.from_number.trim() || null,
        enabled: form.enabled,
      };
      await saveSmsSettings(body);
      toast.success("Saved.");
      setShowForm(false);
      setForm((f) => ({ ...f, account_sid: "", auth_token: "" }));
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function removeSettings() {
    if (!window.confirm("Remove Twilio credentials? SMS will drop to log-only mode.")) return;
    try {
      await deleteSmsSettings();
      toast.success("Credentials removed.");
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Delete failed");
    }
  }

  async function runTest() {
    if (!testForm.to) return;
    setTesting(true);
    try {
      const res = await sendTestSms({
        to: testForm.to.trim(),
        body: testForm.body.trim(),
      });
      toast.success(
        res.status === "sent" ? "Delivered." :
        res.status === "logged" ? "Logged only — Twilio not yet enabled." :
        `Failed: ${res.error || "unknown"}`
      );
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Test send failed");
    } finally {
      setTesting(false);
    }
  }

  if (loading) return <Skeleton className="h-64 w-full" />;

  return (
    <div data-testid="sms-settings-page" className="space-y-6 max-w-3xl">
      <header>
        <h1 className="text-2xl font-display tracking-tight">
          <MessageSquareText className="inline mr-2 h-5 w-5 align-[-2px]" />
          Two-way texting (Twilio)
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Paste your Twilio credentials to enable real SMS delivery. Until then, every outbound
          message is captured in the log below for inspection.
        </p>
      </header>

      {/* Status card */}
      <section
        data-testid="sms-status-card"
        className="rounded-md border border-border bg-card p-5"
      >
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm text-muted-foreground">Status</p>
            <p className="text-base font-medium">
              {settings?.configured
                ? settings.enabled
                  ? "Live — sending via Twilio"
                  : "Configured, but disabled (log-only)"
                : "Not configured (log-only)"}
            </p>
            {settings?.account_sid_last4 && (
              <p className="text-xs text-muted-foreground mt-1">
                Account SID · ****{settings.account_sid_last4}
                {settings.from_number ? ` · from ${settings.from_number}` : ""}
                {settings.messaging_service_sid ? ` · messaging service ${settings.messaging_service_sid}` : ""}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setShowForm((x) => !x)}
              data-testid="sms-edit-btn"
            >
              {settings?.configured ? "Update credentials" : "Add credentials"}
            </Button>
            {settings?.configured && (
              <Button
                size="sm"
                variant="outline"
                className="text-destructive"
                onClick={removeSettings}
                data-testid="sms-remove-btn"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        </div>

        {!settings?.configured && (
          <div className="mt-4 flex items-start gap-2 rounded-sm bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <p>
              Running in <b>log-only</b> mode. All OTPs, booking confirmations and questionnaire
              invitations still render and are logged, but nothing is delivered until Twilio
              credentials are saved and <b>enabled</b>.
            </p>
          </div>
        )}
      </section>

      {/* Credentials form */}
      {showForm && (
        <section
          data-testid="sms-credentials-form"
          className="rounded-md border border-border bg-card p-5 space-y-4"
        >
          <header className="flex items-center justify-between">
            <h2 className="font-medium">Twilio credentials</h2>
            <a
              href={TWILIO_CONSOLE}
              target="_blank" rel="noreferrer"
              className="text-xs text-primary inline-flex items-center gap-1"
            >
              Twilio Console <ExternalLink className="h-3 w-3" />
            </a>
          </header>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="sid">Account SID</Label>
              <Input
                id="sid"
                data-testid="sms-account-sid-input"
                value={form.account_sid}
                onChange={(e) => setForm((f) => ({ ...f, account_sid: e.target.value }))}
                placeholder="AC…"
              />
            </div>
            <div>
              <Label htmlFor="tok">Auth Token</Label>
              <Input
                id="tok"
                data-testid="sms-auth-token-input"
                type="password"
                value={form.auth_token}
                onChange={(e) => setForm((f) => ({ ...f, auth_token: e.target.value }))}
              />
            </div>
            <div>
              <Label htmlFor="msid">Messaging Service SID</Label>
              <Input
                id="msid"
                data-testid="sms-msid-input"
                value={form.messaging_service_sid}
                onChange={(e) => setForm((f) => ({ ...f, messaging_service_sid: e.target.value }))}
                placeholder="MG… (recommended)"
              />
            </div>
            <div>
              <Label htmlFor="fn">Or from-number</Label>
              <Input
                id="fn"
                data-testid="sms-from-number-input"
                value={form.from_number}
                onChange={(e) => setForm((f) => ({ ...f, from_number: e.target.value }))}
                placeholder="+15555551212"
              />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Switch
              id="enabled"
              data-testid="sms-enabled-toggle"
              checked={form.enabled}
              onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: v }))}
            />
            <Label htmlFor="enabled" className="cursor-pointer">
              Enable live sending (untoggle to keep testing in log-only mode)
            </Label>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              onClick={() => setShowForm(false)}
              data-testid="sms-cancel-btn"
            >
              Cancel
            </Button>
            <Button
              onClick={save}
              disabled={saving}
              data-testid="sms-save-btn"
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </section>
      )}

      {/* Test send */}
      <section
        data-testid="sms-test-card"
        className="rounded-md border border-border bg-card p-5 space-y-3"
      >
        <h2 className="font-medium">Send a test message</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Input
            data-testid="sms-test-to-input"
            value={testForm.to}
            onChange={(e) => setTestForm((f) => ({ ...f, to: e.target.value }))}
            placeholder="+15555551212 or 5035550210"
          />
          <Input
            data-testid="sms-test-body-input"
            value={testForm.body}
            onChange={(e) => setTestForm((f) => ({ ...f, body: e.target.value }))}
          />
        </div>
        <div className="flex justify-end">
          <Button
            onClick={runTest}
            disabled={testing || !testForm.to}
            data-testid="sms-test-send-btn"
          >
            <Send className="mr-1 h-3.5 w-3.5" />
            {testing ? "Sending…" : "Send test"}
          </Button>
        </div>
      </section>

      {/* Outbound log */}
      {log.length > 0 && (
        <section
          data-testid="sms-outbound-log"
          className="rounded-md border border-border bg-card p-5"
        >
          <h2 className="font-medium mb-3">Recent outbound</h2>
          <ul className="divide-y divide-border/60 text-sm">
            {log.map((row) => (
              <li
                key={row.id}
                data-testid={`sms-log-row-${row.id}`}
                className="py-2 flex items-center justify-between gap-4"
              >
                <div className="min-w-0">
                  <p className="truncate">
                    <span className="text-muted-foreground">{row.to}</span> · {row.body}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {formatDateTime(row.created_at)} · {row.category}
                  </p>
                </div>
                <span className={`text-xs px-2 py-0.5 rounded-sm ${
                  row.status === "sent" ? "bg-green-100 text-green-800" :
                  row.status === "logged" ? "bg-amber-100 text-amber-800" :
                  "bg-red-100 text-red-800"
                }`}>
                  {row.status}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
