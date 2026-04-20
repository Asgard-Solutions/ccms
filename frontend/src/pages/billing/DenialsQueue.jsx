import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ArrowRight, Filter } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Dialog,
  DialogContent,
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
import { formatCents } from "../../utils/money";
import { formatDateTime } from "../../utils/time";
import {
  DENIAL_STATUS_LABELS,
  denialStatusTone,
  updateDenialWorkItem,
  useDenialWorkItems,
} from "./useRemittance";

const STATUS_OPTIONS = [
  { v: "all", l: "All statuses" },
  ...Object.entries(DENIAL_STATUS_LABELS).map(([v, l]) => ({ v, l })),
];

export default function DenialsQueue() {
  const { rows, loading, refresh } = useDenialWorkItems();
  const [status, setStatus] = useState("all");
  const [ownerFilter, setOwnerFilter] = useState("");
  const [editItem, setEditItem] = useState(null);

  const filtered = useMemo(() => rows.filter((r) => {
    if (status !== "all" && r.status !== status) return false;
    if (ownerFilter && (r.assigned_to_id || "").slice(0, 8) !== ownerFilter.slice(0, 8)) return false;
    return true;
  }), [rows, status, ownerFilter]);

  const totalAmount = useMemo(
    () => filtered.reduce((a, r) => a + (r.amount_cents || 0), 0),
    [filtered],
  );

  return (
    <div data-testid="denials-queue" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Billing
          </div>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
            Denial work queue
          </h1>
        </div>
        <Button asChild variant="outline" className="rounded-sm">
          <Link to="/billing" data-testid="denials-back-btn">Back to dashboard</Link>
        </Button>
      </header>

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Open items" value={rows.filter(r => r.status === "open").length} tone="warning" />
        <Stat label="In progress" value={rows.filter(r => r.status === "in_progress").length} tone="primary" />
        <Stat label="Amount in view" value={formatCents(totalAmount)} />
      </div>

      <section
        data-testid="denials-filter-bar"
        className="flex flex-wrap items-end gap-3 rounded-sm border border-border bg-card p-4"
      >
        <div className="flex flex-col gap-1">
          <Label className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            <Filter className="mr-1 inline h-3 w-3" /> Status
          </Label>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-48" data-testid="denials-status-filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((o) => (
                <SelectItem key={o.v} value={o.v}>{o.l}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Owner
          </Label>
          <Input
            placeholder="assignee id"
            value={ownerFilter}
            onChange={(e) => setOwnerFilter(e.target.value)}
            className="w-48"
            data-testid="denials-owner-filter"
          />
        </div>
      </section>

      <section className="overflow-hidden rounded-sm border border-border bg-card">
        {loading ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-10 w-full rounded-sm" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <p className="p-6 text-center text-sm text-muted-foreground">
            No denials match this view.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-4 py-2">Opened</th>
                <th className="px-4 py-2">Claim</th>
                <th className="px-4 py-2">Code</th>
                <th className="px-4 py-2 text-right">Amount</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Owner</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr
                  key={r.id}
                  data-testid={`denial-row-${r.id}`}
                  className="border-t border-border hover:bg-muted/30"
                >
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {formatDateTime(r.opened_at)}
                  </td>
                  <td className="px-4 py-3 font-medium">
                    <Link to={`/billing/claims/${r.claim_id}`} className="hover:underline">
                      {r.claim_id.slice(0, 8)} <ArrowRight className="ml-0.5 inline h-3 w-3" />
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-xs uppercase tracking-wider">
                    {r.denial_code}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatCents(r.amount_cents)}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      data-testid={`denial-status-${r.id}`}
                      className={`inline-flex items-center rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${denialStatusTone(r.status)}`}
                    >
                      {DENIAL_STATUS_LABELS[r.status] || r.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {r.assigned_to_id ? r.assigned_to_id.slice(0, 8) : "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Button
                      size="sm" variant="ghost"
                      onClick={() => setEditItem(r)}
                      data-testid={`denial-edit-${r.id}`}
                    >
                      Work
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <EditDenialDialog
        item={editItem}
        onClose={() => setEditItem(null)}
        onSaved={async () => { setEditItem(null); await refresh(); }}
      />
    </div>
  );
}

function Stat({ label, value, tone }) {
  const toneClass = tone === "warning"
    ? "text-warning"
    : tone === "primary"
      ? "text-primary"
      : "text-foreground";
  return (
    <div className="rounded-sm border border-border bg-card p-5">
      <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">{label}</div>
      <div className={`mt-1 font-display text-3xl font-medium tabular-nums ${toneClass}`}>
        {value}
      </div>
    </div>
  );
}

function EditDenialDialog({ item, onClose, onSaved }) {
  const open = !!item;
  const [status, setStatus] = useState("");
  const [assignee, setAssignee] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);

  useMemo(() => {
    if (item) {
      setStatus(item.status);
      setAssignee(item.assigned_to_id || "");
      setNotes(item.resolution_notes || "");
    }
  }, [item]);

  async function onSave() {
    setSaving(true);
    try {
      const body = {};
      if (status && status !== item.status) body.status = status;
      if ((assignee || null) !== (item.assigned_to_id || null)) {
        body.assigned_to_id = assignee.trim() || null;
      }
      if ((notes || null) !== (item.resolution_notes || null)) {
        body.resolution_notes = notes;
      }
      if (Object.keys(body).length === 0) {
        toast.info("Nothing changed");
        setSaving(false);
        return;
      }
      await updateDenialWorkItem(item.id, body);
      toast.success("Denial updated");
      await onSaved();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not update denial");
    } finally { setSaving(false); }
  }

  if (!item) return null;
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="denial-edit-dialog" className="rounded-sm sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">Work denial</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label>Status</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger data-testid="denial-edit-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(DENIAL_STATUS_LABELS).map(([v, l]) => (
                  <SelectItem key={v} value={v}>{l}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Assignee (user id)</Label>
            <Input
              value={assignee}
              onChange={(e) => setAssignee(e.target.value)}
              data-testid="denial-edit-assignee"
              placeholder="leave blank for unassigned"
            />
          </div>
          <div>
            <Label>Resolution notes</Label>
            <Textarea
              rows={3} value={notes}
              onChange={(e) => setNotes(e.target.value)}
              data-testid="denial-edit-notes"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={onSave} disabled={saving} data-testid="denial-edit-save" className="rounded-sm">
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
