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
import { fetchAccessReviews, createAccessReview, changeStatus, patchEntity } from "../api";
import { StatusChip, EmptyState, SectionHeader, HistoryDialog, inDaysIso } from "../common";
import { formatDate } from "../../../utils/time";

const FILTERS = [
  { v: "all", l: "All" },
  { v: "scheduled", l: "Scheduled" },
  { v: "in_progress", l: "In progress" },
  { v: "complete", l: "Complete" },
  { v: "overdue", l: "Overdue" },
];

const SCOPES = [
  "tenant_admins",
  "platform_admins",
  "privileged_engineers",
  "break_glass_events",
  "inactive_users",
  "stale_service_accounts",
];

const NEXT_STATUSES = {
  scheduled: ["in_progress", "complete"],
  in_progress: ["complete"],
  overdue: ["in_progress", "complete"],
  complete: [],
};

function CreateAccessReviewDialog({ open, onClose, onCreated }) {
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: "", scope: "tenant_admins", due_at: inDaysIso(30), notes: "",
  });

  const submit = async () => {
    if (!form.name) {
      toast.error("Name is required.");
      return;
    }
    setBusy(true);
    try {
      const body = { ...form };
      if (body.due_at && !body.due_at.endsWith("Z")) {
        body.due_at = new Date(body.due_at).toISOString();
      }
      await createAccessReview(body);
      toast.success("Access review scheduled.");
      onCreated();
      onClose();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to schedule review.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="access-review-create-dialog" className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">Schedule access review</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label htmlFor="ar-name">Name</Label>
            <Input id="ar-name" data-testid="ar-form-name" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. Q3 2026 admin review" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="ar-scope">Scope</Label>
              <Select value={form.scope} onValueChange={(v) => setForm({ ...form, scope: v })}>
                <SelectTrigger id="ar-scope" data-testid="ar-form-scope"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SCOPES.map((s) => <SelectItem key={s} value={s}>{s.replace(/_/g, " ")}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label htmlFor="ar-due">Due date</Label>
              <Input id="ar-due" data-testid="ar-form-due" type="date" value={form.due_at.slice(0, 10)}
                onChange={(e) => setForm({ ...form, due_at: e.target.value })} />
            </div>
          </div>
          <div>
            <Label htmlFor="ar-notes">Notes</Label>
            <Textarea id="ar-notes" data-testid="ar-form-notes" rows={2} value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </div>
        </div>
        <DialogFooter>
          <Button data-testid="ar-form-cancel" variant="outline" onClick={onClose}>Cancel</Button>
          <Button data-testid="ar-form-save" disabled={busy} onClick={submit}>
            {busy ? "Saving…" : "Schedule"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function AccessReviewsRegister() {
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState(null);
  const [openCreate, setOpenCreate] = useState(false);
  const [historyId, setHistoryId] = useState(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await fetchAccessReviews(filter === "all" ? undefined : filter);
      setRows(data);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to load access reviews");
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const onComplete = async (row) => {
    try {
      await patchEntity("access_review", row.id, {
        completed_at: new Date().toISOString(),
        decision: "no_changes",
      });
      await changeStatus("access_review", row.id, "complete");
      toast.success("Access review marked complete.");
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not complete review.");
    }
  };

  const onAdvance = async (row, newStatus) => {
    try {
      await changeStatus("access_review", row.id, newStatus);
      toast.success(`Access review → ${newStatus}.`);
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not change status.");
    }
  };

  return (
    <section data-testid="access-reviews-register" className="space-y-4">
      <SectionHeader
        testid="access-reviews-header"
        title="Access reviews"
        count={rows.length}
        action={
          <Button data-testid="ar-add-btn" size="sm" onClick={() => setOpenCreate(true)} className="gap-1">
            <Plus className="h-4 w-4" /> Schedule review
          </Button>
        }
      />
      <div data-testid="access-reviews-filters" className="flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.v}
            data-testid={`access-reviews-filter-${f.v}`}
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
        <div data-testid="access-reviews-error" className="rounded-sm border border-destructive-soft bg-destructive-soft p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState testid="access-reviews-empty" label="No reviews in this view." />
      ) : (
        <div className="overflow-x-auto rounded-sm border border-border bg-card">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="border-b border-border text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Review</th>
                <th className="px-4 py-3 font-medium">Scope</th>
                <th className="px-4 py-3 font-medium">Due</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} data-testid={`ar-row-${r.id}`} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 align-top">
                    <div className="text-sm text-foreground">{r.name}</div>
                    {r.notes && <div className="mt-0.5 text-xs text-muted-foreground">{r.notes}</div>}
                  </td>
                  <td className="px-4 py-3 align-top text-xs uppercase tracking-wider text-muted-foreground">
                    {r.scope.replace(/_/g, " ")}
                  </td>
                  <td className="px-4 py-3 align-top text-xs text-muted-foreground">{formatDate(r.due_at)}</td>
                  <td className="px-4 py-3 align-top">
                    <StatusChip status={r.status} testid={`ar-status-${r.id}`} />
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center justify-end gap-2">
                      {r.status !== "complete" && (
                        <>
                          {NEXT_STATUSES[r.status]?.includes("in_progress") && (
                            <Button
                              data-testid={`ar-start-${r.id}`}
                              variant="outline"
                              size="sm"
                              onClick={() => onAdvance(r, "in_progress")}
                            >
                              Start
                            </Button>
                          )}
                          <Button
                            data-testid={`ar-complete-${r.id}`}
                            size="sm"
                            onClick={() => onComplete(r)}
                          >
                            Complete
                          </Button>
                        </>
                      )}
                      <Button
                        data-testid={`ar-history-${r.id}`}
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

      <CreateAccessReviewDialog open={openCreate} onClose={() => setOpenCreate(false)} onCreated={load} />
      <HistoryDialog
        entityType="access_review"
        entityId={historyId}
        open={!!historyId}
        onClose={() => setHistoryId(null)}
      />
    </section>
  );
}
