import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  fetchDenialClassifications,
  removeDenialClassification,
  upsertDenialClassification,
} from "./reportsApi";


/** Add-to-classifier dialog — teaches the heat map to route a denial
 *  code to a friendlier category. Opens from any `Uncategorised` row
 *  or cell. Also exposes a tiny manager for previously-added overrides
 *  so users can fix typos / remove stale entries.
 */
export function DenialClassifierDialog({
  open,
  initialCode,
  onClose,
  onSaved,
}) {
  const [catalog, setCatalog] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [code, setCode] = useState("");
  const [category, setCategory] = useState("");

  useEffect(() => {
    if (!open) return;
    setCode((initialCode || "").toUpperCase());
    setCategory("");
    setLoading(true);
    fetchDenialClassifications()
      .then(setCatalog)
      .catch((e) => toast.error(e?.response?.data?.detail || "Could not load classifier"))
      .finally(() => setLoading(false));
  }, [open, initialCode]);

  const knownCategories = useMemo(
    () => catalog?.known_categories || [],
    [catalog],
  );

  const existing = useMemo(() => {
    if (!catalog || !code) return null;
    return (catalog.tenant_overrides || []).find(
      (row) => (row.code || "").toUpperCase() === code.toUpperCase(),
    ) || null;
  }, [catalog, code]);

  // Suggest the category currently mapped to the code (built-in or
  // tenant) so the user can confirm / re-tag without typing.
  const currentMapping = useMemo(() => {
    if (!catalog || !code) return null;
    const up = code.toUpperCase();
    if (catalog.builtins?.[up]) {
      return { value: catalog.builtins[up], source: "built-in" };
    }
    if (existing) return { value: existing.category, source: "tenant" };
    return null;
  }, [catalog, code, existing]);

  async function onSave() {
    if (!code.trim() || !category.trim()) {
      toast.error("Code and category are both required.");
      return;
    }
    setSaving(true);
    try {
      await upsertDenialClassification({
        code: code.trim(),
        category: category.trim(),
      });
      toast.success(`Mapped ${code.trim().toUpperCase()} → ${category.trim()}`);
      onSaved?.();
      onClose?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not save classification");
    } finally { setSaving(false); }
  }

  async function onRemove(id, codeLabel) {
    try {
      await removeDenialClassification(id);
      toast.success(`Removed ${codeLabel}`);
      const fresh = await fetchDenialClassifications();
      setCatalog(fresh);
      onSaved?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not remove classification");
    }
  }

  return (
    <Dialog open={!!open} onOpenChange={(v) => !v && onClose?.()}>
      <DialogContent
        data-testid="denial-classifier-dialog"
        className="max-w-lg"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            Teach the classifier
          </DialogTitle>
          <DialogDescription>
            Tell the heat map where to route a denial code the payer
            returned. Tenant mappings override the built-in CARC map.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-1">
          <label className="block space-y-1 text-xs">
            <span className="uppercase tracking-wide text-muted-foreground">
              Denial code
            </span>
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="e.g. CO-197"
              maxLength={16}
              data-testid="denial-classifier-code"
              className="rounded-sm font-mono uppercase"
            />
            {currentMapping && (
              <span
                data-testid="denial-classifier-current-mapping"
                className="mt-1 block text-[11px] text-muted-foreground"
              >
                Currently → <span className="font-medium">{currentMapping.value}</span>
                {" "}<em>({currentMapping.source})</em>
              </span>
            )}
          </label>

          <label className="block space-y-1 text-xs">
            <span className="uppercase tracking-wide text-muted-foreground">
              Category
            </span>
            <Input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="e.g. Chiropractic-specific"
              maxLength={80}
              list="denial-known-categories"
              data-testid="denial-classifier-category"
              className="rounded-sm"
            />
            <datalist id="denial-known-categories">
              {knownCategories.map((c) => <option key={c} value={c} />)}
            </datalist>
            <span className="text-[11px] text-muted-foreground">
              Tip: pick an existing category to keep the heat map tidy.
              Only create a new one when none of the existing buckets fit.
            </span>
          </label>

          {catalog?.tenant_overrides?.length > 0 && (
            <section
              data-testid="denial-classifier-existing"
              className="rounded-sm border border-border p-2"
            >
              <header className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Existing tenant mappings ({catalog.tenant_overrides.length})
              </header>
              <ul className="max-h-40 overflow-auto divide-y divide-border text-xs">
                {catalog.tenant_overrides.map((row) => (
                  <li key={row.id} className="flex items-center gap-2 py-1">
                    <span className="font-mono">{row.code}</span>
                    <span className="text-muted-foreground">→</span>
                    <span className="flex-1 truncate">{row.category}</span>
                    <Button
                      size="sm" variant="ghost"
                      onClick={() => onRemove(row.id, row.code)}
                      data-testid={`denial-classifier-remove-${row.id}`}
                      className="h-6 w-6 rounded-sm p-0 text-muted-foreground hover:text-destructive"
                      aria-label={`Remove ${row.code}`}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline" size="sm" onClick={onClose}
            data-testid="denial-classifier-cancel-btn"
            className="rounded-sm"
          >
            Cancel
          </Button>
          <Button
            size="sm" onClick={onSave} disabled={saving || loading}
            data-testid="denial-classifier-save-btn"
            className="rounded-sm"
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            {saving ? "Saving…" : existing ? "Update" : "Add mapping"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
