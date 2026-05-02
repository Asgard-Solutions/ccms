import { useCallback, useEffect, useState } from "react";
import { Plus, History as HistoryIcon, Lock, Unlock, ShieldCheck } from "lucide-react";
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
import { fetchEvidence, createEvidence, setEvidenceLegalHold } from "../api";
import { EmptyState, SectionHeader, HistoryDialog, todayIso, inDaysIso } from "../common";
import { formatDate, formatDateTime } from "../../../utils/time";
import { useReauth } from "../../../components/ReauthGate";

const TYPES = [
  "audit_log", "access_review", "config_snapshot", "backup_test", "security_alert",
  "export_log", "vuln_scan", "incident", "key_rotation", "secret_rotation",
  "dr_exercise", "policy_attestation", "vendor_review", "manual_upload",
];

const TYPE_FILTERS = [{ v: "all", l: "All" }, ...TYPES.map((t) => ({ v: t, l: t.replace("_", " ") }))];

function CreateEvidenceDialog({ open, onClose, onCreated }) {
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    evidence_type: "manual_upload",
    source_system: "",
    source_reference: "",
    content_summary: "",
    coverage_period_start: inDaysIso(-30),
    coverage_period_end: todayIso(),
    retention_days: 2555,
    storage_artifact_path: "",
  });

  const submit = async () => {
    if (!form.source_system || !form.source_reference || !form.content_summary) {
      toast.error("Source system, reference, and summary are required.");
      return;
    }
    setBusy(true);
    try {
      const body = { ...form };
      if (!body.storage_artifact_path) delete body.storage_artifact_path;
      await createEvidence(body);
      toast.success("Evidence captured. Integrity hash sealed.");
      onCreated();
      onClose();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to capture evidence.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="evidence-create-dialog" className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">Capture evidence</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="ev-type">Evidence type</Label>
              <Select value={form.evidence_type} onValueChange={(v) => setForm({ ...form, evidence_type: v })}>
                <SelectTrigger id="ev-type" data-testid="evidence-form-type"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {TYPES.map((t) => <SelectItem key={t} value={t}>{t.replace("_", " ")}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label htmlFor="ev-retention">Retention (days)</Label>
              <Input id="ev-retention" data-testid="evidence-form-retention" type="number" min={1}
                value={form.retention_days}
                onChange={(e) => setForm({ ...form, retention_days: Number(e.target.value) || 365 })} />
            </div>
          </div>
          <div>
            <Label htmlFor="ev-source">Source system</Label>
            <Input id="ev-source" data-testid="evidence-form-source" value={form.source_system}
              onChange={(e) => setForm({ ...form, source_system: e.target.value })}
              placeholder="e.g. ccms.audit, jamf, terraform-state" />
          </div>
          <div>
            <Label htmlFor="ev-ref">Source reference</Label>
            <Input id="ev-ref" data-testid="evidence-form-reference" value={form.source_reference}
              onChange={(e) => setForm({ ...form, source_reference: e.target.value })}
              placeholder="URL, file path, audit id…" />
          </div>
          <div>
            <Label htmlFor="ev-summary">Summary</Label>
            <Textarea id="ev-summary" data-testid="evidence-form-summary" rows={3} value={form.content_summary}
              onChange={(e) => setForm({ ...form, content_summary: e.target.value })} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="ev-start">Coverage start</Label>
              <Input id="ev-start" data-testid="evidence-form-cov-start" type="date"
                value={form.coverage_period_start.slice(0, 10)}
                onChange={(e) => setForm({ ...form, coverage_period_start: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="ev-end">Coverage end</Label>
              <Input id="ev-end" data-testid="evidence-form-cov-end" type="date"
                value={form.coverage_period_end.slice(0, 10)}
                onChange={(e) => setForm({ ...form, coverage_period_end: e.target.value })} />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button data-testid="evidence-form-cancel" variant="outline" onClick={onClose}>Cancel</Button>
          <Button data-testid="evidence-form-save" disabled={busy} onClick={submit}>
            {busy ? "Saving…" : "Capture evidence"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function EvidenceRegister() {
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState(null);
  const [openCreate, setOpenCreate] = useState(false);
  const [historyId, setHistoryId] = useState(null);
  const { requestReauth } = useReauth();

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await fetchEvidence(filter === "all" ? {} : { evidence_type: filter });
      setRows(data);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to load evidence");
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const onLegalHold = async (row) => {
    const ok = await requestReauth({ reason: "Toggling legal hold requires re-authentication." });
    if (!ok) return;
    try {
      await setEvidenceLegalHold(row.id, !row.legal_hold);
      toast.success(`Legal hold ${row.legal_hold ? "released" : "applied"}.`);
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to update legal hold.");
    }
  };

  return (
    <section data-testid="evidence-register" className="space-y-4">
      <SectionHeader
        testid="evidence-header"
        title="Evidence"
        count={rows.length}
        action={
          <Button data-testid="evidence-add-btn" size="sm" onClick={() => setOpenCreate(true)} className="gap-1">
            <Plus className="h-4 w-4" /> Capture evidence
          </Button>
        }
      />

      <div className="flex items-start gap-2 rounded-sm border border-border bg-secondary/40 p-3 text-xs text-muted-foreground">
        <ShieldCheck className="mt-0.5 h-4 w-4 flex-none text-primary" />
        <span>
          Each evidence row is tamper-evident — a SHA-256 over the canonical reference + summary +
          coverage window is sealed at creation. Legal hold blocks retention-based deletion.
        </span>
      </div>

      <div data-testid="evidence-filters" className="flex flex-wrap gap-1.5">
        {TYPE_FILTERS.map((f) => (
          <button
            key={f.v}
            data-testid={`evidence-filter-${f.v}`}
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
        <div data-testid="evidence-error" className="rounded-sm border border-destructive-soft bg-destructive-soft p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState testid="evidence-empty" label="No evidence rows in this view." />
      ) : (
        <div className="overflow-x-auto rounded-sm border border-border bg-card">
          <table className="w-full min-w-[860px] text-left text-sm">
            <thead className="border-b border-border text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Source</th>
                <th className="px-4 py-3 font-medium">Type</th>
                <th className="px-4 py-3 font-medium">Coverage</th>
                <th className="px-4 py-3 font-medium">Integrity hash</th>
                <th className="px-4 py-3 font-medium">Retention</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id} data-testid={`evidence-row-${e.id}`} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 align-top">
                    <div className="text-sm text-foreground">{e.source_system}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground line-clamp-1">{e.source_reference}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{e.content_summary}</div>
                  </td>
                  <td className="px-4 py-3 align-top text-xs uppercase tracking-wider text-muted-foreground">{e.evidence_type.replace("_", " ")}</td>
                  <td className="px-4 py-3 align-top text-xs text-muted-foreground">
                    <div>{formatDate(e.coverage_period_start)}</div>
                    <div>→ {formatDate(e.coverage_period_end)}</div>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <code data-testid={`evidence-hash-${e.id}`} className="font-mono text-[10px] text-muted-foreground">
                      {e.integrity_sha256.slice(0, 12)}…{e.integrity_sha256.slice(-6)}
                    </code>
                  </td>
                  <td className="px-4 py-3 align-top text-xs text-muted-foreground">
                    <div>{formatDate(e.retention_until)}</div>
                    {e.legal_hold && (
                      <span data-testid={`evidence-legalhold-${e.id}`} className="mt-1 inline-flex items-center gap-1 rounded-sm bg-warning-soft px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-warning">
                        <Lock className="h-3 w-3" /> Legal hold
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        data-testid={`evidence-legalhold-toggle-${e.id}`}
                        variant="ghost"
                        size="sm"
                        onClick={() => onLegalHold(e)}
                        title={e.legal_hold ? "Release legal hold" : "Apply legal hold"}
                      >
                        {e.legal_hold ? <Unlock className="h-3.5 w-3.5" /> : <Lock className="h-3.5 w-3.5" />}
                      </Button>
                      <Button
                        data-testid={`evidence-history-${e.id}`}
                        variant="ghost"
                        size="sm"
                        onClick={() => setHistoryId(e.id)}
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

      <CreateEvidenceDialog open={openCreate} onClose={() => setOpenCreate(false)} onCreated={load} />
      <HistoryDialog
        entityType="evidence"
        entityId={historyId}
        open={!!historyId}
        onClose={() => setHistoryId(null)}
      />
    </section>
  );
}
