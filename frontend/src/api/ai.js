import { api } from "./client";

// Chart-prep brief
export const fetchChartBrief = (patientId) =>
  api.get(`/ai/chart-brief/${patientId}`).then((r) => r.data);
export const regenerateChartBrief = (patientId) =>
  api.post(`/ai/chart-brief/${patientId}/regenerate`).then((r) => r.data);

// Encounter-scoped helpers (note_id = clinical_notes.id of current draft)
export const fetchPriorSections = (noteId) =>
  api.get(`/ai/encounters/${noteId}/prior-sections`).then((r) => r.data);
export const draftEncounterSections = (noteId) =>
  api.post(`/ai/encounters/${noteId}/draft-sections`).then((r) => r.data);
export const fetchSinceLastDiff = (noteId) =>
  api.get(`/ai/encounters/${noteId}/since-last-diff`).then((r) => r.data);

// Admin settings
export const fetchAISettings = () =>
  api.get("/ai/settings").then((r) => r.data);
export const saveAISettings = (body) =>
  api.put("/ai/settings", body).then((r) => r.data);

// SOAP-template overrides (admin)
export const listAITemplates = () =>
  api.get("/ai/templates").then((r) => r.data);
export const upsertAITemplate = (body) =>
  api.put("/ai/templates", body).then((r) => r.data);
export const deleteAITemplate = (params) =>
  api.delete("/ai/templates", { params }).then((r) => r.data);

// Natural-language semantic search across patient charts
export const aiSemanticSearch = (body) =>
  api.post("/ai/search", body).then((r) => r.data);

// Natural-language scheduling
export const nlSchedulingParse = (body) =>
  api.post("/scheduling/nl/parse", body).then((r) => r.data);
export const nlSchedulingCreate = (body) =>
  api.post("/scheduling/nl/create", body).then((r) => r.data);
