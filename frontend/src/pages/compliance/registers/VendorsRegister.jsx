import { useCallback, useEffect, useState } from "react";
import { Plus, History as HistoryIcon, ShieldAlert, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../../../components/ui/button";
import { Input } from "../../../components/ui/input";
import { Textarea } from "../../../components/ui/textarea";
import { Label } from "../../../components/ui/label";
import { Checkbox } from "../../../components/ui/checkbox";
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
import { fetchVendors, createVendor } from "../api";
import { StatusChip, EmptyState, SectionHeader, HistoryDialog, isOverdue } from "../common";
import { formatDate } from "../../../utils/time";

const ENVS = ["prod", "staging", "support"];
const REVIEW_STATUSES = ["pending", "approved", "rejected"];

function CreateVendorDialog({ open, onClose, onCreated }) {
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: "", service_provided: "", data_categories: "",
    environment: "prod", baa_required: false, baa_in_place: false,
    security_review_status: "pending", review_cadence_days: 365, notes: "",
  });

  const submit = async () => {
    if (!form.name || !form.service_provided) {
      toast.error("Name and service are required.");
      return;
    }
    setBusy(true);
    try {
      const body = {
        ...form,
        data_categories: form.data_categories.split(",").map((s) => s.trim()).filter(Boolean),
      };
      await createVendor(body);
      toast.success("Vendor recorded.");
      onCreated();
      onClose();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to add vendor.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="vendor-create-dialog" className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">New vendor</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label htmlFor="v-name">Vendor name</Label>
            <Input id="v-name" data-testid="vendor-form-name" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="v-service">Service provided</Label>
            <Input id="v-service" data-testid="vendor-form-service" value={form.service_provided}
              onChange={(e) => setForm({ ...form, service_provided: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="v-data">Data categories (comma-separated)</Label>
            <Input id="v-data" data-testid="vendor-form-data" value={form.data_categories}
              onChange={(e) => setForm({ ...form, data_categories: e.target.value })}
              placeholder="PHI, payment, audit logs" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="v-env">Environment</Label>
              <Select value={form.environment} onValueChange={(v) => setForm({ ...form, environment: v })}>
                <SelectTrigger id="v-env" data-testid="vendor-form-env"><SelectValue /></SelectTrigger>
                <SelectContent>{ENVS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div>
              <Label htmlFor="v-review">Review status</Label>
              <Select value={form.security_review_status} onValueChange={(v) => setForm({ ...form, security_review_status: v })}>
                <SelectTrigger id="v-review" data-testid="vendor-form-review"><SelectValue /></SelectTrigger>
                <SelectContent>{REVIEW_STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox data-testid="vendor-form-baa-req"
                checked={form.baa_required}
                onCheckedChange={(v) => setForm({ ...form, baa_required: !!v })} />
              BAA required
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox data-testid="vendor-form-baa-in"
                checked={form.baa_in_place}
                onCheckedChange={(v) => setForm({ ...form, baa_in_place: !!v })} />
              BAA executed
            </label>
          </div>
          <div>
            <Label htmlFor="v-cadence">Review cadence (days)</Label>
            <Input id="v-cadence" data-testid="vendor-form-cadence" type="number" min={30}
              value={form.review_cadence_days}
              onChange={(e) => setForm({ ...form, review_cadence_days: Number(e.target.value) || 365 })} />
          </div>
          <div>
            <Label htmlFor="v-notes">Notes</Label>
            <Textarea id="v-notes" data-testid="vendor-form-notes" rows={2} value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </div>
        </div>
        <DialogFooter>
          <Button data-testid="vendor-form-cancel" variant="outline" onClick={onClose}>Cancel</Button>
          <Button data-testid="vendor-form-save" disabled={busy} onClick={submit}>
            {busy ? "Saving…" : "Save vendor"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function VendorsRegister() {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState(null);
  const [openCreate, setOpenCreate] = useState(false);
  const [historyId, setHistoryId] = useState(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await fetchVendors();
      setRows(data);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to load vendors");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <section data-testid="vendors-register" className="space-y-4">
      <SectionHeader
        testid="vendors-header"
        title="Vendors"
        count={rows.length}
        action={
          <Button data-testid="vendor-add-btn" size="sm" onClick={() => setOpenCreate(true)} className="gap-1">
            <Plus className="h-4 w-4" /> New vendor
          </Button>
        }
      />

      {error && (
        <div data-testid="vendors-error" className="rounded-sm border border-destructive-soft bg-destructive-soft p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState testid="vendors-empty" label="No vendors registered yet." />
      ) : (
        <div className="overflow-x-auto rounded-sm border border-border bg-card">
          <table className="w-full min-w-[820px] text-left text-sm">
            <thead className="border-b border-border text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Vendor</th>
                <th className="px-4 py-3 font-medium">Data</th>
                <th className="px-4 py-3 font-medium">Env</th>
                <th className="px-4 py-3 font-medium">BAA</th>
                <th className="px-4 py-3 font-medium">Next review</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((v) => {
                const baaOk = !v.baa_required || v.baa_in_place;
                const reviewDue = isOverdue(v.next_review_at);
                return (
                  <tr key={v.id} data-testid={`vendor-row-${v.id}`} className="border-b border-border last:border-0">
                    <td className="px-4 py-3 align-top">
                      <div className="text-sm text-foreground">{v.name}</div>
                      <div className="mt-0.5 text-xs text-muted-foreground line-clamp-1">{v.service_provided}</div>
                    </td>
                    <td className="px-4 py-3 align-top text-xs text-muted-foreground">
                      {v.data_categories?.join(", ") || "—"}
                    </td>
                    <td className="px-4 py-3 align-top text-xs uppercase tracking-wider text-muted-foreground">{v.environment}</td>
                    <td className="px-4 py-3 align-top">
                      {v.baa_required ? (
                        <span data-testid={`vendor-baa-${v.id}`} className={`inline-flex items-center gap-1 rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
                          baaOk ? "bg-primary/10 text-primary" : "bg-destructive-soft text-destructive"
                        }`}>
                          {baaOk ? <ShieldCheck className="h-3 w-3" /> : <ShieldAlert className="h-3 w-3" />}
                          {baaOk ? "Executed" : "Missing"}
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">N/A</span>
                      )}
                    </td>
                    <td className="px-4 py-3 align-top text-xs">
                      <span className={reviewDue ? "text-destructive font-semibold" : "text-muted-foreground"}>
                        {v.next_review_at ? formatDate(v.next_review_at) : "—"}
                        {reviewDue && " · due"}
                      </span>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <StatusChip status={v.status} testid={`vendor-status-${v.id}`} />
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex items-center justify-end gap-2">
                        <Button
                          data-testid={`vendor-history-${v.id}`}
                          variant="ghost"
                          size="sm"
                          onClick={() => setHistoryId(v.id)}
                        >
                          <HistoryIcon className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <CreateVendorDialog open={openCreate} onClose={() => setOpenCreate(false)} onCreated={load} />
      <HistoryDialog
        entityType="vendor"
        entityId={historyId}
        open={!!historyId}
        onClose={() => setHistoryId(null)}
      />
    </section>
  );
}
