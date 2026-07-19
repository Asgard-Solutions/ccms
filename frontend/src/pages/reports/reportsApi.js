import { api, formatApiError } from "../../api/client";

export async function fetchCatalog() {
  const { data } = await api.get("/reports/catalog");
  return data;
}

export async function fetchReportMeta(name) {
  const { data } = await api.get(`/reports/${name}`);
  return data;
}

export async function runReport(name, payload) {
  const { data } = await api.post(`/reports/${name}/run`, payload);
  return data;
}

export async function listViews(name) {
  const { data } = await api.get(`/reports/${name}/views`);
  return data.views || [];
}

export async function createView(name, payload) {
  const { data } = await api.post(`/reports/${name}/views`, payload);
  return data;
}

export async function updateView(viewId, payload) {
  const { data } = await api.patch(`/reports/views/${viewId}`, payload);
  return data;
}

export async function deleteView(viewId) {
  await api.delete(`/reports/views/${viewId}`);
}

export async function requestExport(name, payload) {
  const { data } = await api.post(`/reports/${name}/export`, payload);
  return data;
}

export async function fetchDenialClassifications() {
  const { data } = await api.get("/reports/denial-classifications");
  return data;
}

export async function upsertDenialClassification(payload) {
  const { data } = await api.post("/reports/denial-classifications", payload);
  return data;
}

export async function removeDenialClassification(id) {
  await api.delete(`/reports/denial-classifications/${id}`);
}


export async function pollExport(exportId) {
  const { data } = await api.get(`/exports/${exportId}`);
  return data;
}

export function exportDownloadUrl(exportId, token) {
  const base = process.env.REACT_APP_BACKEND_URL;
  return `${base}/api/exports/${exportId}/download?token=${encodeURIComponent(token)}`;
}

export { formatApiError };
