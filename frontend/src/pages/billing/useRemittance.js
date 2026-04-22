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

export const DENIAL_CATEGORIES = [
  "coding", "eligibility", "authorization",
  "timely_filing", "duplicate", "other",
];

export const DENIAL_CATEGORY_LABELS = {
  coding: "Coding / bundling",
  eligibility: "Eligibility",
  authorization: "Authorization",
  timely_filing: "Timely filing",
  duplicate: "Duplicate",
  other: "Other / unmapped",
};

export function denialCategoryTone(cat) {
  switch (cat) {
    case "coding": return "bg-primary/15 text-primary";
    case "eligibility": return "bg-warning/15 text-warning";
    case "authorization": return "bg-accent/15 text-accent-foreground";
    case "timely_filing": return "bg-destructive/15 text-destructive";
    case "duplicate": return "bg-muted text-muted-foreground";
    default: return "bg-muted text-foreground";
  }
}

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

export function useDenialWorkItems({ status = null, category = null } = {}) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (status && status !== "all") params.status_in = status;
      if (category && category !== "all") params.category = category;
      const { data } = await api.get("/billing/denial-work-items", { params });
      setRows(data || []);
    } finally { setLoading(false); }
  }, [status, category]);
  useEffect(() => { load(); }, [load]);
  return { rows, loading, refresh: load };
}

export function useDenialCategorySummary(includeClosed = false) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get(
        "/billing/denial-work-items/category-summary",
        { params: includeClosed ? { include_closed: true } : {} },
      );
      setData(data);
    } finally { setLoading(false); }
  }, [includeClosed]);
  useEffect(() => { load(); }, [load]);
  return { data, loading, refresh: load };
}

export async function updateDenialWorkItem(id, body) {
  const { data } = await api.patch(`/billing/denial-work-items/${id}`, body);
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

export function statementPdfUrl(patientId, stmtId) {
  const base = process.env.REACT_APP_BACKEND_URL || "";
  return `${base}/api/billing/patients/${patientId}/statements/${stmtId}/pdf`;
}

export async function emailStatement(patientId, stmtId, { channel = "email", to = null } = {}) {
  const payload = { channel };
  if (to) payload.to = to;
  const { data } = await api.post(
    `/billing/patients/${patientId}/statements/${stmtId}/send`,
    payload,
  );
  return data;
}

/**
 * Patient self-service — my own statements.
 */
export async function listMyStatements() {
  const { data } = await api.get("/billing/me/statements");
  return data;
}

export function myStatementPdfUrl(stmtId) {
  const base = process.env.REACT_APP_BACKEND_URL || "";
  return `${base}/api/billing/me/statements/${stmtId}.pdf`;
}

export async function uploadRemittanceImport(file) {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post("/billing/remittances/import", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

/**
 * Month-end bulk action: regenerate + dispatch statements for every
 * patient whose outstanding balance has moved since their last statement.
 */
export async function sendOutstandingStatements({ dryRun = false } = {}) {
  const { data } = await api.post("/billing/statements/send-outstanding", {
    dry_run: dryRun,
  });
  return data;
}

export async function commitRemittanceImport(stagedId) {
  const { data } = await api.post(
    `/billing/remittances/imports/${stagedId}/commit`,
  );
  return data;
}
