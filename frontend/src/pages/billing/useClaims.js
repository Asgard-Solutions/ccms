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
  const { data } = await api.put(`/billing/claims/${claimId}/header`, patch);
  return data;
}

export async function replaceClaimDiagnoses(claimId, diagnoses) {
  const { data } = await api.put(
    `/billing/claims/${claimId}/diagnoses`, diagnoses,
  );
  return data;
}

export async function replaceClaimLines(claimId, lines) {
  const { data } = await api.put(
    `/billing/claims/${claimId}/lines`, lines,
  );
  return data;
}
