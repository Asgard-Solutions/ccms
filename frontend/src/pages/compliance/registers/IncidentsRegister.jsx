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
import { fetchIncidents, createIncident, changeStatus } from "../api";
import { StatusChip, SeverityChip, EmptyState, SectionHeader, HistoryDialog } from "../common";
import { formatDateTime } from "../../../utils/time";
import { useReauth } from "../../../components/ReauthGate";

const STATUS_FILTERS = [
  { v: "all", l: "All" },
  { v: "triage", l: "Triage" },
  { v: "investigating", l: "Investigating" },
  { v: "contained", l: "Contained" },
  { v: "eradicated", l: "Eradicated" },
  { v: "recovered", l: "Recovered" },
  { v: "closed", l: "Closed" },
];

const SEVERITIES = ["low", "medium", "high", "critical"];
const INCIDENT_TYPES = ["phi_exposure", "auth_breach", "ransomware", "availability", "phishing", "data_loss", "configuration_drift"];

const NEXT_STATUSES = {
  triage: ["investigating", "closed"],
  investigating: ["contained", "closed"],
  contained: ["eradicated", "closed"],
  eradicated: ["recovered", "closed"],
  recovered: ["closed"],
  closed: [],
};

function CreateIncidentDialog({ open, onClose, onCreated }) {
  const [busy, setBusy] = useState(false);
  const { requestReauth } = useReauth();
  const [form, setForm] = useState({
    title: "", severity: "medium", incident_type: "availability",
    summary: "", detected_at: new Date().toISOString().slice(0, 16),
  });

  const submit = async () => {
    if (!form.title || !form.summary) {
      toast.error("Title and summary are required.");
      return;
    }
    const ok = await requestReauth({ reason: "Logging an incident requires re-authentication." });
    if (!ok) return;
    setBusy(true);
    try {
      const body = { ...form };
      if (body.detected_at && !body.detected_at.endsWith("Z")) {
        body.detected_at = new Date(body.detected_at).toISOString();
      }
      await createIncident(body);
      toast.success("Incident logged.");
      onCreated();
      onClose();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Failed to log incident.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="incident-create-dialog" className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">Log incident</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label htmlFor="inc-title">Title</Label>
            <Input id="inc-title" data-testid="incident-form-title" value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="inc-sev">Severity</Label>
              <Select value={form.severity} onValueChange={(v) => setForm({ ...form, severity: v })}>
                <SelectTrigger id="inc-sev" data-testid="incident-form-severity"><SelectValue /></SelectTrigger>
                <SelectContent>{SEVERITIES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div>
              <Label htmlFor="inc-type">Type</Label>
              <Select value={form.incident_type} onValueChange={(v) => setForm({ ...form, incident_type: v })}>
                <SelectTrigger id="inc-type" data-testid="incident-form-type"><SelectValue /></SelectTrigger>
                <SelectContent>{INCIDENT_TYPES.map((s) => <SelectItem key={s} value={s}>{s.replace("_", " ")}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </div>
          <div>
            <Label htmlFor="inc-detected">Detected at</Label>
            <Input id="inc-detected" data-testid="incident-form-detected" type="datetime-local"
              value={form.detected_at} onChange={(e) => setForm({ ...form, detected_at: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="inc-summary">Summary</Label>
            <Textarea id="inc-summary" data-testid="incident-form-summary" rows={4} value={form.summary}
              onChange={(e) => setForm({ ...form, summary: e.target.value })} />
          </div>
        </div>
        <DialogFooter>
          <Button data-testid="incident-form-cancel" variant="outline" onClick={onClose}>Cancel</Button>
          <Button data-testid="incident-form-save" disabled={busy} onClick={submit}>
            {busy ? "Saving…" : "Log incident"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function IncidentsRegister() {
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState(null);
  const [openCreate, setOpenCreate] = useState(false);
  const [historyId, setHistoryId] = useState(null);
  const { requestReauth } = useReauth();

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await fetchIncidents(filter === "all" ? {} : { status_filter: filter });
      setRows(data);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to load incidents");
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const onStatus = async (row, newStatus) => {
    const ok = await requestReauth({ reason: "Changing incident status requires re-authentication." });
    if (!ok) return;
    try {
      await changeStatus("incident", row.id, newStatus);
      toast.success(`Incident → ${newStatus}.`);
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not change status.");
    }
  };

  return (
    <section data-testid="incidents-register" className="space-y-4">
      <SectionHeader
        testid="incidents-header"
        title="Incidents"
        count={rows.length}
        action={
          <Button data-testid="incident-add-btn" size="sm" onClick={() => setOpenCreate(true)} className="gap-1">
            <Plus className="h-4 w-4" /> Log incident
          </Button>
        }
      />
      <div data-testid="incidents-filters" className="flex flex-wrap gap-1.5">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.v}
            data-testid={`incidents-filter-${f.v}`}
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
        <div data-testid="incidents-error" className="rounded-sm border border-destructive-soft bg-destructive-soft p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState testid="incidents-empty" label="No incidents in this view." />
      ) : (
        <div className="overflow-x-auto rounded-sm border border-border bg-card">
          <table className="w-full min-w-[820px] text-left text-sm">
            <thead className="border-b border-border text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Incident</th>
                <th className="px-4 py-3 font-medium">Severity</th>
                <th className="px-4 py-3 font-medium">Type</th>
                <th className="px-4 py-3 font-medium">Detected</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((i) => (
                <tr key={i.id} data-testid={`incident-row-${i.id}`} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 align-top">
                    <div className="text-sm text-foreground">{i.title}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{i.summary}</div>
                  </td>
                  <td className="px-4 py-3 align-top"><SeverityChip severity={i.severity} /></td>
                  <td className="px-4 py-3 align-top text-xs uppercase tracking-wider text-muted-foreground">{i.incident_type.replace("_", " ")}</td>
                  <td className="px-4 py-3 align-top text-xs text-muted-foreground">{formatDateTime(i.detected_at)}</td>
                  <td className="px-4 py-3 align-top">
                    <StatusChip status={i.status} testid={`incident-status-${i.id}`} />
                    {i.notification_required && (
                      <div data-testid={`incident-notify-${i.id}`} className="mt-1 text-[10px] uppercase tracking-wider text-warning">
                        Notification required
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center justify-end gap-2">
                      {NEXT_STATUSES[i.status]?.length > 0 && (
                        <Select onValueChange={(v) => onStatus(i, v)}>
                          <SelectTrigger data-testid={`incident-status-trigger-${i.id}`} className="h-8 w-[140px] text-xs">
                            <SelectValue placeholder="Change status" />
                          </SelectTrigger>
                          <SelectContent>
                            {NEXT_STATUSES[i.status].map((s) => (
                              <SelectItem key={s} value={s} data-testid={`incident-status-option-${i.id}-${s}`}>{s}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                      <Button
                        data-testid={`incident-history-${i.id}`}
                        variant="ghost"
                        size="sm"
                        onClick={() => setHistoryId(i.id)}
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

      <CreateIncidentDialog open={openCreate} onClose={() => setOpenCreate(false)} onCreated={load} />
      <HistoryDialog
        entityType="incident"
        entityId={historyId}
        open={!!historyId}
        onClose={() => setHistoryId(null)}
      />
    </section>
  );
}
