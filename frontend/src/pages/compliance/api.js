import { api } from "../../api/client";

const BASE = "/compliance-ops";

const get = async (path, params) => {
  const { data } = await api.get(`${BASE}${path}`, { params });
  return data;
};

export const fetchComplianceDashboard = () => get("/dashboard");

export const fetchControls = (q = {}) => get("/controls", q);
export const createControl = (body) => api.post(`${BASE}/controls`, body).then((r) => r.data);

export const fetchPolicies = (statusFilter) => get("/policies", statusFilter ? { status_filter: statusFilter } : {});
export const createPolicy = (body) => api.post(`${BASE}/policies`, body).then((r) => r.data);

export const fetchRisks = (statusFilter) => get("/risks", statusFilter ? { status_filter: statusFilter } : {});
export const createRisk = (body) => api.post(`${BASE}/risks`, body).then((r) => r.data);

export const fetchEvidence = (params = {}) => get("/evidence", params);
export const createEvidence = (body) => api.post(`${BASE}/evidence`, body).then((r) => r.data);
export const setEvidenceLegalHold = (id, on) =>
  api.post(`${BASE}/evidence/${id}/legal-hold`, null, { params: { on } }).then((r) => r.data);

export const fetchIncidents = (params = {}) => get("/incidents", params);
export const createIncident = (body) => api.post(`${BASE}/incidents`, body).then((r) => r.data);

export const fetchVendors = () => get("/vendors");
export const createVendor = (body) => api.post(`${BASE}/vendors`, body).then((r) => r.data);

export const fetchDataClasses = () => get("/data-classes");

export const fetchAccessReviews = (statusFilter) =>
  get("/access-reviews", statusFilter ? { status_filter: statusFilter } : {});
export const createAccessReview = (body) => api.post(`${BASE}/access-reviews`, body).then((r) => r.data);

// Generic — works for any of: control | evidence | risk | policy | incident | vendor | data_class | access_review
export const changeStatus = (entityType, id, newStatus, note) =>
  api.post(`${BASE}/${entityType}/${id}/status`, { new_status: newStatus, note: note || null }).then((r) => r.data);

export const patchEntity = (entityType, id, fields, note) =>
  api.patch(`${BASE}/${entityType}/${id}`, { fields, note: note || null }).then((r) => r.data);

export const getEntityRaw = (entityType, id) =>
  api.get(`${BASE}/${entityType}/${id}`).then((r) => r.data);
