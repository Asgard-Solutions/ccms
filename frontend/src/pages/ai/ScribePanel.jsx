/**
 * AI Scribe + SOAP-draft side panel.
 *
 * Doctor-only. Used inside the encounter editors. Three sections:
 *   1. Voice recorder — click to start, click to stop (no hard cap).
 *      Each stop uploads to /api/scribe/audio and synchronously
 *      transcribes via Whisper. Multiple chunks accumulate per note.
 *   2. Doctor's addendum — a free-text textarea for last-mile
 *      clarifications that override the transcript.
 *   3. SOAP draft preview — full S/O/A/P generated from the combined
 *      transcript + addendum, with per-section "Apply" buttons and a
 *      single "Apply all" button.
 *
 * Audio retention is handled server-side: chunks soft-delete when the
 * host note is signed.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Mic, Square, Loader2, Sparkles, Trash2, ArrowDownToLine, FileText, Receipt, AlertTriangle } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { Textarea } from "../../components/ui/textarea";
import {
  uploadScribeAudio, listScribeAudio, deleteScribeAudio, draftScribeSoap,
  suggestScribeCodes,
} from "../../api/scribe";

const SECTIONS = [
  { key: "subjective", label: "Subjective" },
  { key: "objective", label: "Objective" },
  { key: "assessment", label: "Assessment" },
  { key: "plan", label: "Plan" },
];

function pickRecorderMime() {
  // Prefer mp4/m4a for Safari, webm for Chrome/Firefox. Fall back to
  // whatever the browser decides if the explicit choices aren't supported.
  const candidates = [
    "audio/webm;codecs=opus", "audio/webm",
    "audio/mp4;codecs=mp4a.40.2", "audio/mp4",
  ];
  for (const m of candidates) {
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(m)) return m;
  }
  return "";
}

export default function ScribePanel({
  noteId, noteType = "follow_up", onApplySection, onApplyAll, disabled = false,
}) {
  const [chunks, setChunks] = useState([]);   // server-side audio rows
  const [recording, setRecording] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [drafts, setDrafts] = useState(null);
  const [rationale, setRationale] = useState("");
  const [addendum, setAddendum] = useState("");
  const [coding, setCoding] = useState(null);
  const [codingLoading, setCodingLoading] = useState(false);

  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const recordedChunksRef = useRef([]);
  const startedAtRef = useRef(null);
  const [tick, setTick] = useState(0);

  // Light timer for the live "MM:SS" display.
  useEffect(() => {
    if (!recording) return;
    const id = setInterval(() => setTick((t) => t + 1), 500);
    return () => clearInterval(id);
  }, [recording]);

  const refresh = useCallback(async () => {
    if (!noteId) return;
    try {
      const res = await listScribeAudio(noteId, noteType);
      setChunks(res?.chunks || []);
    } catch {
      // soft-fail; the panel still works for a fresh note
    }
  }, [noteId, noteType]);

  useEffect(() => { refresh(); }, [refresh]);

  const fullTranscript = useMemo(() => {
    return chunks
      .filter((c) => c.transcribe_status === "ok" && c.transcript)
      .map((c) => c.transcript.trim())
      .filter(Boolean)
      .join("\n\n");
  }, [chunks]);

  async function startRecording() {
    if (disabled) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mimeType = pickRecorderMime();
      const rec = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      recordedChunksRef.current = [];
      rec.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) recordedChunksRef.current.push(ev.data);
      };
      rec.onstop = handleStop;
      recorderRef.current = rec;
      rec.start();
      startedAtRef.current = Date.now();
      setRecording(true);
    } catch (err) {
      toast.error("Microphone access denied — enable it to use the AI scribe.");
    }
  }

  function stopRecording() {
    const rec = recorderRef.current;
    if (rec && rec.state !== "inactive") rec.stop();
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setRecording(false);
  }

  async function handleStop() {
    const blob = new Blob(recordedChunksRef.current, {
      type: recorderRef.current?.mimeType || "audio/webm",
    });
    if (blob.size === 0) {
      toast.error("Empty recording — try again.");
      return;
    }
    if (blob.size > 25 * 1024 * 1024) {
      toast.error("Chunk over 25 MB — please record in shorter bursts.");
      return;
    }
    setUploading(true);
    try {
      const ext = (blob.type || "audio/webm").includes("mp4") ? "m4a" : "webm";
      const file = new File([blob], `chunk-${Date.now()}.${ext}`, { type: blob.type });
      const res = await uploadScribeAudio(noteId, noteType, file);
      if (res.transcribe_status !== "ok") {
        toast.error("Transcription failed — recording saved, you can retry later.");
      } else {
        toast.success("Transcribed.");
      }
      await refresh();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function removeChunk(audioId) {
    try {
      await deleteScribeAudio(audioId);
      await refresh();
    } catch {
      toast.error("Couldn't delete that clip.");
    }
  }

  async function generateDraft() {
    if (!fullTranscript && !addendum.trim()) {
      toast.error("Record audio or write an addendum before drafting.");
      return;
    }
    setDrafting(true);
    try {
      const res = await draftScribeSoap(noteId, noteType, {
        transcript: fullTranscript, addendum,
      });
      setDrafts(res?.drafts || null);
      setRationale(res?.rationale || "");
      setCoding(null); // fresh drafts ⇒ stale codes
      toast.success("SOAP draft ready.");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Draft failed");
    } finally {
      setDrafting(false);
    }
  }

  /**
   * After applying drafts to the editor, fire the billing-readiness
   * coding suggester so the doctor sees CPT/ICD hints inline. Auto-
   * triggered by Apply-All; available manually via the Coding button.
   */
  async function fetchCodingSuggestions() {
    const d = drafts;
    if (!d) return;
    setCodingLoading(true);
    try {
      const res = await suggestScribeCodes(noteId, noteType, {
        drafts: d, addendum,
      });
      setCoding(res || null);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Coding suggestion failed");
    } finally {
      setCodingLoading(false);
    }
  }

  function applyAll() {
    if (!drafts) return;
    if (onApplyAll) {
      onApplyAll(drafts);
    } else if (onApplySection) {
      SECTIONS.forEach(({ key }) => {
        if (drafts[key]) onApplySection(key, drafts[key]);
      });
    }
    toast.success("Applied to all sections.");
    // Auto-fire coding suggestions so the doctor sees CPT/ICD hints
    // inline immediately after pulling the draft into the note.
    fetchCodingSuggestions();
  }

  function applySection(section) {
    const text = drafts?.[section];
    if (!text) return;
    if (onApplySection) onApplySection(section, text);
    toast.success(`Applied to ${section}.`);
  }

  const elapsed = recording && startedAtRef.current
    ? Math.max(0, Math.floor((Date.now() - startedAtRef.current) / 1000))
    : 0;
  const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const ss = String(elapsed % 60).padStart(2, "0");

  return (
    <aside
      data-testid="scribe-panel"
      className="rounded-md border border-border bg-card p-4 space-y-4"
    >
      <header className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-primary" />
        <h3 className="font-medium text-sm">AI scribe</h3>
        <span className="ml-auto text-[10px] uppercase tracking-wider text-muted-foreground">
          doctor only
        </span>
      </header>

      {/* Recorder */}
      <section className="space-y-2" data-testid="scribe-recorder">
        <div className="flex items-center gap-2">
          {!recording ? (
            <Button
              size="sm"
              onClick={startRecording}
              disabled={disabled || uploading}
              data-testid="scribe-record-btn"
              className="rounded-sm"
            >
              <Mic className="mr-1.5 h-3.5 w-3.5" />
              Record
            </Button>
          ) : (
            <Button
              size="sm"
              variant="destructive"
              onClick={stopRecording}
              data-testid="scribe-stop-btn"
              className="rounded-sm"
            >
              <Square className="mr-1.5 h-3.5 w-3.5" />
              Stop
            </Button>
          )}
          {recording && (
            <span
              data-testid="scribe-elapsed"
              className="font-mono text-xs text-destructive"
              aria-live="polite"
            >
              ● {mm}:{ss}
            </span>
          )}
          {uploading && (
            <span
              data-testid="scribe-uploading"
              className="flex items-center gap-1 text-xs text-muted-foreground"
            >
              <Loader2 className="h-3 w-3 animate-spin" /> Transcribing…
            </span>
          )}
        </div>

        {/* Chunk list */}
        {chunks.length > 0 && (
          <ul
            data-testid="scribe-chunks"
            className="space-y-1.5 max-h-44 overflow-y-auto rounded-sm border border-border/60 bg-muted/20 p-2"
          >
            {chunks.map((c) => (
              <li
                key={c.id}
                data-testid={`scribe-chunk-${c.id}`}
                className="text-xs"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    {c.transcribe_status === "ok" ? "transcript" : c.transcribe_status}
                  </span>
                  <button
                    type="button"
                    onClick={() => removeChunk(c.id)}
                    data-testid={`scribe-chunk-delete-${c.id}`}
                    className="text-muted-foreground hover:text-destructive"
                    aria-label="Delete clip"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
                <p className="leading-relaxed whitespace-pre-wrap text-foreground/85">
                  {c.transcript || c.transcribe_error || "(no text)"}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Doctor's free-text addendum */}
      <section className="space-y-1.5" data-testid="scribe-addendum-section">
        <label
          htmlFor="scribe-addendum-input"
          className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground"
        >
          <FileText className="h-3 w-3" />
          Last-mile note (optional)
        </label>
        <Textarea
          id="scribe-addendum-input"
          data-testid="scribe-addendum-input"
          rows={3}
          value={addendum}
          onChange={(e) => setAddendum(e.target.value)}
          placeholder="Anything the recording missed — corrections, clarifications, additional findings."
          className="text-sm"
          disabled={disabled}
        />
      </section>

      {/* Generate */}
      <Button
        size="sm"
        onClick={generateDraft}
        disabled={disabled || drafting || (!fullTranscript && !addendum.trim())}
        data-testid="scribe-draft-btn"
        className="w-full rounded-sm"
      >
        {drafting ? (
          <>
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            Drafting SOAP…
          </>
        ) : (
          <>
            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
            Draft SOAP from this visit
          </>
        )}
      </Button>

      {/* Drafts */}
      {drafts && (
        <section data-testid="scribe-drafts" className="space-y-2 pt-1">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              SOAP draft
            </h4>
            <Button
              size="sm"
              variant="outline"
              onClick={applyAll}
              data-testid="scribe-apply-all-btn"
              className="h-7 rounded-sm text-[11px]"
            >
              <ArrowDownToLine className="mr-1 h-3 w-3" />
              Apply all
            </Button>
          </div>
          {SECTIONS.map(({ key, label }) => {
            const text = drafts[key];
            if (!text) return null;
            return (
              <div
                key={key}
                data-testid={`scribe-draft-${key}`}
                className="rounded-sm border border-border/60 bg-muted/30 p-2.5"
              >
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {label}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 rounded-sm text-xs"
                    onClick={() => applySection(key)}
                    data-testid={`scribe-apply-${key}-btn`}
                  >
                    <ArrowDownToLine className="mr-1 h-3 w-3" />
                    Apply
                  </Button>
                </div>
                <p className="whitespace-pre-wrap text-xs leading-relaxed">
                  {text}
                </p>
              </div>
            );
          })}
          {rationale && (
            <p className="text-[11px] italic text-muted-foreground">
              {rationale}
            </p>
          )}

          {/* Inline CPT/ICD coding suggestions */}
          <div className="pt-2" data-testid="scribe-coding">
            {!coding && !codingLoading && (
              <Button
                size="sm"
                variant="outline"
                className="w-full rounded-sm"
                onClick={fetchCodingSuggestions}
                data-testid="scribe-coding-btn"
              >
                <Receipt className="mr-1.5 h-3.5 w-3.5" />
                Suggest CPT / ICD codes
              </Button>
            )}
            {codingLoading && (
              <div
                data-testid="scribe-coding-loading"
                className="flex items-center gap-2 text-xs text-muted-foreground"
              >
                <Loader2 className="h-3 w-3 animate-spin" />
                Checking billing readiness…
              </div>
            )}
            {coding && (
              <div className="space-y-2">
                {coding.cpt_suggestions?.length > 0 && (
                  <div data-testid="scribe-coding-cpt">
                    <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                      CPT
                    </h4>
                    <ul className="space-y-1">
                      {coding.cpt_suggestions.map((c, i) => (
                        <li
                          key={`cpt-${i}`}
                          data-testid={`scribe-coding-cpt-${c.code}`}
                          className="rounded-sm border border-border/60 bg-muted/30 px-2 py-1.5 text-xs"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-mono font-semibold">{c.code}</span>
                            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                              {c.confidence}
                            </span>
                          </div>
                          <div className="text-foreground/80">{c.description}</div>
                          {c.rationale && (
                            <div className="mt-0.5 text-[11px] italic text-muted-foreground">
                              {c.rationale}
                            </div>
                          )}
                          {Array.isArray(c.modifier_suggestions) && c.modifier_suggestions.length > 0 && (
                            <div className="mt-0.5 text-[11px] text-muted-foreground">
                              modifiers: {c.modifier_suggestions.join(", ")}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {coding.icd_suggestions?.length > 0 && (
                  <div data-testid="scribe-coding-icd">
                    <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                      ICD-10
                    </h4>
                    <ul className="space-y-1">
                      {coding.icd_suggestions.map((c, i) => (
                        <li
                          key={`icd-${i}`}
                          data-testid={`scribe-coding-icd-${c.code}`}
                          className="rounded-sm border border-border/60 bg-muted/30 px-2 py-1.5 text-xs"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-mono font-semibold">{c.code}</span>
                            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                              {c.is_primary_candidate ? "primary" : c.confidence}
                            </span>
                          </div>
                          <div className="text-foreground/80">{c.description}</div>
                          {c.rationale && (
                            <div className="mt-0.5 text-[11px] italic text-muted-foreground">
                              {c.rationale}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {coding.documentation_warnings?.length > 0 && (
                  <div data-testid="scribe-coding-warnings" className="space-y-1">
                    {coding.documentation_warnings.map((w, i) => (
                      <div
                        key={`warn-${i}`}
                        data-testid={`scribe-coding-warning-${i}`}
                        className="flex items-start gap-1.5 rounded-sm border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-900 dark:text-amber-200"
                      >
                        <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                        <span>{w}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>
      )}
    </aside>
  );
}
