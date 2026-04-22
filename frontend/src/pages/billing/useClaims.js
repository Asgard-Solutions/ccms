import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

// ---------------------------------------------------------------------------
// Claims queue + detail + scrubber hooks
// ---------------------------------------------------------------------------
export const CLAIM_STATUS_LABELS = {
  draft: "Draft",
  validation_failed: "Needs fixes",
  ready: "Ready to submit",
  submitted: "Submitted",
  accepted: "Accepted",
  rejected: "Rejected",
  paid: "Paid",
  partially_paid: "Partially paid",
  denied: "Denied",
  appealed: "Appealed",
  closed: "Closed",
};

/** Semantic tone for claim status pills. */
export function claimStatusTone(status) {
  switch (status) {
    case "paid":
    case "accepted":
      return "bg-success-soft text-success";
    case "ready":
    case "submitted":
      return "bg-primary/10 text-primary";
    case "validation_failed":
    case "rejected":
    case "denied":
      return "bg-destructive/10 text-destructive";
    case "partially_paid":
    case "appealed":
      return "bg-warning-soft text-warning";
    default:
      return "bg-muted text-muted-foreground";
  }
}

export function useClaims({ status = null, patientId = null } = {}) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (status) params.status = status;
      if (patientId) params.patient_id = patientId;
      const { data } = await api.get("/billing/claims", { params });
      setRows(data || []);
    } finally { setLoading(false); }
  }, [status, patientId]);

  useEffect(() => { load(); }, [load]);
  return { rows, loading, refresh: load };
}

export async function createClaimFromInvoice(invoiceId) {
  const { data } = await api.post(`/billing/claims/from-invoice/${invoiceId}`);
  return data;
}

export async function fetchClaimDetail(claimId) {
  const { data } = await api.get(`/billing/claims/${claimId}/detail`);
  return data;
}

export async function validateClaim(claimId) {
  const { data } = await api.post(`/billing/claims/${claimId}/validate`);
  return data;
}

export async function submitClaim(claimId) {
  const { data } = await api.post(`/billing/claims/${claimId}/submit`);
  return data;
}

export async function updateClaimHeader(claimId, patch) {
  const { data } = await api.patch(`/billing/claims/${claimId}/header`, patch);
  return data;
}

export async function replaceClaimDiagnoses(claimId, diagnoses) {
  const { data } = await api.patch(
    `/billing/claims/${claimId}/diagnoses`, diagnoses,
  );
  return data;
}

export async function replaceClaimLines(claimId, lines) {
  const { data } = await api.patch(
    `/billing/claims/${claimId}/lines`, lines,
  );
  return data;
}

// ---------------------------------------------------------------------------
// Phase 4 — submissions, outcomes, timeline, queues, assignment
// ---------------------------------------------------------------------------
export const SUBMISSION_METHOD_LABELS = {
  manual_paper: "Manual — paper/fax",
  manual_portal: "Manual — payer portal",
  batch_file: "Batch file (837P)",
};

export const OUTCOME_LABELS = {
  accepted: "Accepted",
  rejected: "Rejected",
  pending: "Pending (payer working)",
  paid: "Paid",
  partially_paid: "Partially paid",
  denied: "Denied",
};

export const QUEUE_KEYS = [
  { key: "pending-submission", label: "Pending submission" },
  { key: "needs-fixes", label: "Needs fixes" },
  { key: "rejected", label: "Rejected / denied" },
  { key: "follow-up", label: "Follow-up needed" },
];

// Phase 2b — canonical event-type labels for the queue "last activity"
// column. Keep this map narrow on purpose; unknown event types fall
// through to a humanised variant of the raw string.
export const CLAIM_EVENT_LABELS = {
  created: "Created",
  validated: "Validated",
  submitted: "Submitted",
  resubmitted: "Resubmitted",
  ack_999_accepted: "999 accepted",
  ack_999_rejected: "999 rejected",
  ack_277ca_accepted: "277CA accepted",
  ack_277ca_rejected: "277CA rejected",
  outcome_recorded: "Outcome recorded",
  era_posted: "ERA posted",
  denied: "Denied",
  appeal_filed: "Appeal filed",
  assigned: "Assigned",
  voided: "Voided",
  closed: "Closed",
};

export function claimEventLabel(type) {
  if (!type) return null;
  return CLAIM_EVENT_LABELS[type]
    || type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export async function createClaimSubmission(claimId, body) {
  const { data } = await api.post(
    `/billing/claims/${claimId}/submissions`, body,
  );
  return data;
}

export async function listClaimSubmissions(claimId) {
  const { data } = await api.get(`/billing/claims/${claimId}/submissions`);
  return data;
}

export async function fetchSubmissionPayload(claimId, subId) {
  const { data } = await api.get(
    `/billing/claims/${claimId}/submissions/${subId}/payload`,
  );
  return data;
}

export async function recordSubmissionOutcome(claimId, subId, body) {
  const { data } = await api.post(
    `/billing/claims/${claimId}/submissions/${subId}/outcome`, body,
  );
  return data;
}

export async function fetchClaimTimeline(claimId) {
  const { data } = await api.get(`/billing/claims/${claimId}/timeline`);
  return data;
}

export async function updateClaimAssignment(claimId, assignedTo) {
  const { data } = await api.patch(
    `/billing/claims/${claimId}/assignment`,
    { assigned_to: assignedTo || null },
  );
  return data;
}

export function useClaimQueue({ queue, filters = {} } = {}) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const statusInKey = filters.status_in?.join(",") || "";

  const load = useCallback(async () => {
    if (!queue) { setRows([]); setLoading(false); return; }
    setLoading(true);
    try {
      const params = {};
      if (filters.payer_id) params.payer_id = filters.payer_id;
      if (filters.assigned_to) params.assigned_to = filters.assigned_to;
      if (filters.age_days) params.age_days = filters.age_days;
      if (statusInKey) params.status_in = statusInKey;
      const { data } = await api.get(
        `/billing/claims/queues/${queue}`, { params },
      );
      setRows(data || []);
    } finally { setLoading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queue, filters.payer_id, filters.assigned_to, filters.age_days, statusInKey]);

  useEffect(() => { load(); }, [load]);
  return { rows, loading, refresh: load };
}

// Phase 2b — read the canonical `claim_events` stream for a single
// claim. Used by the ClaimDetail timeline for clearinghouse-aware
// activity (999 / 277CA / era_posted) that doesn't live on the main
// status enum.
export async function fetchClaimEvents(claimId, { eventType, limit } = {}) {
  const params = {};
  if (eventType) params.event_type = eventType;
  if (limit) params.limit = limit;
  const { data } = await api.get(
    `/billing/claims/${claimId}/events`, { params },
  );
  return data || [];
}
