/**
 * Shared billing-admin hooks — payers, fee schedules, insurance
 * policies CRUD, and encounter charge-capture preview/commit.
 *
 * Kept separate from `useBilling.js` (which is the patient-facing
 * billing API layer) so admin UIs don't pull in the full dashboard
 * bundle.
 */
import { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "../../api/client";

// ---------------------------------------------------------------------------
// Payers
// ---------------------------------------------------------------------------
export function usePayers({ activeOnly = false } = {}) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/billing/payers", {
        params: activeOnly ? { active_only: true } : {},
      });
      setRows(data || []);
      setError(null);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [activeOnly]);

  useEffect(() => { load(); }, [load]);
  return { rows, loading, error, refresh: load };
}

export async function createPayer(payload) {
  const { data } = await api.post("/billing/payers", payload);
  return data;
}

export async function updatePayer(id, payload) {
  const { data } = await api.patch(`/billing/payers/${id}`, payload);
  return data;
}

// ---------------------------------------------------------------------------
// Insurance policies
// ---------------------------------------------------------------------------
export function usePatientPolicies(patientId) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!patientId) return;
    setLoading(true);
    try {
      const { data } = await api.get("/billing/insurance-policies", {
        params: { patient_id: patientId },
      });
      setRows(data || []);
    } finally {
      setLoading(false);
    }
  }, [patientId]);

  useEffect(() => { load(); }, [load]);
  return { rows, loading, refresh: load };
}

export async function createPolicy(payload) {
  const { data } = await api.post("/billing/insurance-policies", payload);
  return data;
}

export async function updatePolicy(id, payload) {
  const { data } = await api.patch(`/billing/insurance-policies/${id}`, payload);
  return data;
}

export async function deactivatePolicy(id) {
  await api.delete(`/billing/insurance-policies/${id}`);
}

// ---------------------------------------------------------------------------
// Eligibility 270/271
// ---------------------------------------------------------------------------
export async function runEligibilityCheck(policyId, payload = {}) {
  const { data } = await api.post(
    `/billing/policies/${policyId}/eligibility-check`,
    payload,
  );
  return data;
}

export async function listEligibilityChecks(policyId) {
  const { data } = await api.get(
    `/billing/policies/${policyId}/eligibility-checks`,
  );
  return data || [];
}

export async function fetchEligibilityCheckDetail(checkId) {
  const { data } = await api.get(`/billing/eligibility-checks/${checkId}`);
  return data;
}

// ---------------------------------------------------------------------------
// Fee schedules
// ---------------------------------------------------------------------------
export function useFeeSchedules() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/billing/fee-schedules");
      setRows(data || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  return { rows, loading, refresh: load };
}

export async function createFeeSchedule(payload) {
  const { data } = await api.post("/billing/fee-schedules", payload);
  return data;
}

export async function upsertFeeScheduleLines(id, lines) {
  const { data } = await api.patch(`/billing/fee-schedules/${id}/lines`, lines);
  return data;
}

export async function fetchFeeScheduleLines(id) {
  const { data } = await api.get(`/billing/fee-schedules/${id}/lines`);
  return data;
}

// ---------------------------------------------------------------------------
// Charge capture (encounters)
// ---------------------------------------------------------------------------
export async function previewChargeCandidates(recordId) {
  const { data } = await api.get(
    `/billing/encounters/${recordId}/charge-candidates`,
  );
  return data;
}

export async function captureEncounter(recordId) {
  const { data } = await api.post(`/billing/encounters/${recordId}/capture`);
  return data;
}

// ---------------------------------------------------------------------------
// Medical record coding / sign (lives in patient service but used from
// the billing surface)
// ---------------------------------------------------------------------------
export async function updateRecordCoding(patientId, recordId, payload) {
  const { data } = await api.patch(
    `/patients/${patientId}/records/${recordId}/coding`, payload,
  );
  return data;
}

export async function signRecord(patientId, recordId) {
  const { data } = await api.post(
    `/patients/${patientId}/records/${recordId}/sign`,
  );
  return data;
}
