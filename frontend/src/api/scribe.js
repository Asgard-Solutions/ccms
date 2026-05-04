import { api } from "./client";

/**
 * Upload a recorded audio chunk (≤25 MB Whisper limit). The backend
 * runs Whisper synchronously and returns the transcript.
 */
export const uploadScribeAudio = (noteId, noteType, file) => {
  const fd = new FormData();
  fd.append("note_id", noteId);
  fd.append("note_type", noteType);
  fd.append("audio", file);
  return api
    .post("/scribe/audio", fd, {
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);
};

export const listScribeAudio = (noteId, noteType) =>
  api
    .get(`/scribe/encounters/${noteType}/${noteId}/audio`)
    .then((r) => r.data);

export const deleteScribeAudio = (audioId) =>
  api.delete(`/scribe/audio/${audioId}`).then((r) => r.data);

/**
 * Generate SOAP draft from a transcript + optional doctor addendum.
 * Pass either `transcript` explicitly or omit it to let the server
 * concatenate any audio chunks on file for this note.
 */
export const draftScribeSoap = (noteId, noteType, body) =>
  api
    .post(`/scribe/encounters/${noteType}/${noteId}/soap/draft`, body || {})
    .then((r) => r.data);

export const suggestScribeCodes = (noteId, noteType, body) =>
  api
    .post(`/scribe/encounters/${noteType}/${noteId}/coding-suggest`, body || {})
    .then((r) => r.data);
