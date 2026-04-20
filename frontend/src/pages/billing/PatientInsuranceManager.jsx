import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ShieldCheck, Plus, Trash2, Pencil } from "lucide-react";
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
  createPolicy,
  deactivatePolicy,
  updatePolicy,
  usePatientPolicies,
  usePayers,
} from "./useBillingAdmin";
import { formatDate } from "../../utils/time";

const RANKS = [
  { v: "primary", l: "Primary" },
  { v: "secondary", l: "Secondary" },
  { v: "tertiary", l: "Tertiary" },
];
const RELATIONSHIPS = [
  { v: "self", l: "Self" },
  { v: "spouse", l: "Spouse" },
  { v: "child", l: "Child" },
  { v: "other", l: "Other" },
];

/**
 * PatientInsuranceManager — embedded card on PatientDetail letting
 * operators add / edit / deactivate insurance policies for a patient.
 * Scoped by tenant automatically.
 */
export default function PatientInsuranceManager({ patientId }) {
  const { rows, loading, refresh } = usePatientPolicies(patientId);
  const { rows: payers } = usePayers({ activeOnly: true });
  const [editing, setEditing] = useState(null); // null | {} for new

  const activePrimary = useMemo(
    () => rows.find((p) => p.rank === "primary" && p.status === "active"),
    [rows],
  );

  async function onDeactivate(policy) {
    if (!window.confirm(
      `Deactivate ${policy.rank} policy for ${policy.subscriber_name}?`,
    )) return;
    try {
      await deactivatePolicy(policy.id);
      toast.success("Policy deactivated");
      await refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not deactivate");
    }
  }

  return (
    <section
      data-testid="patient-insurance-card"
      className="rounded-sm border border-border bg-card p-6"
    >
      <header className="mb-4 flex items-baseline justify-between">
        <div>
          <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            Billing setup
          </div>
          <h2 className="mt-1 font-display text-2xl font-medium tracking-tight">
            Insurance
          </h2>
        </div>
        <Button
          onClick={() => setEditing({})}
          data-testid="insurance-add-btn"
          className="rounded-sm"
          size="sm"
        >
          <Plus className="mr-1 h-4 w-4" /> Add policy
        </Button>
      </header>

      {loading ? (
        <Skeleton className="h-16 w-full rounded-sm" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No insurance policies on file. Patient will be billed self-pay
          by default.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((p) => {
            const payer = payers.find((x) => x.id === p.payer_id);
            return (
              <li
                key={p.id}
                data-testid={`policy-row-${p.id}`}
                className="flex items-center justify-between gap-3 py-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4 text-primary" />
                    <span className="font-medium">
                      {payer?.name || "Unknown payer"}
                    </span>
                    <span
                      className={`rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${p.status === "active" ? "bg-success-soft text-success" : "bg-muted text-muted-foreground"}`}
                    >
                      {p.rank} · {p.status}
                    </span>
                  </div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {p.subscriber_name} · member {p.member_id}
                    {p.group_number ? ` · group ${p.group_number}` : ""}
                    {p.effective_date ? ` · effective ${formatDate(p.effective_date)}` : ""}
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost" size="sm"
                    onClick={() => setEditing(p)}
                    data-testid={`policy-edit-${p.id}`}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  {p.status === "active" && (
                    <Button
                      variant="ghost" size="sm"
                      onClick={() => onDeactivate(p)}
                      data-testid={`policy-deactivate-${p.id}`}
                      className="text-destructive hover:bg-destructive/10"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {rows.length > 0 && !activePrimary && (
        <p className="mt-3 rounded-sm border border-warning/30 bg-warning-soft p-2 text-xs text-warning">
          No active primary policy — insurance billing will be blocked
          until one is added.
        </p>
      )}

      {editing !== null && (
        <PolicyDialog
          open
          policy={editing.id ? editing : null}
          patientId={patientId}
          payers={payers}
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

function PolicyDialog({ open, policy, patientId, payers, onClose, onSaved }) {
  const isEdit = !!policy;
  const [form, setForm] = useState(() => ({
    payer_id: policy?.payer_id || "",
    rank: policy?.rank || "primary",
    subscriber_name: policy?.subscriber_name || "",
    relationship_to_subscriber: policy?.relationship_to_subscriber || "self",
    member_id: policy?.member_id || "",
    group_number: policy?.group_number || "",
    effective_date: policy?.effective_date || "",
    termination_date: policy?.termination_date || "",
    notes: policy?.notes || "",
  }));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    // reset when dialog is opened fresh on a different policy
    if (!open) return;
    setSaving(false);
  }, [open]);

  function patch(k, v) { setForm((f) => ({ ...f, [k]: v })); }

  async function onSubmit() {
    if (!form.payer_id) return toast.error("Select a payer");
    if (!form.subscriber_name.trim()) return toast.error("Subscriber name required");
    if (!form.member_id.trim()) return toast.error("Member ID required");

    const payload = {
      payer_id: form.payer_id,
      rank: form.rank,
      subscriber_name: form.subscriber_name.trim(),
      relationship_to_subscriber: form.relationship_to_subscriber,
      member_id: form.member_id.trim(),
      group_number: form.group_number.trim() || null,
      effective_date: form.effective_date || null,
      termination_date: form.termination_date || null,
      notes: form.notes.trim() || null,
    };
    setSaving(true);
    try {
      if (isEdit) {
        await updatePolicy(policy.id, payload);
        toast.success("Policy updated");
      } else {
        await createPolicy({ ...payload, patient_id: patientId });
        toast.success("Policy added");
      }
      onSaved?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not save policy");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        data-testid="policy-dialog"
        className="max-h-[85vh] overflow-y-auto rounded-sm sm:max-w-xl"
      >
        <DialogHeader>
          <DialogTitle className="font-display">
            {isEdit ? "Edit policy" : "Add insurance policy"}
          </DialogTitle>
          <DialogDescription>
            Billing will prefer the active primary policy at charge
            capture time.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <Label htmlFor="pol-payer">Payer</Label>
            <Select value={form.payer_id} onValueChange={(v) => patch("payer_id", v)}>
              <SelectTrigger id="pol-payer" data-testid="pol-payer">
                <SelectValue placeholder="Select payer" />
              </SelectTrigger>
              <SelectContent>
                {payers.map((p) => (
                  <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="pol-rank">Rank</Label>
            <Select value={form.rank} onValueChange={(v) => patch("rank", v)}>
              <SelectTrigger id="pol-rank"><SelectValue /></SelectTrigger>
              <SelectContent>
                {RANKS.map((r) => (
                  <SelectItem key={r.v} value={r.v}>{r.l}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="pol-rel">Relationship</Label>
            <Select
              value={form.relationship_to_subscriber}
              onValueChange={(v) => patch("relationship_to_subscriber", v)}
            >
              <SelectTrigger id="pol-rel"><SelectValue /></SelectTrigger>
              <SelectContent>
                {RELATIONSHIPS.map((r) => (
                  <SelectItem key={r.v} value={r.v}>{r.l}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="pol-sub">Subscriber name</Label>
            <Input
              id="pol-sub" data-testid="pol-subscriber"
              value={form.subscriber_name}
              onChange={(e) => patch("subscriber_name", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="pol-member">Member ID</Label>
            <Input
              id="pol-member" data-testid="pol-member"
              value={form.member_id}
              onChange={(e) => patch("member_id", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="pol-group">Group #</Label>
            <Input
              id="pol-group"
              value={form.group_number}
              onChange={(e) => patch("group_number", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="pol-eff">Effective date</Label>
            <Input
              id="pol-eff" type="date"
              value={form.effective_date}
              onChange={(e) => patch("effective_date", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="pol-term">Termination date</Label>
            <Input
              id="pol-term" type="date"
              value={form.termination_date}
              onChange={(e) => patch("termination_date", e.target.value)}
            />
          </div>
          <div className="sm:col-span-2">
            <Label htmlFor="pol-notes">Notes</Label>
            <Textarea
              id="pol-notes" rows={2}
              value={form.notes}
              onChange={(e) => patch("notes", e.target.value)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            onClick={onSubmit} disabled={saving}
            data-testid="pol-save"
            className="rounded-sm"
          >
            {saving ? "Saving…" : isEdit ? "Save changes" : "Add policy"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
