import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Calculator, Plus, Tag } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
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
  createFeeSchedule,
  fetchFeeScheduleLines,
  upsertFeeScheduleLines,
  useFeeSchedules,
  usePayers,
} from "./useBillingAdmin";
import { formatCents, parseDollarsToCents } from "../../utils/money";

export default function FeeSchedulesManager() {
  const { rows, loading, refresh } = useFeeSchedules();
  const [createOpen, setCreateOpen] = useState(false);
  const [linesOpen, setLinesOpen] = useState(null);

  return (
    <section
      data-testid="fee-schedules-manager"
      className="rounded-sm border border-border bg-card p-6"
    >
      <header className="mb-4 flex items-end justify-between">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Billing master data
          </div>
          <h2 className="mt-1 font-display text-2xl font-medium tracking-tight">
            Fee schedules
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            One active self-pay rate schedule drives default pricing.
            Payer-specific schedules override for insurance billing.
          </p>
        </div>
        <Button
          onClick={() => setCreateOpen(true)}
          data-testid="fs-add-btn"
          className="rounded-sm" size="sm"
        >
          <Plus className="mr-1 h-4 w-4" /> New schedule
        </Button>
      </header>

      {loading ? (
        <Skeleton className="h-24 w-full rounded-sm" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No fee schedules yet. Create a self-pay schedule to unblock
          charge capture.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((s) => (
            <li
              key={s.id}
              data-testid={`fs-row-${s.id}`}
              className="flex items-center gap-3 py-3"
            >
              <Calculator className="h-4 w-4 text-muted-foreground" />
              <div className="flex-1 min-w-0">
                <div className="font-medium">{s.name}</div>
                <div className="text-xs text-muted-foreground">
                  {s.kind.replaceAll("_", " ")} · {s.line_count} code
                  {s.line_count === 1 ? "" : "s"}
                  {s.active ? " · active" : " · inactive"}
                </div>
              </div>
              <Button
                variant="outline" size="sm"
                onClick={() => setLinesOpen(s)}
                data-testid={`fs-edit-${s.id}`}
              >
                <Tag className="mr-1 h-3.5 w-3.5" /> Edit rates
              </Button>
            </li>
          ))}
        </ul>
      )}

      {createOpen && (
        <CreateDialog
          onClose={() => setCreateOpen(false)}
          onCreated={async () => {
            setCreateOpen(false);
            await refresh();
          }}
        />
      )}
      {linesOpen && (
        <LinesDialog
          schedule={linesOpen}
          onClose={() => setLinesOpen(null)}
          onSaved={async () => {
            setLinesOpen(null);
            await refresh();
          }}
        />
      )}
    </section>
  );
}

function CreateDialog({ onClose, onCreated }) {
  const { rows: payers } = usePayers({ activeOnly: true });
  const [form, setForm] = useState({
    name: "", kind: "self_pay", payer_id: "",
  });
  const [saving, setSaving] = useState(false);

  async function onSubmit() {
    if (form.name.trim().length < 2) return toast.error("Name required");
    if (form.kind === "payer" && !form.payer_id) {
      return toast.error("Select a payer");
    }
    setSaving(true);
    try {
      await createFeeSchedule({
        name: form.name.trim(),
        kind: form.kind,
        payer_id: form.kind === "payer" ? form.payer_id : null,
      });
      toast.success("Schedule created");
      onCreated();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="fs-create-dialog" className="rounded-sm sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-display">New fee schedule</DialogTitle>
          <DialogDescription>
            Self-pay is the clinic default. Payer-specific overrides
            insurance pricing for contracted rates.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label htmlFor="fs-name">Name</Label>
            <Input id="fs-name" data-testid="fs-name"
                   value={form.name}
                   onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="fs-kind">Kind</Label>
            <Select value={form.kind} onValueChange={(v) => setForm({ ...form, kind: v })}>
              <SelectTrigger id="fs-kind"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="self_pay">Self-pay (default)</SelectItem>
                <SelectItem value="payer">Payer-specific</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {form.kind === "payer" && (
            <div>
              <Label htmlFor="fs-payer">Payer</Label>
              <Select
                value={form.payer_id}
                onValueChange={(v) => setForm({ ...form, payer_id: v })}
              >
                <SelectTrigger id="fs-payer"><SelectValue placeholder="Select" /></SelectTrigger>
                <SelectContent>
                  {payers.map((p) => (
                    <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={onSubmit} disabled={saving}
                  data-testid="fs-create-submit" className="rounded-sm">
            {saving ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function LinesDialog({ schedule, onClose, onSaved }) {
  const [lines, setLines] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await fetchFeeScheduleLines(schedule.id);
        if (!alive) return;
        setLines(data.length
          ? data
          : [{ code_type: "cpt", code: "", allowed_cents: 0 }]);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [schedule.id]);

  function addRow() {
    setLines([...lines, { code_type: "cpt", code: "", allowed_cents: 0 }]);
  }

  function updateRow(i, patch) {
    setLines(lines.map((l, idx) => idx === i ? { ...l, ...patch } : l));
  }

  async function onSubmit() {
    const valid = lines.filter(
      (l) => (l.code || "").trim() && (l.allowed_cents >= 0),
    );
    if (valid.length === 0) return toast.error("Add at least one row");
    setSaving(true);
    try {
      await upsertFeeScheduleLines(schedule.id, valid.map((l) => ({
        code_type: l.code_type || "cpt",
        code: l.code.trim(),
        allowed_cents: Number(l.allowed_cents) || 0,
      })));
      toast.success(`Saved ${valid.length} rates`);
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
        data-testid="fs-lines-dialog"
        className="max-h-[85vh] overflow-y-auto rounded-sm sm:max-w-2xl"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            Rates · {schedule.name}
          </DialogTitle>
          <DialogDescription>
            Upserts by (code_type, code). Existing codes get their rate
            updated.
          </DialogDescription>
        </DialogHeader>
        {loading ? (
          <Skeleton className="h-32 w-full rounded-sm" />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="py-1 pr-2 w-24">Code type</th>
                <th className="py-1 pr-2">Code</th>
                <th className="py-1 pr-2 text-right">Rate ($)</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1.5 pr-2">
                    <Select
                      value={l.code_type || "cpt"}
                      onValueChange={(v) => updateRow(i, { code_type: v })}
                    >
                      <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="cpt">CPT</SelectItem>
                        <SelectItem value="hcpcs">HCPCS</SelectItem>
                        <SelectItem value="custom">Custom</SelectItem>
                      </SelectContent>
                    </Select>
                  </td>
                  <td className="py-1.5 pr-2">
                    <Input
                      value={l.code}
                      data-testid={`fs-line-code-${i}`}
                      onChange={(e) => updateRow(i, { code: e.target.value })}
                    />
                  </td>
                  <td className="py-1.5 pr-2 text-right">
                    <Input
                      className="w-28 text-right"
                      value={l.allowed_cents ? (l.allowed_cents / 100).toFixed(2) : ""}
                      data-testid={`fs-line-rate-${i}`}
                      onChange={(e) => updateRow(i, {
                        allowed_cents: parseDollarsToCents(e.target.value) || 0,
                      })}
                      placeholder="0.00"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Button
          variant="outline" size="sm" onClick={addRow}
          data-testid="fs-add-row"
        >
          <Plus className="mr-1 h-3.5 w-3.5" /> Add row
        </Button>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={onSubmit} disabled={saving || loading}
                  data-testid="fs-lines-save" className="rounded-sm">
            {saving ? "Saving…" : "Save rates"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Reference formatCents in the file so it stays imported (used for hover labels / future)
void formatCents;
