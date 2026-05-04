/**
 * SOAP-template overrides — admin-only.
 *
 * Lets an admin tweak the system-prompt addendum used by the AI scribe
 * (and other AI surfaces) per scope:
 *   • tenant — applies clinic-wide
 *   • location — applies to one clinic site
 *   • provider — applies to one doctor
 *
 * Resolution order at runtime is provider → location → tenant: a
 * provider override stacks on top of the location and tenant defaults.
 * Empty / disabled rows fall through to the base system prompt baked
 * into `services/scribe/prompts.py::SCRIBE_SOAP_SYSTEM`.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Save, Trash2, Loader2 } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Textarea } from "../../components/ui/textarea";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../../components/ui/select";
import {
  listAITemplates, upsertAITemplate, deleteAITemplate,
} from "../../api/ai";
import { api } from "../../api/client";

const SURFACES = [
  { value: "scribe_soap", label: "Scribe — SOAP draft" },
  { value: "chart_brief", label: "Chart-prep brief" },
  { value: "prior_sections", label: "Encounter — prior sections" },
  { value: "draft_sections", label: "Encounter — Draft S+P" },
];

const SCOPES = [
  { value: "tenant", label: "Tenant (clinic-wide default)" },
  { value: "location", label: "Location" },
  { value: "provider", label: "Provider" },
];

export default function AITemplatesPage() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [locations, setLocations] = useState([]);
  const [providers, setProviders] = useState([]);
  const [draft, setDraft] = useState({
    scope_type: "tenant",
    scope_id: "",
    surface: "scribe_soap",
    instructions: "",
    enabled: true,
  });

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listAITemplates();
      setRows(res?.templates || []);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Load lookup tables once so we can render friendly names instead
  // of raw UUIDs in both the form picker and the list view.
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.get("/locations").then((r) => r.data).catch(() => []),
      api.get("/users", { params: { role: "doctor" } }).then((r) => r.data).catch(() => []),
    ]).then(([locs, docs]) => {
      if (cancelled) return;
      setLocations(Array.isArray(locs) ? locs : []);
      setProviders(Array.isArray(docs) ? docs : []);
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const labelForScope = useCallback((row) => {
    if (row.scope_type === "tenant") return "(tenant default)";
    if (row.scope_type === "location") {
      const l = locations.find((x) => x.id === row.scope_id);
      return l ? `${l.name}${l.code ? ` (${l.code})` : ""}` : row.scope_id;
    }
    if (row.scope_type === "provider") {
      const p = providers.find((x) => x.id === row.scope_id);
      return p ? (p.name || p.email || p.id) : row.scope_id;
    }
    return row.scope_id || "—";
  }, [locations, providers]);

  const sortedRows = useMemo(
    () => [...rows].sort((a, b) =>
      (a.scope_type + a.surface).localeCompare(b.scope_type + b.surface),
    ),
    [rows],
  );

  async function save() {
    if (draft.scope_type !== "tenant" && !draft.scope_id.trim()) {
      toast.error("Scope ID is required for location and provider scopes.");
      return;
    }
    setSaving(true);
    try {
      await upsertAITemplate({
        scope_type: draft.scope_type,
        scope_id: draft.scope_type === "tenant" ? null : draft.scope_id.trim(),
        surface: draft.surface,
        instructions: draft.instructions,
        enabled: draft.enabled,
      });
      toast.success("Template saved.");
      setDraft((d) => ({ ...d, instructions: "", scope_id: "" }));
      refresh();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function remove(row) {
    if (!window.confirm(`Delete the ${row.scope_type} template for ${row.surface}?`)) return;
    try {
      await deleteAITemplate({
        scope_type: row.scope_type,
        scope_id: row.scope_id,
        surface: row.surface,
      });
      toast.success("Deleted.");
      refresh();
    } catch {
      toast.error("Delete failed.");
    }
  }

  return (
    <div
      data-testid="ai-templates-page"
      className="mx-auto max-w-4xl space-y-6 p-6"
    >
      <header>
        <h1 className="text-2xl font-semibold">SOAP-template overrides</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Add clinic-, location- or provider-specific instructions that get
          appended to the AI scribe&apos;s system prompt at runtime. Provider
          rules win over location rules, which win over tenant defaults.
        </p>
      </header>

      {/* Editor */}
      <section
        data-testid="ai-templates-editor"
        className="rounded-md border border-border bg-card p-5 space-y-4"
      >
        <h2 className="text-sm font-medium">Add / update override</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="space-y-1">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Scope
            </span>
            <Select
              value={draft.scope_type}
              onValueChange={(v) => setDraft((d) => ({
                ...d, scope_type: v, scope_id: "",
              }))}
            >
              <SelectTrigger data-testid="ai-templates-scope-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SCOPES.map((s) => (
                  <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-1">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Surface
            </span>
            <Select
              value={draft.surface}
              onValueChange={(v) => setDraft((d) => ({ ...d, surface: v }))}
            >
              <SelectTrigger data-testid="ai-templates-surface-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SURFACES.map((s) => (
                  <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-1">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Scope ID {draft.scope_type === "tenant" && "(auto)"}
            </span>
            {draft.scope_type === "tenant" ? (
              <Input
                data-testid="ai-templates-scope-id-input"
                value=""
                disabled
                placeholder="—"
              />
            ) : (
              <Select
                value={draft.scope_id}
                onValueChange={(v) => setDraft((d) => ({ ...d, scope_id: v }))}
              >
                <SelectTrigger data-testid="ai-templates-scope-id-select">
                  <SelectValue placeholder={
                    draft.scope_type === "location"
                      ? "Select location…"
                      : "Select provider…"
                  } />
                </SelectTrigger>
                <SelectContent>
                  {(draft.scope_type === "location" ? locations : providers).map((opt) => {
                    const label = draft.scope_type === "location"
                      ? `${opt.name}${opt.code ? ` (${opt.code})` : ""}`
                      : `${(opt.name || opt.email || opt.id)}`;
                    return (
                      <SelectItem
                        key={opt.id}
                        value={opt.id}
                        data-testid={`ai-templates-scope-option-${opt.id}`}
                      >
                        {label}
                      </SelectItem>
                    );
                  })}
                  {(draft.scope_type === "location" ? locations : providers).length === 0 && (
                    <div className="px-2 py-1.5 text-xs text-muted-foreground">
                      None available
                    </div>
                  )}
                </SelectContent>
              </Select>
            )}
          </label>
        </div>
        <label className="block space-y-1">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Instructions (appended to system prompt)
          </span>
          <Textarea
            data-testid="ai-templates-instructions-input"
            rows={6}
            value={draft.instructions}
            onChange={(e) => setDraft((d) => ({ ...d, instructions: e.target.value }))}
            placeholder='e.g. "Always document spinal-region count in the Plan section. Use plain English in the Subjective and avoid abbreviations like &apos;c/o&apos;."'
            className="font-mono text-xs"
          />
        </label>
        <Button
          size="sm"
          onClick={save}
          disabled={saving || !draft.instructions.trim()}
          data-testid="ai-templates-save-btn"
          className="rounded-sm"
        >
          {saving ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Save className="mr-1.5 h-3.5 w-3.5" />
          )}
          Save override
        </Button>
      </section>

      {/* Existing rows */}
      <section
        data-testid="ai-templates-list"
        className="rounded-md border border-border bg-card p-5 space-y-3"
      >
        <h2 className="text-sm font-medium">Existing overrides</h2>
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : sortedRows.length === 0 ? (
          <p
            data-testid="ai-templates-empty"
            className="text-sm text-muted-foreground"
          >
            No overrides yet — the AI scribe is using the built-in system prompt for everyone.
          </p>
        ) : (
          <ul className="space-y-2">
            {sortedRows.map((r) => (
              <li
                key={`${r.scope_type}-${r.scope_id}-${r.surface}`}
                data-testid={`ai-templates-row-${r.scope_type}-${r.surface}`}
                className="rounded-sm border border-border/60 bg-muted/20 p-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="space-y-0.5">
                    <p className="text-sm font-medium">
                      {SCOPES.find((s) => s.value === r.scope_type)?.label || r.scope_type}
                      {" · "}
                      {SURFACES.find((s) => s.value === r.surface)?.label || r.surface}
                    </p>
                    <p className="font-mono text-[11px] text-muted-foreground">
                      {labelForScope(r)}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => remove(r)}
                    data-testid={`ai-templates-delete-${r.scope_type}-${r.surface}-btn`}
                    className="h-7 rounded-sm text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                <pre className="mt-2 whitespace-pre-wrap rounded-sm border border-border/40 bg-background/50 p-2 font-mono text-xs">
                  {r.instructions || "(empty)"}
                </pre>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
