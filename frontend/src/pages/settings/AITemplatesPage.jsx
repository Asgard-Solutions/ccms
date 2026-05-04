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
  // Locations are loaded eagerly (typically a small list); providers
  // are loaded on-demand with server-side search so tenants with
  // hundreds of doctors stay snappy.
  useEffect(() => {
    let cancelled = false;
    api.get("/authz/locations").then((r) => r.data).catch(() => [])
      .then((locs) => {
        if (cancelled) return;
        setLocations(Array.isArray(locs) ? locs : []);
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const [providerQuery, setProviderQuery] = useState("");
  const [providersLoading, setProvidersLoading] = useState(false);
  const [providerOverflow, setProviderOverflow] = useState(false);

  // Debounced server-side provider search. Triggered when the picker
  // is on the provider scope OR when we need to resolve labels for
  // already-saved overrides (rows). Capped at 200 rows per call.
  useEffect(() => {
    let cancelled = false;
    const needsProviders = (
      draft.scope_type === "provider"
      || rows.some((r) => r.scope_type === "provider")
    );
    if (!needsProviders) return undefined;
    setProvidersLoading(true);
    const handle = setTimeout(async () => {
      try {
        const params = { role: "doctor", limit: 200 };
        if (providerQuery.trim()) params.q = providerQuery.trim();
        const res = await api.get("/auth/users", { params }).then((r) => r.data);
        if (cancelled) return;
        const list = Array.isArray(res) ? res : [];
        setProviders(list);
        setProviderOverflow(list.length >= 200);
      } catch {
        if (!cancelled) setProviders([]);
      } finally {
        if (!cancelled) setProvidersLoading(false);
      }
    }, 250);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [draft.scope_type, providerQuery, rows]);

  const visibleScopeOptions = useMemo(() => {
    if (draft.scope_type === "location") return locations;
    if (draft.scope_type !== "provider") return [];
    // Providers are already server-filtered by `providerQuery`, so we
    // just hand the array through. Client-side fallback filter is
    // intentionally omitted to keep this list-of-truth single-source.
    return providers;
  }, [draft.scope_type, providers, locations]);

  const labelForScope = useCallback((row) => {
    if (row.scope_type === "tenant") return "(tenant default)";
    if (row.scope_type === "location") {
      const l = locations.find((x) => x.id === row.scope_id);
      return l ? `${l.name}${l.code ? ` (${l.code})` : ""}` : row.scope_id;
    }
    if (row.scope_type === "provider") {
      const p = providers.find((x) => x.id === row.scope_id);
      if (!p) return row.scope_id;
      const firstLast = [p.first_name, p.last_name].filter(Boolean).join(" ");
      const display = p.display_name
        || firstLast
        || (p.name && p.name !== "License doctor" ? p.name : null);
      return display ? `${display}${p.email ? ` · ${p.email}` : ""}` : (p.email || p.id);
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
              <div className="space-y-1.5">
                {draft.scope_type === "provider" && (
                  <Input
                    data-testid="ai-templates-provider-search"
                    value={providerQuery}
                    onChange={(e) => setProviderQuery(e.target.value)}
                    placeholder="Search by name or email…"
                    className="h-8 text-xs"
                  />
                )}
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
                  <SelectContent className="max-h-[280px]">
                    {visibleScopeOptions.slice(0, 100).map((opt) => {
                      const label = draft.scope_type === "location"
                        ? `${opt.name}${opt.code ? ` (${opt.code})` : ""}`
                        : (() => {
                            const firstLast = [opt.first_name, opt.last_name]
                              .filter(Boolean).join(" ");
                            const display = opt.display_name
                              || firstLast
                              || (opt.name && opt.name !== "License doctor" ? opt.name : null);
                            return display
                              ? `${display}${opt.email ? ` · ${opt.email}` : ""}`
                              : (opt.email || opt.id);
                          })();
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
                    {visibleScopeOptions.length === 0 && !providersLoading && (
                      <div className="px-2 py-1.5 text-xs text-muted-foreground">
                        No matches
                      </div>
                    )}
                    {providersLoading && draft.scope_type === "provider" && (
                      <div
                        data-testid="ai-templates-provider-loading"
                        className="px-2 py-1.5 text-xs text-muted-foreground"
                      >
                        Searching…
                      </div>
                    )}
                    {(providerOverflow || visibleScopeOptions.length > 100) && (
                      <div
                        data-testid="ai-templates-overflow-hint"
                        className="border-t border-border/40 px-2 py-1.5 text-[10px] uppercase tracking-wider text-muted-foreground"
                      >
                        {draft.scope_type === "provider"
                          ? "Showing up to 200 results — refine your search"
                          : `Showing first 100 of ${visibleScopeOptions.length} — refine search`}
                      </div>
                    )}
                  </SelectContent>
                </Select>
              </div>
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
