import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ArrowUpRight, Cable, Lock, ShieldCheck } from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

const ROUTE_OPTIONS = [
  { value: "none",               label: "None — manual / portal" },
  { value: "change_healthcare",  label: "Change Healthcare" },
  { value: "optum",              label: "Optum" },
];

const MODE_OPTIONS = [
  { value: "edi",    label: "EDI (via clearinghouse)" },
  { value: "portal", label: "Payer portal (manual)" },
  { value: "paper",  label: "Paper / fax" },
];

const ENROLLMENT_OPTIONS = [
  { value: "not_started", label: "Not started",  tone: "muted" },
  { value: "in_progress", label: "In progress",  tone: "warning" },
  { value: "enrolled",    label: "Enrolled",     tone: "success" },
  { value: "suspended",   label: "Suspended",    tone: "destructive" },
];

function enrollmentTone(status) {
  return ENROLLMENT_OPTIONS.find((e) => e.value === status)?.tone || "muted";
}

function toneClass(tone) {
  switch (tone) {
    case "success":     return "bg-success-soft text-success border-success/40";
    case "warning":     return "bg-warning-soft text-warning border-warning/40";
    case "destructive": return "bg-destructive/10 text-destructive border-destructive/40";
    default:            return "bg-muted text-muted-foreground border-border";
  }
}

/**
 * Clearinghouse Settings — admin-only page.
 *
 * Shows the env-sourced adapter configuration (no secrets surfaced),
 * plus a table of every payer in the tenant with inline editors for
 * `clearinghouse_route` / `claim_submission_mode` / `enrollment_status`
 * / `trading_partner_id`. A per-payer edit dialog also upserts the
 * canonical `clearinghouse_enrollments` row so the payer setting and
 * the enrollment record stay consistent.
 */
export default function ClearinghouseSettingsPage() {
  const [adapters, setAdapters] = useState(null);
  const [payers, setPayers] = useState(null);
  const [enrollments, setEnrollments] = useState({});
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const [adapterResp, payerResp, enrollResp] = await Promise.all([
        api.get("/billing/clearinghouse/config"),
        api.get("/billing/payers"),
        api.get("/billing/clearinghouse/enrollments"),
      ]);
      setAdapters(adapterResp.data || []);
      setPayers(payerResp.data || []);
      const byPayer = {};
      for (const e of (enrollResp.data || [])) {
        byPayer[`${e.payer_id}:${e.clearinghouse}`] = e;
      }
      setEnrollments(byPayer);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to load clearinghouse settings");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function onSave() {
    if (!editing) return;
    setSaving(true);
    try {
      // 1. Update the payer record so the submission path sees the
      //    latest route / submission mode / trading partner id.
      await api.patch(`/billing/payers/${editing.payer_id}`, {
        clearinghouse_route: editing.clearinghouse_route,
        claim_submission_mode: editing.claim_submission_mode,
        enrollment_status: editing.enrollment_status,
        trading_partner_id: editing.trading_partner_id || null,
      });
      // 2. Upsert the canonical enrollment row when the route is a
      //    real clearinghouse. For route="none" we skip — there's no
      //    clearinghouse to enroll with.
      if (editing.clearinghouse_route !== "none") {
        await api.post("/billing/clearinghouse/enrollments", {
          payer_id: editing.payer_id,
          clearinghouse: editing.clearinghouse_route,
          status: editing.enrollment_status,
          submitter_id: editing.submitter_id || null,
          trading_partner_id: editing.trading_partner_id || null,
          notes: editing.notes || null,
        });
      }
      toast.success("Clearinghouse settings saved");
      setEditing(null);
      await load();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  const sortedPayers = useMemo(
    () => [...(payers || [])].sort((a, b) => (a.name || "").localeCompare(b.name || "")),
    [payers],
  );

  return (
    <div data-testid="clearinghouse-settings" className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Administration
          </div>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
            Clearinghouse
          </h1>
          <p className="mt-2 max-w-xl text-sm text-muted-foreground">
            Configure per-payer routing, enrollment progress, and submission
            mode. Credentials are read from server environment variables —
            they are never editable from the browser and never returned to
            the client.
          </p>
        </div>
      </header>

      {/* --- Adapter status ------------------------------------------------ */}
      <section
        data-testid="clearinghouse-adapters-section"
        className="rounded-sm border border-border bg-card p-6"
      >
        <div className="mb-4 flex items-center gap-2">
          <Cable className="h-4 w-4 text-primary" />
          <h2 className="font-display text-lg font-medium tracking-tight">
            Registered clearinghouses
          </h2>
        </div>
        {adapters === null ? (
          <Skeleton className="h-24 w-full" />
        ) : adapters.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No clearinghouses registered.
          </p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {adapters.map((a) => (
              <AdapterCard key={a.route_id} adapter={a} />
            ))}
          </div>
        )}
        <p className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
          <Lock className="h-3 w-3" />
          Credentials come from environment variables prefixed with the route's
          configuration namespace (e.g. <code className="font-mono">CLEARINGHOUSE_CHC_*</code>).
          The server never discloses the secret values.
        </p>
      </section>

      {/* --- Per-payer routing ---------------------------------------------- */}
      <section
        data-testid="clearinghouse-payers-section"
        className="rounded-sm border border-border bg-card"
      >
        <header className="flex items-center justify-between border-b border-border px-6 py-4">
          <h2 className="font-display text-lg font-medium tracking-tight">
            Payers
          </h2>
          <span className="text-xs text-muted-foreground">
            {sortedPayers.length} total
          </span>
        </header>
        {payers === null ? (
          <div className="space-y-2 p-6">
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : sortedPayers.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">
            No payers configured. Add payers from the{" "}
            <a href="/settings/payers" className="text-primary hover:underline">
              Payers page
            </a>.
          </p>
        ) : (
          <table className="w-full table-auto text-sm">
            <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-6 py-2">Payer</th>
                <th className="px-6 py-2">Route</th>
                <th className="px-6 py-2">Submission mode</th>
                <th className="px-6 py-2">Enrollment</th>
                <th className="px-6 py-2">Trading partner ID</th>
                <th className="px-6 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sortedPayers.map((p) => (
                <tr
                  key={p.id}
                  data-testid={`ch-payer-row-${p.id}`}
                  className="border-t border-border"
                >
                  <td className="px-6 py-3 font-medium">{p.name}</td>
                  <td className="px-6 py-3 text-muted-foreground">
                    {ROUTE_OPTIONS.find((o) => o.value === p.clearinghouse_route)?.label
                      || p.clearinghouse_route || "—"}
                  </td>
                  <td className="px-6 py-3 text-muted-foreground">
                    {MODE_OPTIONS.find((o) => o.value === p.claim_submission_mode)?.label
                      || p.claim_submission_mode || "—"}
                  </td>
                  <td className="px-6 py-3">
                    <span
                      data-testid={`ch-payer-enroll-${p.id}`}
                      className={`inline-flex items-center rounded-sm border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${toneClass(enrollmentTone(p.enrollment_status))}`}
                    >
                      {ENROLLMENT_OPTIONS.find((o) => o.value === p.enrollment_status)?.label
                        || p.enrollment_status}
                    </span>
                  </td>
                  <td className="px-6 py-3 text-muted-foreground font-mono text-xs">
                    {p.trading_partner_id || "—"}
                  </td>
                  <td className="px-6 py-3 text-right">
                    <Button
                      size="sm"
                      variant="outline"
                      className="rounded-sm"
                      data-testid={`ch-payer-edit-${p.id}`}
                      onClick={() => setEditing({
                        payer_id: p.id,
                        payer_name: p.name,
                        clearinghouse_route: p.clearinghouse_route || "none",
                        claim_submission_mode: p.claim_submission_mode || "portal",
                        enrollment_status: p.enrollment_status || "not_started",
                        trading_partner_id: p.trading_partner_id || "",
                        submitter_id: "",
                        notes: "",
                      })}
                    >
                      Configure
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* --- Edit dialog ---------------------------------------------------- */}
      <Dialog open={!!editing} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent data-testid="ch-edit-dialog" className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Configure {editing?.payer_name}</DialogTitle>
            <DialogDescription>
              These settings gate claim submission for this payer. An
              enrollment row is upserted automatically when a real
              clearinghouse route is selected.
            </DialogDescription>
          </DialogHeader>
          {editing && (
            <div className="space-y-4">
              <Field label="Clearinghouse route">
                <Select
                  value={editing.clearinghouse_route}
                  onValueChange={(v) => setEditing({ ...editing, clearinghouse_route: v })}
                >
                  <SelectTrigger data-testid="ch-edit-route">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROUTE_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Submission mode">
                <Select
                  value={editing.claim_submission_mode}
                  onValueChange={(v) => setEditing({ ...editing, claim_submission_mode: v })}
                >
                  <SelectTrigger data-testid="ch-edit-mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MODE_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Enrollment status">
                <Select
                  value={editing.enrollment_status}
                  onValueChange={(v) => setEditing({ ...editing, enrollment_status: v })}
                >
                  <SelectTrigger data-testid="ch-edit-enrollment">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ENROLLMENT_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Trading partner ID (optional)">
                <Input
                  data-testid="ch-edit-tpid"
                  value={editing.trading_partner_id}
                  onChange={(e) => setEditing({ ...editing, trading_partner_id: e.target.value })}
                  placeholder="e.g. TP-12345"
                />
              </Field>
              <Field label="Submitter ID (optional)">
                <Input
                  data-testid="ch-edit-submitter"
                  value={editing.submitter_id}
                  onChange={(e) => setEditing({ ...editing, submitter_id: e.target.value })}
                  placeholder="Clearinghouse submitter identifier"
                />
              </Field>
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditing(null)} disabled={saving}>
              Cancel
            </Button>
            <Button
              data-testid="ch-edit-save"
              onClick={onSave}
              disabled={saving}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function AdapterCard({ adapter }) {
  const modeTone = adapter.mode === "production"
    ? (adapter.has_client_id && adapter.has_client_secret ? "success" : "warning")
    : adapter.mode === "sandbox"
      ? "warning"
      : "muted";
  return (
    <div
      data-testid={`ch-adapter-${adapter.route_id}`}
      className="rounded-sm border border-border bg-background p-4"
    >
      <div className="flex items-center justify-between">
        <span className="font-display text-sm font-medium">
          {adapter.route_id === "change_healthcare"
            ? "Change Healthcare"
            : adapter.route_id === "optum" ? "Optum" : adapter.route_id}
        </span>
        <Badge className={toneClass(modeTone)}>{adapter.mode}</Badge>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Base URL</dt>
        <dd className="truncate font-mono">{adapter.base_url || "—"}</dd>
        <dt className="text-muted-foreground">Client ID</dt>
        <dd className="font-mono">
          {adapter.has_client_id
            ? (adapter.client_id_hint || "set")
            : <span className="text-destructive">missing</span>}
        </dd>
        <dt className="text-muted-foreground">Client secret</dt>
        <dd>{adapter.has_client_secret
          ? <span className="inline-flex items-center gap-1 text-success"><ShieldCheck className="h-3 w-3" /> set</span>
          : <span className="text-destructive">missing</span>}</dd>
        <dt className="text-muted-foreground">Capabilities</dt>
        <dd className="text-xs">
          {[
            adapter.supports_edi && "EDI",
            adapter.supports_era && "ERA",
            adapter.supports_eligibility && "270/271",
          ].filter(Boolean).join(" · ") || "—"}
        </dd>
      </dl>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <Label className="mb-1 block text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  );
}
