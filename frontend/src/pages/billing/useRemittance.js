/**
 * Phase 5 — Remittance posting / AR aging / denials hooks.
 * All calls route through the shared axios `api` with ReauthGate
 * interceptor; no special handling needed here.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

export const DENIAL_STATUS_LABELS = {
  open: "Open",
  in_progress: "In progress",
  resolved: "Resolved",
  escalated: "Escalated",
  closed: "Closed",
};

export function denialStatusTone(s) {
  switch (s) {
    case "open": return "bg-warning/15 text-warning";
    case "in_progress": return "bg-primary/15 text-primary";
    case "resolved": return "bg-success/15 text-success";
    case "escalated": return "bg-destructive/15 text-destructive";
    case "closed": return "bg-muted text-muted-foreground";
    default: return "bg-muted text-foreground";
  }
}

export async function postRemittance(body) {
  const { data } = await api.post("/billing/remittances", body);
  return data;
}

export async function fetchRemittanceDetail(id) {
  const { data } = await api.get(`/billing/remittances/${id}`);
  return data;
}

export function useRemittances() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/billing/remittances");
      setRows(data || []);
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  return { rows, loading, refresh: load };
}

export function useDenialWorkItems() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/billing/denial-work-items");
      setRows(data || []);
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  return { rows, loading, refresh: load };
}

export async function updateDenialWorkItem(id, body) {
  const { data } = await api.put(`/billing/denial-work-items/${id}`, body);
  return data;
}

export function useArAging(payerId = null) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = payerId ? { payer_id: payerId } : {};
      const { data } = await api.get("/billing/ar/aging", { params });
      setData(data);
    } finally { setLoading(false); }
  }, [payerId]);
  useEffect(() => { load(); }, [load]);
  return { data, loading, refresh: load };
}

export function useArAgingByPayer() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/billing/ar/aging/by-payer");
      setRows(data?.rows || []);
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  return { rows, loading, refresh: load };
}

export async function generateStatement(patientId) {
  const { data } = await api.post(
    `/billing/patients/${patientId}/statements`,
  );
  return data;
}

export async function listStatements(patientId) {
  const { data } = await api.get(
    `/billing/patients/${patientId}/statements`,
  );
  return data;
}
