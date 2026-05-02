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
import { fetchPolicies, createPolicy, changeStatus } from "../api";
import { StatusChip, EmptyState, SectionHeader, HistoryDialog, todayIso, inDaysIso, isOverdue } from "../common";
import { formatDate } from "../../../utils/time";

const FILTERS = [
  { v: "all", l: "All" },
  { v: "draft", l: "Draft" },
  { v: "approved", l: "Approved" },
  { v: "retired", l: "Retired" },
];

const NEXT_STATUSES = {
  draft: ["approved", "retired"],
  approved: ["draft", "retired"],
  retired: ["draft"],
};

function CreatePolicyDialog({ open, onClose, onCreated }) {
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: "", version: "1.0", summary: "",
    effective_date: todayIso(), review_date: inDaysIso(365),
  });

  const submit = async () => {
    if (!form.name || !form.summary) {
      toast.error("Name and summary are required.");
      return;
    }
    setBusy(true);
    try {
      await createPolicy(form);
      toast.success("Policy drafted.");
      onCreated();
      onClose();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to create policy.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="policy-create-dialog" className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">Draft new policy</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label htmlFor="policy-name">Name</Label>
            <Input
              id="policy-name"
              data-testid="policy-form-name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. Acceptable Use Policy"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="policy-version">Version</Label>
              <Input
                id="policy-version"
                data-testid="policy-form-version"
                value={form.version}
                onChange={(e) => setForm({ ...form, version: e.target.value })}
              />
            </div>
            <div>
              <Label htmlFor="policy-effective">Effective date</Label>
              <Input
                id="policy-effective"
                data-testid="policy-form-effective"
                type="date"
                value={form.effective_date.slice(0, 10)}
                onChange={(e) => setForm({ ...form, effective_date: e.target.value })}
              />
            </div>
          </div>
          <div>
            <Label htmlFor="policy-review">Next review date</Label>
            <Input
              id="policy-review"
              data-testid="policy-form-review"
              type="date"
              value={form.review_date.slice(0, 10)}
              onChange={(e) => setForm({ ...form, review_date: e.target.value })}
            />
          </div>
          <div>
            <Label htmlFor="policy-summary">Summary</Label>
            <Textarea
              id="policy-summary"
              data-testid="policy-form-summary"
              rows={4}
              value={form.summary}
              onChange={(e) => setForm({ ...form, summary: e.target.value })}
              placeholder="One-paragraph summary of policy intent."
            />
          </div>
        </div>
        <DialogFooter>
          <Button data-testid="policy-form-cancel" variant="outline" onClick={onClose}>Cancel</Button>
          <Button data-testid="policy-form-save" disabled={busy} onClick={submit}>
            {busy ? "Saving…" : "Save draft"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function PoliciesRegister() {
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState(null);
  const [openCreate, setOpenCreate] = useState(false);
  const [historyId, setHistoryId] = useState(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await fetchPolicies(filter === "all" ? undefined : filter);
      setRows(data);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to load policies");
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const onStatus = async (row, newStatus) => {
    try {
      await changeStatus("policy", row.id, newStatus);
      toast.success(`Policy → ${newStatus}.`);
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not change status.");
    }
  };

  return (
    <section data-testid="policies-register" className="space-y-4">
      <SectionHeader
        testid="policies-header"
        title="Policies"
        count={rows.length}
        action={
          <Button
            data-testid="policy-add-btn"
            size="sm"
            onClick={() => setOpenCreate(true)}
            className="gap-1"
          >
            <Plus className="h-4 w-4" /> New policy
          </Button>
        }
      />
      <div data-testid="policies-filters" className="flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.v}
            data-testid={`policies-filter-${f.v}`}
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
        <div data-testid="policies-error" className="rounded-sm border border-destructive-soft bg-destructive-soft p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState testid="policies-empty" label="No policies in this view." />
      ) : (
        <div className="overflow-x-auto rounded-sm border border-border bg-card">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="border-b border-border text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Policy</th>
                <th className="px-4 py-3 font-medium">Version</th>
                <th className="px-4 py-3 font-medium">Effective</th>
                <th className="px-4 py-3 font-medium">Next review</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id} data-testid={`policy-row-${p.id}`} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 align-top">
                    <div className="text-sm text-foreground">{p.name}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{p.summary}</div>
                  </td>
                  <td className="px-4 py-3 align-top text-xs font-mono text-muted-foreground">{p.version}</td>
                  <td className="px-4 py-3 align-top text-xs text-muted-foreground">{formatDate(p.effective_date)}</td>
                  <td className="px-4 py-3 align-top text-xs">
                    <span className={isOverdue(p.review_date) && p.status !== "retired" ? "text-destructive font-semibold" : "text-muted-foreground"}>
                      {formatDate(p.review_date)}
                      {isOverdue(p.review_date) && p.status !== "retired" && " · overdue"}
                    </span>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <StatusChip status={p.status} testid={`policy-status-${p.id}`} />
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center justify-end gap-2">
                      {NEXT_STATUSES[p.status] && (
                        <Select onValueChange={(v) => onStatus(p, v)}>
                          <SelectTrigger data-testid={`policy-status-trigger-${p.id}`} className="h-8 w-[140px] text-xs">
                            <SelectValue placeholder="Change status" />
                          </SelectTrigger>
                          <SelectContent>
                            {NEXT_STATUSES[p.status].map((s) => (
                              <SelectItem key={s} value={s} data-testid={`policy-status-option-${p.id}-${s}`}>
                                {s.charAt(0).toUpperCase() + s.slice(1)}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                      <Button
                        data-testid={`policy-history-${p.id}`}
                        variant="ghost"
                        size="sm"
                        onClick={() => setHistoryId(p.id)}
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

      <CreatePolicyDialog open={openCreate} onClose={() => setOpenCreate(false)} onCreated={load} />
      <HistoryDialog
        entityType="policy"
        entityId={historyId}
        open={!!historyId}
        onClose={() => setHistoryId(null)}
      />
    </section>
  );
}
