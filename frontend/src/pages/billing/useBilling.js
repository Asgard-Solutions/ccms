/**
 * Billing API hooks — thin wrappers around the shared `api` client.
 * Centralising every `/api/billing/*` call here keeps route strings in
 * one place and gives components typed-ish access patterns.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "../../api/client";

// ---------------------------------------------------------------------------
// Invoices
// ---------------------------------------------------------------------------
export function useInvoices({ patientId = null, status = null } = {}) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {};
      if (patientId) params.patient_id = patientId;
      if (status) params.status = status;
      const { data } = await api.get("/billing/invoices", { params });
      setRows(data || []);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [patientId, status]);

  useEffect(() => {
    load();
  }, [load]);

  return { rows, loading, error, refresh: load };
}

export async function fetchInvoice(id) {
  const { data } = await api.get(`/billing/invoices/${id}`);
  return data;
}

export async function fetchInvoiceLines(id) {
  const { data } = await api.get(`/billing/invoices/${id}/lines`);
  return data;
}

export async function transitionInvoiceStatus(id, desired) {
  const { data } = await api.post(
    `/billing/invoices/${id}/status`, null, { params: { desired } },
  );
  return data;
}

export async function voidInvoice(id, reason) {
  const { data } = await api.post(
    `/billing/invoices/${id}/void`, null, { params: { reason } },
  );
  return data;
}

export async function createInvoice(payload) {
  const { data } = await api.post("/billing/invoices", payload);
  return data;
}

// ---------------------------------------------------------------------------
// Payments
// ---------------------------------------------------------------------------
export function usePayments({ patientId = null } = {}) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (patientId) params.patient_id = patientId;
      const { data } = await api.get("/billing/payments", { params });
      setRows(data || []);
    } finally {
      setLoading(false);
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  return { rows, loading, refresh: load };
}

export async function createPayment(payload) {
  const { data } = await api.post("/billing/payments", payload);
  return data;
}

export async function allocatePayment(paymentId, allocations) {
  const { data } = await api.post(
    `/billing/payments/${paymentId}/allocations`, allocations,
  );
  return data;
}

// ---------------------------------------------------------------------------
// Refunds / adjustments
// ---------------------------------------------------------------------------
export async function createRefund(payload) {
  const { data } = await api.post("/billing/refunds", payload);
  return data;
}

export async function createAdjustment(payload) {
  const { data } = await api.post("/billing/adjustments", payload);
  return data;
}

// ---------------------------------------------------------------------------
// Patient ledger
// ---------------------------------------------------------------------------
export function usePatientLedger(patientId) {
  const [payload, setPayload] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!patientId) return;
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get(`/billing/patients/${patientId}/ledger`);
      setPayload(data);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  return { payload, loading, error, refresh: load };
}

// ---------------------------------------------------------------------------
// Helpers used by the UI
// ---------------------------------------------------------------------------
export const INVOICE_STATUS_LABELS = {
  draft: "Draft",
  issued: "Issued",
  partially_paid: "Partially paid",
  paid: "Paid",
  adjusted: "Adjusted",
  void: "Void",
  refunded: "Refunded",
};

export const PAYMENT_METHOD_LABELS = {
  cash: "Cash",
  check: "Check",
  card_present: "Card (in-person)",
  card_not_present: "Card (phone / web)",
  ach: "ACH / bank transfer",
  era_posting: "ERA posting",
  hsa_fsa: "HSA / FSA",
  other: "Other",
};

/** Pill colour class for an invoice status — uses semantic tokens only. */
export function invoiceStatusTone(status) {
  switch (status) {
    case "paid":
    case "adjusted":
      return "bg-success-soft text-success";
    case "partially_paid":
    case "issued":
      return "bg-warning-soft text-warning";
    case "void":
    case "refunded":
      return "bg-destructive/10 text-destructive";
    case "draft":
    default:
      return "bg-muted text-muted-foreground";
  }
}

export function useOutstandingSummary(invoices) {
  return useMemo(() => {
    const outstanding = invoices.reduce(
      (acc, inv) => acc + (inv.balance_cents || 0),
      0,
    );
    const totalBilled = invoices.reduce(
      (acc, inv) => acc + (inv.total_cents || 0),
      0,
    );
    const openCount = invoices.filter(
      (inv) => inv.balance_cents > 0 && !["void", "refunded"].includes(inv.status),
    ).length;
    return { outstanding, totalBilled, openCount };
  }, [invoices]);
}
