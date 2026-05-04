import { api } from "./client";

export const fetchEmailSettings = () =>
  api.get("/email/settings").then((r) => r.data);
export const saveEmailSettings = (body) =>
  api.put("/email/settings", body).then((r) => r.data);
export const deleteEmailSettings = () =>
  api.delete("/email/settings").then((r) => r.data);
export const sendTestEmail = (body) =>
  api.post("/email/settings/test", body).then((r) => r.data);
export const fetchEmailLog = (params = {}) =>
  api.get("/email/outbound-log", { params }).then((r) => r.data);

// Google OAuth
export const googleAvailability = () =>
  api.get("/auth/google/availability").then((r) => r.data);
export const googleExchange = (sessionId) =>
  api
    .post("/auth/google/exchange", { session_id: sessionId })
    .then((r) => r.data);
export const fetchGoogleSettings = () =>
  api.get("/auth/google/settings").then((r) => r.data);
export const saveGoogleSettings = (body) =>
  api.put("/auth/google/settings", body).then((r) => r.data);
