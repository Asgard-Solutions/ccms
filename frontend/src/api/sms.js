import { api } from "./client";

export const fetchSmsSettings = () => api.get("/sms/settings").then((r) => r.data);
export const saveSmsSettings = (body) => api.put("/sms/settings", body).then((r) => r.data);
export const deleteSmsSettings = () => api.delete("/sms/settings").then((r) => r.data);
export const sendTestSms = (body) => api.post("/sms/settings/test", body).then((r) => r.data);

export const sendSms = (body) => api.post("/sms/send", body).then((r) => r.data);
export const listSmsThreads = (limit = 50) =>
  api.get(`/sms/threads?limit=${limit}`).then((r) => r.data);
export const listThreadMessages = (threadId) =>
  api.get(`/sms/threads/${threadId}/messages`).then((r) => r.data);
export const fetchOutboundLog = (params = {}) =>
  api
    .get("/sms/outbound-log", { params })
    .then((r) => r.data);
