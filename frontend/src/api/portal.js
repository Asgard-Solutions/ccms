import { api } from "./client";

// ---- Portal — SMS OTP auth (unauth during request/verify) ----
export const portalOtpRequest = (payload) =>
  api.post("/portal/auth/otp/request", payload).then((r) => r.data);
export const portalOtpVerify = (payload) =>
  api.post("/portal/auth/otp/verify", payload).then((r) => r.data);

// ---- Portal — patient-authed endpoints ----
export const fetchPortalOverview = () =>
  api.get("/portal/overview").then((r) => r.data);

export const portalCheckIn = (appointmentId) =>
  api.post(`/portal/appointments/${appointmentId}/check-in`).then((r) => r.data);

export const fetchPortalProviders = () =>
  api.get("/portal/providers").then((r) => r.data);
export const fetchPortalAppointmentTypes = () =>
  api.get("/portal/appointment-types").then((r) => r.data);

// Booking requests (portal side)
export const createBookingRequest = (body) =>
  api.post("/portal/booking-requests", body).then((r) => r.data);
export const listMyBookingRequests = () =>
  api.get("/portal/booking-requests").then((r) => r.data);
export const cancelBookingRequest = (id) =>
  api.post(`/portal/booking-requests/${id}/cancel`).then((r) => r.data);

// Questionnaires (portal side)
export const listMyQuestionnaires = () =>
  api.get("/portal/questionnaires").then((r) => r.data);
export const getMyQuestionnaire = (id) =>
  api.get(`/portal/questionnaires/${id}`).then((r) => r.data);
export const submitMyQuestionnaire = (id, answers) =>
  api.post(`/portal/questionnaires/${id}/submit`, { answers }).then((r) => r.data);

// ---- Kiosk — unauthenticated public endpoint ----
export const kioskCheckIn = (body, tenantSlug = "default") =>
  api
    .post("/kiosk/check-in", body, { headers: { "X-Kiosk-Tenant": tenantSlug } })
    .then((r) => r.data);

// ---- Staff-side: booking requests queue + questionnaires ----
export const staffListBookingRequests = (params = {}) =>
  api.get("/booking-requests", { params }).then((r) => r.data);
export const staffApproveBooking = (id, body) =>
  api.post(`/booking-requests/${id}/approve`, body).then((r) => r.data);
export const staffDeclineBooking = (id, reason) =>
  api.post(`/booking-requests/${id}/decline`, { reason }).then((r) => r.data);

export const fetchQuestionnaireTemplates = () =>
  api.get("/questionnaires/templates").then((r) => r.data);
export const assignQuestionnaire = (body) =>
  api.post("/questionnaires/assign", body).then((r) => r.data);
export const staffListAssignments = (params = {}) =>
  api.get("/questionnaires/assignments", { params }).then((r) => r.data);
