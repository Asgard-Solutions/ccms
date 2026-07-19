/**
 * Resend / email settings — admin-only at /settings/email.
 *
 * Mirrors /settings/sms exactly: encrypted credentials, log-only
 * fallback when not enabled, test-send + recent outbound preview.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle, ExternalLink, Mail, Send, Trash2,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Switch } from "../../components/ui/switch";
import { Skeleton } from "../../components/ui/skeleton";
import {
  deleteEmailSettings, fetchEmailLog, fetchEmailSettings,
  saveEmailSettings, sendTestEmail,
} from "../../api/integrations";
import { formatDateTime } from "../../utils/time";

const RESEND_CONSOLE = "https://resend.com/api-keys";

export default function EmailSettings() {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    api_key: "",
    from_email: "",
    from_name: "",
    reply_to: "",
    enabled: false,
  });
  const [testForm, setTestForm] = useState({
    to: "", subject: "Test from CCMS", body: "Hello!",
  });
  const [testing, setTesting] = useState(false);
  const [log, setLog] = useState([]);

  const load = useCallback(async () => {
    try {
      const data = await fetchEmailSettings();
      setSettings(data);
      if (data.configured) {
        setForm({
          api_key: "",
          from_email: data.from_email || "",
          from_name: data.from_name || "",
          reply_to: data.reply_to || "",
          enabled: data.enabled,
        });
        try {
          setLog(await fetchEmailLog({ limit: 20 }));
        } catch (_) { /* perms */ }
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function save() {
    if (!form.api_key || !form.from_email) {
      toast.error("API key and From email are required.");
      return;
    }
    setSaving(true);
    try {
      const body = {
        api_key: form.api_key.trim(),
        from_email: form.from_email.trim(),
        from_name: form.from_name.trim() || null,
        reply_to: form.reply_to.trim() || null,
        enabled: form.enabled,
      };
      await saveEmailSettings(body);
      toast.success("Saved.");
      setShowForm(false);
      setForm((f) => ({ ...f, api_key: "" }));
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function removeSettings() {
    if (!window.confirm("Remove Resend credentials? Email will drop to log-only mode.")) return;
    try {
      await deleteEmailSettings();
      toast.success("Removed.");
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Delete failed");
    }
  }

  async function runTest() {
    if (!testForm.to) return;
    setTesting(true);
    try {
      const res = await sendTestEmail(testForm);
      toast.success(
        res.status === "sent" ? "Delivered." :
        res.status === "logged" ? "Logged only — Resend not yet enabled." :
        `Failed: ${res.error || "unknown"}`
      );
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Test failed");
    } finally {
      setTesting(false);
    }
  }

  if (loading) return <Skeleton className="h-64 w-full" />;

  return (
    <div data-testid="email-settings-page" className="space-y-6 max-w-3xl">
      <header>
        <h1 className="text-2xl font-display tracking-tight">
          <Mail className="inline mr-2 h-5 w-5 align-[-2px]" />
          Email (Resend)
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Configure the per-tenant Resend API key. Until enabled, every transactional
          email (statements, password resets, appointment reminders) is captured in the log
          below for inspection.
        </p>
      </header>

      <section
        data-testid="email-status-card"
        className="rounded-md border border-border bg-card p-5"
      >
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm text-muted-foreground">Status</p>
            <p className="text-base font-medium">
              {settings?.configured
                ? settings.enabled ? "Live — sending via Resend" : "Configured but disabled (log-only)"
                : "Not configured (log-only)"}
            </p>
            {settings?.from_email && (
              <p className="text-xs text-muted-foreground mt-1">
                From: <span className="font-medium">{settings.from_email}</span>
                {settings.api_key_last4 ? ` · API key ****${settings.api_key_last4}` : ""}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => setShowForm((x) => !x)} data-testid="email-edit-btn">
              {settings?.configured ? "Update" : "Add credentials"}
            </Button>
            {settings?.configured && (
              <Button size="sm" variant="outline" className="text-destructive" onClick={removeSettings} data-testid="email-remove-btn">
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        </div>
        {!settings?.configured && (
          <div className="mt-4 flex items-start gap-2 rounded-sm bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <p>
              Running in <b>log-only</b> mode. Outbound emails are persisted to
              <code> email_outbound_log</code> with <code>provider=log-only</code> until you save credentials and enable.
            </p>
          </div>
        )}
      </section>

      {showForm && (
        <section
          data-testid="email-credentials-form"
          className="rounded-md border border-border bg-card p-5 space-y-4"
        >
          <header className="flex items-center justify-between">
            <h2 className="font-medium">Resend credentials</h2>
            <a href={RESEND_CONSOLE} target="_blank" rel="noreferrer" className="text-xs text-primary inline-flex items-center gap-1">
              Resend dashboard <ExternalLink className="h-3 w-3" />
            </a>
          </header>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="md:col-span-2">
              <Label htmlFor="key">API key</Label>
              <Input
                id="key" type="password"
                data-testid="email-api-key-input"
                value={form.api_key}
                onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
                placeholder="re_..."
              />
            </div>
            <div>
              <Label htmlFor="from">From email</Label>
              <Input
                id="from"
                data-testid="email-from-email-input"
                value={form.from_email}
                onChange={(e) => setForm((f) => ({ ...f, from_email: e.target.value }))}
                placeholder="hello@yourclinic.com"
              />
            </div>
            <div>
              <Label htmlFor="fromn">From name</Label>
              <Input
                id="fromn"
                data-testid="email-from-name-input"
                value={form.from_name}
                onChange={(e) => setForm((f) => ({ ...f, from_name: e.target.value }))}
                placeholder="Riverbend Chiropractic"
              />
            </div>
            <div className="md:col-span-2">
              <Label htmlFor="reply">Reply-to (optional)</Label>
              <Input
                id="reply"
                data-testid="email-reply-to-input"
                value={form.reply_to}
                onChange={(e) => setForm((f) => ({ ...f, reply_to: e.target.value }))}
                placeholder="frontdesk@yourclinic.com"
              />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Switch
              id="enabled"
              data-testid="email-enabled-toggle"
              checked={form.enabled}
              onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: v }))}
            />
            <Label htmlFor="enabled" className="cursor-pointer">
              Enable live sending
            </Label>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setShowForm(false)} data-testid="email-cancel-btn">Cancel</Button>
            <Button onClick={save} disabled={saving} data-testid="email-save-btn">
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </section>
      )}

      <section
        data-testid="email-test-card"
        className="rounded-md border border-border bg-card p-5 space-y-3"
      >
        <h2 className="font-medium">Send a test email</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Input
            data-testid="email-test-to-input"
            value={testForm.to}
            onChange={(e) => setTestForm((f) => ({ ...f, to: e.target.value }))}
            placeholder="you@example.com"
          />
          <Input
            data-testid="email-test-subject-input"
            value={testForm.subject}
            onChange={(e) => setTestForm((f) => ({ ...f, subject: e.target.value }))}
          />
          <Input
            data-testid="email-test-body-input"
            value={testForm.body}
            onChange={(e) => setTestForm((f) => ({ ...f, body: e.target.value }))}
          />
        </div>
        <div className="flex justify-end">
          <Button onClick={runTest} disabled={testing || !testForm.to} data-testid="email-test-send-btn">
            <Send className="mr-1 h-3.5 w-3.5" />
            {testing ? "Sending…" : "Send test"}
          </Button>
        </div>
      </section>

      {log.length > 0 && (
        <section
          data-testid="email-outbound-log"
          className="rounded-md border border-border bg-card p-5"
        >
          <h2 className="font-medium mb-3">Recent outbound</h2>
          <ul className="divide-y divide-border/60 text-sm">
            {log.map((row) => (
              <li
                key={row.id}
                data-testid={`email-log-row-${row.id}`}
                className="py-2 flex items-center justify-between gap-4"
              >
                <div className="min-w-0">
                  <p className="truncate">
                    <span className="text-muted-foreground">{row.to}</span> · {row.subject}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {formatDateTime(row.created_at)} · {row.category}
                  </p>
                </div>
                <span className={`text-xs px-2 py-0.5 rounded-sm ${
                  row.status === "sent" ? "bg-green-100 text-green-800" :
                  row.status === "logged" ? "bg-amber-100 text-amber-800" :
                  "bg-red-100 text-red-800"
                }`}>{row.status}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
