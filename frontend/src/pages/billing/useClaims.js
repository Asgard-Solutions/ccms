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

// Phase 3 — canonical lifecycle (single source of truth for row
// badges, filter options, and dashboards). Mirrors
// backend/services/billing/canonical_status.py.
export const CANONICAL_STATUS_LABELS = {
  draft:       "Draft",
  ready:       "Ready",
  submitted:   "Submitted",
  accepted:    "Accepted",
  needs_fixes: "Needs fixes",
  denied:      "Denied",
  paid:        "Paid",
  follow_up:   "Follow-up needed",
};

export const CANONICAL_STATUS_ORDER = [
  "draft", "ready", "submitted", "accepted",
  "needs_fixes", "denied", "paid", "follow_up",
];

export function canonicalStatusTone(canonical) {
  switch (canonical) {
    case "draft":       return "bg-muted text-muted-foreground";
    case "ready":       return "bg-primary/10 text-primary";
    case "submitted":   return "bg-primary/10 text-primary";
    case "accepted":    return "bg-success-soft text-success";
    case "needs_fixes": return "bg-destructive/10 text-destructive";
    case "denied":      return "bg-destructive/10 text-destructive";
    case "paid":        return "bg-success-soft text-success";
    case "follow_up":   return "bg-warning-soft text-warning";
    default:            return "bg-muted text-muted-foreground";
  }
}

export function canonicalStatusLabel(canonical) {
  return CANONICAL_STATUS_LABELS[canonical] || canonical || "—";
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

export async function fetchAssignableUsers() {
  const { data } = await api.get(`/billing/claims/assignable-users`);
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

export async function flagClaimForFollowup(claimId, { reason, next_action_at } = {}) {
  const { data } = await api.post(
    `/billing/claims/${claimId}/flag-followup`,
    { reason: reason || null, next_action_at: next_action_at || null },
  );
  return data;
}

export async function clearClaimFollowupFlag(claimId) {
  const { data } = await api.delete(
    `/billing/claims/${claimId}/flag-followup`,
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

// -----------------------------------------------------------------------
// Phase-UI — server-paginated / sorted / enriched queue hook
// -----------------------------------------------------------------------
// Backed by `GET /api/billing/claims/queue`. Returns the full envelope
// (rows + summary + tab_counts + filter_options + total) so the page
// doesn't need to fetch anything else. Filters, page, and sort are
// all driven server-side so the UI never builds summary stats from a
// truncated page.
export function useClaimsQueueV2({ tab, page, pageSize, sort, filters = {} }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const statusInKey = filters.status_in?.join(",") || "";
  const canonicalInKey = filters.canonical_status_in?.join(",") || "";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {
        tab,
        page,
        page_size: pageSize,
        sort,
      };
      if (filters.payer_id)    params.payer_id = filters.payer_id;
      if (filters.unassigned)  params.unassigned = true;
      else if (filters.assigned_to) params.assigned_to = filters.assigned_to;
      if (filters.age_days)    params.age_days = filters.age_days;
      if (statusInKey)         params.status_in = statusInKey;
      if (canonicalInKey)      params.canonical_status_in = canonicalInKey;
      const { data: resp } = await api.get("/billing/claims/queue", { params });
      setData(resp);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, page, pageSize, sort, filters.payer_id,
      filters.assigned_to, filters.unassigned, filters.age_days,
      statusInKey, canonicalInKey]);

  useEffect(() => { load(); }, [load]);
  return { data, loading, error, refresh: load };
}
