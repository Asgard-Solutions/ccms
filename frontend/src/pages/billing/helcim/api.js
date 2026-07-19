import { api } from "../../../api/client";

const BASE = "/billing/helcim";

export const fetchHelcimSettings = () => api.get(`${BASE}/settings`).then((r) => r.data);

export const saveHelcimSettings = (body) =>
  api.put(`${BASE}/settings`, body).then((r) => r.data);

export const deleteHelcimSettings = () =>
  api.delete(`${BASE}/settings`).then((r) => r.data);

export const testHelcimConnection = () =>
  api.post(`${BASE}/settings/test`).then((r) => r.data);

export const initializeHelcimCheckout = (body) =>
  api.post(`${BASE}/checkout/initialize`, body).then((r) => r.data);

export const captureHelcimCheckout = (body) =>
  api.post(`${BASE}/checkout/capture`, body).then((r) => r.data);

export const chargeHelcimSavedCard = (body) =>
  api.post(`${BASE}/charges/saved-card`, body).then((r) => r.data);

export const refundHelcim = (body) =>
  api.post(`${BASE}/refunds`, body).then((r) => r.data);

export const fetchHelcimWebhookLog = () =>
  api.get(`${BASE}/webhook-log`).then((r) => r.data);

// --- Customer Vault (saved cards) ---

export const listSavedCards = (patientId) =>
  api.get(`${BASE}/cards/${patientId}`).then((r) => r.data);

export const saveCardManual = (body) =>
  api.post(`${BASE}/cards`, body).then((r) => r.data);

export const deleteSavedCard = (tokenId) =>
  api.delete(`${BASE}/cards/${tokenId}`).then((r) => r.data);

export const chargeSavedCardById = (body) =>
  api.post(`${BASE}/cards/charge`, body).then((r) => r.data);

// --- Payment schedules ---

export const listSchedules = (params = {}) =>
  api.get(`${BASE}/schedules`, { params }).then((r) => r.data);

export const createSchedule = (body) =>
  api.post(`${BASE}/schedules`, body).then((r) => r.data);

export const patchSchedule = (id, body) =>
  api.patch(`${BASE}/schedules/${id}`, body).then((r) => r.data);

export const changeScheduleStatus = (id, newStatus) =>
  api.post(`${BASE}/schedules/${id}/status`, { new_status: newStatus }).then((r) => r.data);

export const fetchScheduleRuns = (id) =>
  api.get(`${BASE}/schedules/${id}/runs`).then((r) => r.data);

export const runScheduleNow = (id) =>
  api.post(`${BASE}/schedules/${id}/run-now`).then((r) => r.data);

export const tickScheduler = () =>
  api.post(`${BASE}/scheduler/tick`).then((r) => r.data);

// --- Billing failures dashboard ---

export const fetchBillingFailures = (params = {}) =>
  api.get(`${BASE}/billing-failures`, { params }).then((r) => r.data);

export const dismissBillingFailure = (notifId) =>
  api.post(`${BASE}/billing-failures/${notifId}/dismiss`).then((r) => r.data);

// --- Statement auto-pay ---

export const fetchStatementAutopaySettings = () =>
  api.get(`${BASE}/statement-autopay/settings`).then((r) => r.data);

export const saveStatementAutopaySettings = (body) =>
  api.put(`${BASE}/statement-autopay/settings`, body).then((r) => r.data);

export const fetchPatientAutopayOptIn = (patientId) =>
  api.get(`${BASE}/statement-autopay/patients/${patientId}`).then((r) => r.data);

export const savePatientAutopayOptIn = (patientId, body) =>
  api.put(`${BASE}/statement-autopay/patients/${patientId}`, body).then((r) => r.data);
