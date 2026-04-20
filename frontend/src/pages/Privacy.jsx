import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Scale,
  Inbox,
  FileText,
  AlertTriangle,
  ClipboardList,
  Database,
} from "lucide-react";
import { api } from "../api/client";
import { formatApiError } from "../api/client";
import { formatDateTime, relativeFromNow } from "../utils/time";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Skeleton } from "../components/ui/skeleton";

const REQUEST_TYPES = ["export", "delete", "correct", "restrict", "opt_out"];
const STATUS_FLOW = {
  received: ["in_review", "rejected", "withdrawn"],
  in_review: ["approved", "rejected", "withdrawn"],
  approved: ["fulfilled", "rejected", "withdrawn"],
  fulfilled: [],
  rejected: [],
  withdrawn: [],
};
const STATUS_CHIP = {
  received: "bg-primary/10 text-primary",
  in_review: "bg-warning-soft text-warning",
  approved: "bg-info-soft text-info",
  fulfilled: "bg-success-soft text-success",
  rejected: "bg-destructive-soft text-destructive",
  withdrawn: "bg-muted text-muted-foreground",
};

function StatusChip({ s }) {
  return (
    <span
      className={`inline-block rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
        STATUS_CHIP[s] || "bg-muted"
      }`}
    >
      {s}
    </span>
  );
}

function InventoryTab() {
  const [data, setData] = useState(null);
  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/privacy/data-inventory");
        setData(data);
      } catch (e) {
        toast.error(formatApiError(e));
      }
    })();
  }, []);
  if (!data) return <Skeleton className="h-80 w-full" />;
  return (
    <div data-testid="inventory-tab" className="space-y-4">
      <div className="rounded-sm border border-border bg-background p-4 text-xs text-muted-foreground">
        <div className="font-semibold uppercase tracking-[0.15em] text-foreground">
          Retention settings
        </div>
        <div className="mt-1">
          Patient records: {data.retention_settings?.patient_retention_years} years after soft-delete ·
          Audit log: {data.retention_settings?.audit_retention_years} years
        </div>
        <div className="mt-1 italic">{data.retention_settings?.notes}</div>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {data.categories.map((c) => (
          <div
            key={c.id}
            data-testid={`inventory-${c.id}`}
            className="rounded-sm border border-border bg-card p-4"
          >
            <div className="flex items-center justify-between">
              <div>
                <div className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
                  {c.ccpa_category}
                </div>
                <div className="font-display text-lg font-medium">{c.name}</div>
              </div>
              {c.phi && (
                <span className="rounded-sm bg-warning-soft px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-warning">
                  PHI
                </span>
              )}
            </div>
            <dl className="mt-3 space-y-2 text-xs">
              <div>
                <dt className="font-semibold text-foreground">Collected</dt>
                <dd className="text-muted-foreground">{c.collected.join(", ")}</dd>
              </div>
              <div>
                <dt className="font-semibold text-foreground">Purpose</dt>
                <dd className="text-muted-foreground">{c.purpose}</dd>
              </div>
              <div>
                <dt className="font-semibold text-foreground">Access</dt>
                <dd className="text-muted-foreground">{c.access_roles.join(", ")}</dd>
              </div>
              <div>
                <dt className="font-semibold text-foreground">Retention</dt>
                <dd className="text-muted-foreground">{c.retention_default}</dd>
              </div>
              <div>
                <dt className="font-semibold text-foreground">At rest</dt>
                <dd className="text-muted-foreground">{c.encrypted_at_rest}</dd>
              </div>
            </dl>
          </div>
        ))}
      </div>
    </div>
  );
}

function NewRequestForm({ onCreated }) {
  const [requestType, setRequestType] = useState("export");
  const [subjectUserId, setSubjectUserId] = useState("");
  const [subjectPatientId, setSubjectPatientId] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!subjectUserId.trim()) {
      toast.error("Subject user id is required");
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await api.post("/privacy/requests", {
        request_type: requestType,
        subject_user_id: subjectUserId.trim(),
        subject_patient_id: subjectPatientId.trim() || null,
        notes: notes.trim(),
      });
      toast.success(`Request ${data.id.slice(0, 8)} received`);
      setSubjectUserId("");
      setSubjectPatientId("");
      setNotes("");
      onCreated();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      data-testid="new-request-form"
      className="space-y-3 rounded-sm border border-border bg-card p-4"
    >
      <div className="flex items-center gap-2 text-foreground">
        <Inbox className="h-4 w-4" />
        <span className="font-display text-lg font-medium">Log a new privacy request</span>
      </div>
      <div className="grid gap-3 md:grid-cols-4">
        <div>
          <Label className="text-[11px]">Type</Label>
          <select
            data-testid="new-request-type"
            value={requestType}
            onChange={(e) => setRequestType(e.target.value)}
            className="h-9 w-full rounded-sm border border-border bg-card px-2 text-sm"
          >
            {REQUEST_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label className="text-[11px]">Subject user id</Label>
          <Input
            data-testid="new-request-subject"
            value={subjectUserId}
            onChange={(e) => setSubjectUserId(e.target.value)}
            placeholder="UUID"
            className="h-9 rounded-sm font-mono text-xs"
          />
        </div>
        <div>
          <Label className="text-[11px]">Subject patient id (optional)</Label>
          <Input
            data-testid="new-request-patient"
            value={subjectPatientId}
            onChange={(e) => setSubjectPatientId(e.target.value)}
            placeholder="UUID"
            className="h-9 rounded-sm font-mono text-xs"
          />
        </div>
        <div className="self-end">
          <Button
            type="submit"
            data-testid="new-request-submit"
            disabled={submitting}
            className="h-9 w-full rounded-sm bg-primary text-primary-foreground hover:bg-[var(--primary-hover)]"
          >
            {submitting ? "Logging…" : "Log request"}
          </Button>
        </div>
      </div>
      <div>
        <Label className="text-[11px]">Intake notes (no PHI)</Label>
        <Input
          data-testid="new-request-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Verification method, requester channel, etc."
          className="h-9 rounded-sm"
        />
      </div>
    </form>
  );
}

function RequestRow({ r, onChanged }) {
  const [busy, setBusy] = useState(false);
  const nextStates = STATUS_FLOW[r.status] || [];

  async function transition(next) {
    const response_notes = window.prompt(
      `Transition to "${next}". Add a response note (no PHI):`,
      r.response_notes || "",
    );
    if (response_notes === null) return;
    setBusy(true);
    try {
      await api.patch(`/privacy/requests/${r.id}`, {
        status: next,
        response_notes,
      });
      toast.success(`Moved to ${next}`);
      onChanged();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function fulfillDelete() {
    if (!window.confirm("Fulfil this delete request? This requires recent re-authentication.")) return;
    setBusy(true);
    try {
      await api.post(`/privacy/requests/${r.id}/fulfill-delete`);
      toast.success("Request fulfilled — complete the patient soft-delete separately.");
      onChanged();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr
      data-testid={`request-row-${r.id}`}
      className="border-b border-border last:border-0 align-top"
    >
      <td className="px-3 py-3">
        <div>{formatDateTime(r.created_at)}</div>
        <div className="text-[11px] text-muted-foreground">{relativeFromNow(r.created_at)}</div>
      </td>
      <td className="px-3 py-3">
        <div className="font-mono text-xs">{r.request_type}</div>
        <div className="mt-1">
          <StatusChip s={r.status} />
        </div>
      </td>
      <td className="px-3 py-3 font-mono text-[11px] text-muted-foreground">
        <div>subj: {r.subject_user_id?.slice(0, 8)}…</div>
        {r.subject_patient_id && <div>pat: {r.subject_patient_id.slice(0, 8)}…</div>}
      </td>
      <td className="px-3 py-3 text-xs text-muted-foreground">
        <div className="font-semibold text-foreground">Notes</div>
        <div>{r.notes || "—"}</div>
        {r.response_notes && (
          <>
            <div className="mt-2 font-semibold text-foreground">Response</div>
            <div>{r.response_notes}</div>
          </>
        )}
      </td>
      <td className="px-3 py-3">
        <div className="flex flex-wrap gap-1">
          {nextStates.map((ns) => (
            <button
              key={ns}
              data-testid={`transition-${r.id}-${ns}`}
              onClick={() => transition(ns)}
              disabled={busy}
              className="rounded-sm border border-border bg-card px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground hover:bg-muted"
            >
              → {ns}
            </button>
          ))}
          {r.request_type === "delete" && r.subject_patient_id && r.status === "approved" && (
            <button
              data-testid={`fulfill-delete-${r.id}`}
              onClick={fulfillDelete}
              disabled={busy}
              className="rounded-sm border border-destructive bg-destructive-soft px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-destructive"
            >
              Fulfil delete
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

function RequestsTab() {
  const [rows, setRows] = useState(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const fetchRows = useMemo(
    () => async () => {
      setRows(null);
      try {
        const params = {};
        if (statusFilter) params.status = statusFilter;
        if (typeFilter) params.request_type = typeFilter;
        const { data } = await api.get("/privacy/requests", { params });
        setRows(data);
      } catch (e) {
        toast.error(formatApiError(e));
        setRows([]);
      }
    },
    [statusFilter, typeFilter],
  );
  useEffect(() => {
    fetchRows();
  }, [fetchRows]);

  return (
    <div data-testid="requests-tab" className="space-y-4">
      <NewRequestForm onCreated={fetchRows} />

      <div className="flex flex-wrap items-center gap-2">
        <select
          data-testid="filter-status"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="h-9 rounded-sm border border-border bg-card px-2 text-sm"
        >
          <option value="">All statuses</option>
          {Object.keys(STATUS_FLOW).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          data-testid="filter-type"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="h-9 rounded-sm border border-border bg-card px-2 text-sm"
        >
          <option value="">All types</option>
          {REQUEST_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <span className="ml-auto text-xs text-muted-foreground">
          {rows ? `${rows.length} requests` : "loading…"}
        </span>
      </div>

      {rows === null ? (
        <Skeleton className="h-48 rounded-sm" />
      ) : rows.length === 0 ? (
        <div className="rounded-sm border border-dashed border-border bg-card p-12 text-center">
          <ClipboardList className="mx-auto h-10 w-10 text-muted-foreground/70" />
          <p className="mt-3 font-display text-base">No privacy requests logged yet.</p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-sm border border-border bg-card">
          <table className="w-full min-w-[880px] text-left text-sm">
            <thead className="border-b border-border bg-background text-[11px] uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-3 py-3 font-medium">Created</th>
                <th className="px-3 py-3 font-medium">Type / status</th>
                <th className="px-3 py-3 font-medium">Subject</th>
                <th className="px-3 py-3 font-medium">Notes</th>
                <th className="px-3 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <RequestRow key={r.id} r={r} onChanged={fetchRows} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const TABS = [
  { v: "requests", l: "Requests", icon: Inbox },
  { v: "inventory", l: "Data inventory", icon: Database },
];

export default function Privacy() {
  const [tab, setTab] = useState("requests");
  return (
    <div data-testid="privacy-page" className="space-y-8 animate-in fade-in duration-300">
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Compliance
        </span>
        <h1 className="mt-2 font-display text-4xl font-medium tracking-tight">
          Privacy operations
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
          Admin intake for CCPA-style data-subject requests (access, correction,
          deletion, restriction, opt-out) and a structured inventory of the
          data categories CCMS handles.
        </p>
        <div
          data-testid="privacy-disclaimer"
          className="mt-4 flex items-start gap-2 rounded-sm border border-border bg-warning-soft p-3 text-xs text-warning"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
          <span>
            Intake notes and response notes must <strong>not</strong> contain
            PHI — they're stored unencrypted for the workflow. PHI-bearing
            fulfilment actions (patient soft-delete, full PHI export) run
            through the existing <code>/api/patients/*</code> endpoints, which
            require re-authentication and write PHI-aware audit rows.
          </span>
        </div>
      </header>

      <div className="flex gap-2">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.v}
              data-testid={`privacy-tab-${t.v}`}
              onClick={() => setTab(t.v)}
              className={`inline-flex items-center gap-2 rounded-sm px-3 py-1.5 text-sm font-medium transition-colors ${
                tab === t.v
                  ? "bg-primary text-primary-foreground"
                  : "border border-border bg-card text-muted-foreground hover:bg-muted"
              }`}
            >
              <Icon className="h-4 w-4" />
              {t.l}
            </button>
          );
        })}
      </div>

      {tab === "requests" ? <RequestsTab /> : <InventoryTab />}

      <div className="rounded-sm border border-border bg-card p-4 text-xs text-muted-foreground">
        <div className="flex items-center gap-2 text-foreground">
          <FileText className="h-4 w-4" />
          <span className="font-display text-sm font-medium">Reference</span>
        </div>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li><code>/app/memory/PRIVACY_AND_RETENTION.md</code> — full workflow and retention model</li>
          <li><code>/app/memory/COMPLIANCE_BASELINE.md</code> — CCPA / SOC 2 / ISO 27001 control mapping</li>
          <li><code>/app/memory/HIPAA_COMPLIANCE.md</code> — HIPAA technical safeguards</li>
        </ul>
      </div>

      <div className="hidden">
        <Scale className="h-4 w-4" />
      </div>
    </div>
  );
}
