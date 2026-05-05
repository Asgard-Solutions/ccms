/**
 * Settings → AI models picker.
 *
 * Lets a tenant admin pick which Anthropic model powers each AI
 * surface (SOAP draft, coding suggestions, semantic search, NL
 * scheduling, etc.). Per-tenant override is stored on
 * `ai_settings.surface_models`; runtime resolution falls back to
 * `ai_settings.model_name` and then the env default.
 *
 * Designed to be the daily-driver way to balance cost vs. quality
 * — e.g., Opus for SOAP drafts (high stakes, doctor-facing) and
 * Haiku for ranking + parsing (high volume, latency-sensitive).
 */
import { useEffect, useMemo, useState } from "react";
import { Brain, Check, Info, Loader2, RefreshCw, Save, Sparkles, Zap } from "lucide-react";
import { toast } from "sonner";

import { api } from "../../api/client";
import { Button } from "../../components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../../components/ui/select";

const TIER_BADGE = {
  premium:  { className: "bg-violet-500/15 text-violet-700 dark:text-violet-300", icon: Sparkles },
  balanced: { className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300", icon: Brain },
  fast:     { className: "bg-amber-500/15 text-amber-700 dark:text-amber-300", icon: Zap },
};

function modelLabel(model) {
  return model?.label || model?.alias || model?.id || "—";
}

function priceCellsPerMtok(model) {
  if (!model) return "—";
  return (
    `$${model.input_per_mtok_usd.toFixed(2)} in / `
    + `$${model.output_per_mtok_usd.toFixed(2)} out per 1M tok`
  );
}

export default function AIModelsPage() {
  const [meta, setMeta] = useState(null);
  const [draft, setDraft] = useState(null);  // { tenant default + surface_models map }
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    try {
      const { data } = await api.get("/ai/settings");
      setMeta(data);
      setDraft({
        model_provider: data.model_provider,
        model_name: data.model_name,
        enabled: data.enabled !== false,
        surface_models: { ...(data.surface_models || {}) },
      });
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load AI settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  async function save() {
    if (!draft) return;
    setSaving(true);
    try {
      await api.put("/ai/settings", draft);
      toast.success("AI model selection saved");
      await refresh();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      if (detail?.code === "UNKNOWN_MODEL") {
        toast.error(`${detail.model} is not an allowed model for ${detail.surface}`);
      } else {
        toast.error(typeof detail === "string" ? detail : "Save failed");
      }
    } finally {
      setSaving(false);
    }
  }

  function setTenantDefault(model_name) {
    setDraft((d) => ({ ...d, model_name }));
  }

  function setSurfaceModel(surfaceKey, model_id) {
    setDraft((d) => {
      const next = { ...d.surface_models };
      // Empty string means "use tenant default" — store as undefined.
      if (!model_id) delete next[surfaceKey];
      else next[surfaceKey] = model_id;
      return { ...d, surface_models: next };
    });
  }

  function applyAllRecommendations() {
    if (!meta) return;
    setDraft((d) => {
      const next = { ...(d.surface_models || {}) };
      for (const surface of meta.surfaces) {
        if (surface.recommended_model) next[surface.key] = surface.recommended_model;
      }
      return { ...d, surface_models: next };
    });
    toast("Recommendations applied — click Save to commit");
  }

  const modelById = useMemo(() => {
    const m = {};
    (meta?.available_models || []).forEach((x) => { m[x.id] = x; });
    return m;
  }, [meta]);

  if (loading || !meta || !draft) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading AI settings…
      </div>
    );
  }

  return (
    <div data-testid="ai-models-page" className="mx-auto max-w-5xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="font-display text-3xl font-medium tracking-tight">
          AI models
        </h1>
        <p className="text-sm text-muted-foreground">
          Pick the Claude model that powers each AI surface. Use Opus where doctors read every word,
          Haiku where the call fires hundreds of times a day. Per-surface overrides win over the
          tenant default; an unset row falls back to the default below.
        </p>
      </header>

      {/* Tenant default */}
      <section className="rounded-sm border border-border bg-card p-5">
        <header className="mb-3 flex items-baseline justify-between gap-2">
          <div>
            <h2 className="text-lg font-medium">Tenant default model</h2>
            <p className="text-xs text-muted-foreground">
              Used when no per-surface override is set. We recommend Sonnet 4.5 — the cost / quality sweet spot.
            </p>
          </div>
          <Button
            variant="outline" size="sm"
            onClick={applyAllRecommendations}
            data-testid="ai-models-apply-recommendations-btn"
          >
            <Sparkles className="mr-1.5 h-3.5 w-3.5" /> Apply recommended per surface
          </Button>
        </header>
        <Select
          value={draft.model_name}
          onValueChange={setTenantDefault}
        >
          <SelectTrigger
            data-testid="ai-models-tenant-default-select"
            className="max-w-sm"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {meta.available_models.map((m) => (
              <SelectItem
                key={m.id}
                value={m.id}
                data-testid={`ai-models-tenant-default-option-${m.alias}`}
              >
                <ModelRow model={m} />
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </section>

      {/* Per-surface overrides */}
      <section className="rounded-sm border border-border bg-card p-5">
        <header className="mb-4">
          <h2 className="text-lg font-medium">Per-surface overrides</h2>
          <p className="text-xs text-muted-foreground">
            Empty = use tenant default ({modelLabel(modelById[draft.model_name])}).
          </p>
        </header>

        <ul className="divide-y divide-border" data-testid="ai-models-surface-list">
          {meta.surfaces.map((surface) => {
            const current = draft.surface_models[surface.key] || "";
            const recommended = modelById[surface.recommended_model];
            const recommendedTone = (
              recommended && current === recommended.id
                ? "ring-1 ring-emerald-500/40"
                : ""
            );
            return (
              <li
                key={surface.key}
                data-testid={`ai-models-surface-${surface.key}`}
                className={`grid grid-cols-1 gap-2 py-3 sm:grid-cols-[1fr_18rem] sm:gap-4 ${recommendedTone}`}
              >
                <div className="space-y-1">
                  <div className="flex items-baseline gap-2">
                    <h3 className="font-medium">{surface.label}</h3>
                    {recommended && (
                      <span
                        title={`Recommended: ${modelLabel(recommended)}`}
                        className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground"
                      >
                        rec: {recommended.alias}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">{surface.intent}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Select
                    value={current || "__default__"}
                    onValueChange={(v) => setSurfaceModel(surface.key, v === "__default__" ? "" : v)}
                  >
                    <SelectTrigger
                      data-testid={`ai-models-surface-${surface.key}-select`}
                      className="w-full"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__default__"
                                  data-testid={`ai-models-surface-${surface.key}-option-default`}>
                        <span className="text-muted-foreground">
                          Tenant default — {modelLabel(modelById[draft.model_name])}
                        </span>
                      </SelectItem>
                      {meta.available_models.map((m) => (
                        <SelectItem
                          key={m.id}
                          value={m.id}
                          data-testid={`ai-models-surface-${surface.key}-option-${m.alias}`}
                        >
                          <ModelRow model={m}
                                    isRecommended={recommended?.id === m.id} />
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </li>
            );
          })}
        </ul>
      </section>

      {/* Save bar */}
      <div className="sticky bottom-4 flex items-center justify-end gap-2 rounded-sm border border-border bg-card/95 p-3 backdrop-blur">
        <p className="mr-auto text-xs text-muted-foreground">
          Changes apply on next AI call. Existing in-flight requests are unaffected.
        </p>
        <Button
          variant="ghost"
          onClick={refresh}
          data-testid="ai-models-reset-btn"
          disabled={saving}
        >
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Discard
        </Button>
        <Button
          onClick={save}
          data-testid="ai-models-save-btn"
          disabled={saving}
        >
          {saving ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  : <Save className="mr-1.5 h-3.5 w-3.5" />}
          Save AI settings
        </Button>
      </div>
    </div>
  );
}


function ModelRow({ model, isRecommended }) {
  const tier = TIER_BADGE[model.tier] || TIER_BADGE.balanced;
  const TierIcon = tier.icon;
  return (
    <span className="flex items-center gap-2">
      <span
        className={`inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${tier.className}`}
      >
        <TierIcon className="h-3 w-3" />
        {model.tier}
      </span>
      <span className="font-medium">{model.label}</span>
      {isRecommended && (
        <span className="inline-flex items-center gap-0.5 text-[10px] text-emerald-600 dark:text-emerald-400">
          <Check className="h-3 w-3" /> rec
        </span>
      )}
      <span className="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground">
        <Info className="h-3 w-3" />
        {priceCellsPerMtok(model)}
      </span>
    </span>
  );
}
