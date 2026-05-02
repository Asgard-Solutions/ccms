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
