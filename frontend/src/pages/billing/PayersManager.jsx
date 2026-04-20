import { useState } from "react";
import { toast } from "sonner";
import { Plus, Building2, Pencil } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import {
  createPayer,
  updatePayer,
  usePayers,
} from "./useBillingAdmin";

const PAYER_TYPES = [
  "commercial", "medicare", "medicaid", "workers_comp",
  "auto", "self_pay", "other",
];
const REMIT_METHODS = [
  { v: "era", l: "ERA (electronic)" },
  { v: "paper_eob", l: "Paper EOB" },
  { v: "none", l: "None" },
];

export default function PayersManager() {
  const { rows, loading, refresh } = usePayers();
  const [editing, setEditing] = useState(null);

  return (
    <section
      data-testid="payers-manager"
      className="rounded-sm border border-border bg-card p-6"
    >
      <header className="mb-4 flex items-end justify-between">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Billing master data
          </div>
          <h2 className="mt-1 font-display text-2xl font-medium tracking-tight">
            Payers
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Insurance carriers and other bill-to parties.
          </p>
        </div>
        <Button
          onClick={() => setEditing({})}
          data-testid="payers-add-btn"
          className="rounded-sm"
          size="sm"
        >
          <Plus className="mr-1 h-4 w-4" /> Add payer
        </Button>
      </header>

      {loading ? (
        <Skeleton className="h-24 w-full rounded-sm" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No payers configured yet.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((p) => (
            <li
              key={p.id}
              data-testid={`payer-row-${p.id}`}
              className="flex items-center gap-3 py-3"
            >
              <Building2 className="h-4 w-4 text-muted-foreground" />
              <div className="flex-1 min-w-0">
                <div className="font-medium">{p.name}</div>
                <div className="text-xs text-muted-foreground">
                  {p.payer_type.replaceAll("_", " ")}
                  {p.payer_code ? ` · code ${p.payer_code}` : ""}
                  {" · "}
                  <span className={p.status === "active" ? "text-success" : ""}>
                    {p.status}
                  </span>
                </div>
              </div>
              <Button
                variant="ghost" size="sm"
                onClick={() => setEditing(p)}
                data-testid={`payer-edit-${p.id}`}
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      {editing !== null && (
        <PayerDialog
          payer={editing.id ? editing : null}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await refresh();
          }}
        />
      )}
    </section>
  );
}

function PayerDialog({ payer, onClose, onSaved }) {
  const isEdit = !!payer;
  const [form, setForm] = useState(() => ({
    name: payer?.name || "",
    payer_type: payer?.payer_type || "commercial",
    payer_code: payer?.payer_code || "",
    electronic_payer_id: payer?.electronic_payer_id || "",
    remit_method: payer?.remit_method || "era",
    status: payer?.status || "active",
    notes: payer?.notes || "",
  }));
  const [saving, setSaving] = useState(false);

  function patch(k, v) { setForm((f) => ({ ...f, [k]: v })); }

  async function onSubmit() {
    if (form.name.trim().length < 2) return toast.error("Name required");
    const payload = {
      name: form.name.trim(),
      payer_type: form.payer_type,
      payer_code: form.payer_code.trim() || null,
      electronic_payer_id: form.electronic_payer_id.trim() || null,
      remit_method: form.remit_method,
      notes: form.notes.trim() || null,
    };
    if (isEdit) payload.status = form.status;
    setSaving(true);
    try {
      if (isEdit) await updatePayer(payer.id, payload);
      else await createPayer(payload);
      toast.success(isEdit ? "Payer updated" : "Payer created");
      onSaved();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        data-testid="payer-dialog"
        className="rounded-sm sm:max-w-lg"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            {isEdit ? "Edit payer" : "Add payer"}
          </DialogTitle>
          <DialogDescription>
            Payer records are shared across the tenant. They drive
            insurance billing and fee schedule overrides.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <Label htmlFor="payer-name">Name</Label>
            <Input id="payer-name" data-testid="payer-name"
                   value={form.name} onChange={(e) => patch("name", e.target.value)} />
          </div>
          <div>
            <Label htmlFor="payer-type">Type</Label>
            <Select
              value={form.payer_type}
              onValueChange={(v) => patch("payer_type", v)}
            >
              <SelectTrigger id="payer-type"><SelectValue /></SelectTrigger>
              <SelectContent>
                {PAYER_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>{t.replaceAll("_", " ")}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="payer-remit">Remit method</Label>
            <Select
              value={form.remit_method}
              onValueChange={(v) => patch("remit_method", v)}
            >
              <SelectTrigger id="payer-remit"><SelectValue /></SelectTrigger>
              <SelectContent>
                {REMIT_METHODS.map((m) => (
                  <SelectItem key={m.v} value={m.v}>{m.l}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="payer-code">Payer code</Label>
            <Input id="payer-code" value={form.payer_code}
                   onChange={(e) => patch("payer_code", e.target.value)} />
          </div>
          <div>
            <Label htmlFor="payer-epid">Electronic payer ID</Label>
            <Input id="payer-epid" value={form.electronic_payer_id}
                   onChange={(e) => patch("electronic_payer_id", e.target.value)} />
          </div>
          {isEdit && (
            <div>
              <Label htmlFor="payer-status">Status</Label>
              <Select
                value={form.status}
                onValueChange={(v) => patch("status", v)}
              >
                <SelectTrigger id="payer-status"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">Active</SelectItem>
                  <SelectItem value="inactive">Inactive</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="sm:col-span-2">
            <Label htmlFor="payer-notes">Notes</Label>
            <Textarea id="payer-notes" rows={2}
                      value={form.notes} onChange={(e) => patch("notes", e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={onSubmit} disabled={saving}
                  data-testid="payer-save" className="rounded-sm">
            {saving ? "Saving…" : isEdit ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
