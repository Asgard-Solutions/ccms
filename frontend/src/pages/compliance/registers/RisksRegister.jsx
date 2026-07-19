import { useCallback, useEffect, useState } from "react";
import { Plus, History as HistoryIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../../../components/ui/button";
import { Input } from "../../../components/ui/input";
import { Textarea } from "../../../components/ui/textarea";
import { Label } from "../../../components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../../components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../../components/ui/dialog";
import { fetchRisks, createRisk, changeStatus } from "../api";
import { StatusChip, EmptyState, SectionHeader, ScoreChip, HistoryDialog, inDaysIso } from "../common";
import { formatDate } from "../../../utils/time";

const FILTERS = [
  { v: "all", l: "All" },
  { v: "open", l: "Open" },
  { v: "mitigating", l: "Mitigating" },
  { v: "mitigated", l: "Mitigated" },
  { v: "accepted", l: "Accepted" },
  { v: "closed", l: "Closed" },
];

const NEXT_STATUSES = {
  open: ["mitigating", "accepted", "transferred"],
  mitigating: ["mitigated", "accepted"],
  mitigated: ["closed", "open"],
  accepted: ["open"],
  transferred: ["closed"],
  closed: ["open"],
};

const TREATMENTS = ["mitigate", "accept", "transfer", "avoid"];

function CreateRiskDialog({ open, onClose, onCreated }) {
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    title: "", description: "", asset: "", threat: "", vulnerability: "",
    likelihood: 3, impact: 3, treatment: "mitigate", target_date: inDaysIso(60),
  });
  const score = form.likelihood * form.impact;

  const submit = async () => {
    if (!form.title || !form.asset || !form.threat || !form.vulnerability) {
      toast.error("Title, asset, threat and vulnerability are required.");
      return;
    }
    setBusy(true);
    try {
      await createRisk(form);
      toast.success("Risk recorded.");
      onCreated();
      onClose();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to create risk.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="risk-create-dialog" className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">New risk</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label htmlFor="risk-title">Title</Label>
            <Input id="risk-title" data-testid="risk-form-title" value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="e.g. Excessive privileged access in finance system" />
          </div>
          <div>
            <Label htmlFor="risk-description">Description</Label>
            <Textarea id="risk-description" data-testid="risk-form-description" rows={2} value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label htmlFor="risk-asset">Asset</Label>
              <Input id="risk-asset" data-testid="risk-form-asset" value={form.asset}
                onChange={(e) => setForm({ ...form, asset: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="risk-threat">Threat</Label>
              <Input id="risk-threat" data-testid="risk-form-threat" value={form.threat}
                onChange={(e) => setForm({ ...form, threat: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="risk-vulnerability">Vulnerability</Label>
              <Input id="risk-vulnerability" data-testid="risk-form-vulnerability" value={form.vulnerability}
                onChange={(e) => setForm({ ...form, vulnerability: e.target.value })} />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label htmlFor="risk-likelihood">Likelihood (1-5)</Label>
              <Input id="risk-likelihood" data-testid="risk-form-likelihood" type="number" min={1} max={5}
                value={form.likelihood}
                onChange={(e) => setForm({ ...form, likelihood: Number(e.target.value) || 1 })} />
            </div>
            <div>
              <Label htmlFor="risk-impact">Impact (1-5)</Label>
              <Input id="risk-impact" data-testid="risk-form-impact" type="number" min={1} max={5}
                value={form.impact}
                onChange={(e) => setForm({ ...form, impact: Number(e.target.value) || 1 })} />
            </div>
            <div>
              <Label>Inherent score</Label>
              <div data-testid="risk-form-score" className="mt-2"><ScoreChip score={score} /></div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="risk-treatment">Treatment</Label>
              <Select value={form.treatment} onValueChange={(v) => setForm({ ...form, treatment: v })}>
                <SelectTrigger id="risk-treatment" data-testid="risk-form-treatment"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {TREATMENTS.map((t) => (
                    <SelectItem key={t} value={t}>{t}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label htmlFor="risk-target">Target date</Label>
              <Input id="risk-target" data-testid="risk-form-target" type="date"
                value={form.target_date.slice(0, 10)}
                onChange={(e) => setForm({ ...form, target_date: e.target.value })} />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button data-testid="risk-form-cancel" variant="outline" onClick={onClose}>Cancel</Button>
          <Button data-testid="risk-form-save" disabled={busy} onClick={submit}>
            {busy ? "Saving…" : "Save risk"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function RisksRegister() {
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState(null);
  const [openCreate, setOpenCreate] = useState(false);
  const [historyId, setHistoryId] = useState(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await fetchRisks(filter === "all" ? undefined : filter);
      setRows(data);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to load risks");
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const onStatus = async (row, newStatus) => {
    try {
      await changeStatus("risk", row.id, newStatus);
      toast.success(`Risk → ${newStatus}.`);
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not change status.");
    }
  };

  return (
    <section data-testid="risks-register" className="space-y-4">
      <SectionHeader
        testid="risks-header"
        title="Risk register"
        count={rows.length}
        action={
          <Button data-testid="risk-add-btn" size="sm" onClick={() => setOpenCreate(true)} className="gap-1">
            <Plus className="h-4 w-4" /> New risk
          </Button>
        }
      />
      <div data-testid="risks-filters" className="flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.v}
            data-testid={`risks-filter-${f.v}`}
            onClick={() => setFilter(f.v)}
            className={`rounded-sm px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider transition-colors ${
              filter === f.v ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground hover:bg-secondary-hover"
            }`}
          >
            {f.l}
          </button>
        ))}
      </div>

      {error && (
        <div data-testid="risks-error" className="rounded-sm border border-destructive-soft bg-destructive-soft p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState testid="risks-empty" label="No risks in this view." />
      ) : (
        <div className="overflow-x-auto rounded-sm border border-border bg-card">
          <table className="w-full min-w-[820px] text-left text-sm">
            <thead className="border-b border-border text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Risk</th>
                <th className="px-4 py-3 font-medium">Asset / threat</th>
                <th className="px-4 py-3 font-medium">Inherent</th>
                <th className="px-4 py-3 font-medium">Treatment</th>
                <th className="px-4 py-3 font-medium">Target</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} data-testid={`risk-row-${r.id}`} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 align-top">
                    <div className="text-sm text-foreground">{r.title}</div>
                    {r.description && (
                      <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{r.description}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 align-top text-xs text-muted-foreground">
                    <div>{r.asset}</div>
                    <div className="text-[11px]">{r.threat} · {r.vulnerability}</div>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <ScoreChip score={r.inherent_score} testid={`risk-score-${r.id}`} />
                    <div className="mt-0.5 text-[10px] text-muted-foreground">L{r.likelihood} · I{r.impact}</div>
                  </td>
                  <td className="px-4 py-3 align-top text-xs uppercase tracking-wider text-muted-foreground">{r.treatment}</td>
                  <td className="px-4 py-3 align-top text-xs text-muted-foreground">
                    {r.target_date ? formatDate(r.target_date) : "—"}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <StatusChip status={r.status} testid={`risk-status-${r.id}`} />
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center justify-end gap-2">
                      {NEXT_STATUSES[r.status] && (
                        <Select onValueChange={(v) => onStatus(r, v)}>
                          <SelectTrigger data-testid={`risk-status-trigger-${r.id}`} className="h-8 w-[140px] text-xs">
                            <SelectValue placeholder="Change status" />
                          </SelectTrigger>
                          <SelectContent>
                            {NEXT_STATUSES[r.status].map((s) => (
                              <SelectItem key={s} value={s} data-testid={`risk-status-option-${r.id}-${s}`}>
                                {s}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                      <Button
                        data-testid={`risk-history-${r.id}`}
                        variant="ghost"
                        size="sm"
                        onClick={() => setHistoryId(r.id)}
                      >
                        <HistoryIcon className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <CreateRiskDialog open={openCreate} onClose={() => setOpenCreate(false)} onCreated={load} />
      <HistoryDialog
        entityType="risk"
        entityId={historyId}
        open={!!historyId}
        onClose={() => setHistoryId(null)}
      />
    </section>
  );
}
