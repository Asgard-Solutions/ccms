import axios from "axios";

const API_ROOT = `${process.env.REACT_APP_BACKEND_URL}/api`;

/**
 * Shared axios instance — sends httpOnly cookies with every request.
 * Keep all service modules talking through this single client so the
 * API Gateway pattern is enforced on the frontend too.
 */
export const api = axios.create({
  baseURL: API_ROOT,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

/** Normalise FastAPI error shapes (422 validation arrays, 4xx detail strings, etc.). */
export function formatApiError(err) {
  const detail = err?.response?.data?.detail;
  if (detail == null) return err?.message || "Something went wrong.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
      .filter(Boolean)
      .join(" ");
  }
  if (detail && typeof detail.msg === "string") return detail.msg;
  return String(detail);
}
