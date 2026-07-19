/**
 * Live claim submission timeline.
 *
 * Polls `GET /api/billing/claims/{id}/events` every 30 s as a baseline,
 * AND opens a WebSocket to `/api/billing/ws/claims/{id}/events` for
 * real-time pushes. The WebSocket layer is best-effort: if it fails to
 * connect (older browsers, hostile proxies), the polling layer keeps
 * the timeline accurate without any visual difference.
 *
 * Designed for the Submit-to-clearinghouse demo: sandbox submissions
 * are walked through synthetic 999 / 277CA / outcome / ERA events by
 * the backend simulator so the timeline animates within ~20 s.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, CheckCircle2, Clock, Loader2, RefreshCw, Wifi, WifiOff, XCircle } from "lucide-react";
import { Button } from "../../components/ui/button";
import { api } from "../../api/client";

const EVENT_LABELS = {
  created: "Claim created",
  validated: "Scrubber run",
  submitted: "Submitted to clearinghouse",
  resubmitted: "Resubmitted",
  ack_999_accepted: "999 functional ack — accepted",
  ack_999_rejected: "999 functional ack — rejected",
  ack_277ca_accepted: "277CA claim ack — accepted",
  ack_277ca_rejected: "277CA claim ack — rejected",
  outcome_recorded: "Outcome recorded",
  era_posted: "ERA posted (paid)",
  denied: "Denied",
  appeal_filed: "Appeal filed",
  assigned: "Assigned",
  voided: "Voided",
  closed: "Closed",
};

function eventIcon(eventType) {
  if (eventType?.endsWith("_rejected") || eventType === "denied" || eventType === "voided") {
    return <XCircle className="h-3.5 w-3.5 text-destructive" />;
  }
  if (eventType?.endsWith("_accepted") || eventType === "era_posted" || eventType === "outcome_recorded") {
    return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />;
  }
  return <Activity className="h-3.5 w-3.5 text-muted-foreground" />;
}

function eventTone(eventType) {
  if (eventType?.endsWith("_rejected") || eventType === "denied" || eventType === "voided") {
    return "border-destructive/40 bg-destructive/5";
  }
  if (eventType?.endsWith("_accepted") || eventType === "era_posted") {
    return "border-emerald-500/40 bg-emerald-500/5";
  }
  return "border-border/60 bg-muted/30";
}

function buildWsUrl(claimId) {
  // Convert REACT_APP_BACKEND_URL to ws/wss origin and append the
  // billing WS path. The same cookie-based auth flows automatically.
  const base = process.env.REACT_APP_BACKEND_URL;
  if (!base) return null;
  let proto = "wss:";
  let host = base.replace(/^https?:\/\//, "");
  if (base.startsWith("http://")) proto = "ws:";
  return `${proto}//${host}/api/billing/ws/claims/${claimId}/events`;
}

export default function ClaimTimeline({ claimId }) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [wsState, setWsState] = useState("connecting"); // connecting | live | offline
  const wsRef = useRef(null);
  const pollerRef = useRef(null);

  const refresh = useCallback(async () => {
    if (!claimId) return;
    try {
      const { data } = await api.get(`/billing/claims/${claimId}/events`, {
        params: { limit: 100 },
      });
      setEvents(Array.isArray(data) ? data : []);
    } catch {
      // soft-fail; the timeline just stays stale until the next tick
    } finally {
      setLoading(false);
    }
  }, [claimId]);

  // Initial + 30 s polling fallback. Always runs even when the WS is
  // live so a missed push (rare but possible) can't leave the UI
  // stuck.
  useEffect(() => {
    refresh();
    pollerRef.current = setInterval(refresh, 30_000);
    return () => clearInterval(pollerRef.current);
  }, [refresh]);

  // WebSocket subscription. We dedupe events by id so a push that
  // races with a poll doesn't show twice.
  useEffect(() => {
    if (!claimId) return undefined;
    const url = buildWsUrl(claimId);
    if (!url) return undefined;
    let socket;
    try {
      socket = new WebSocket(url);
    } catch {
      setWsState("offline");
      return undefined;
    }
    wsRef.current = socket;
    socket.onopen = () => setWsState("live");
    socket.onerror = () => setWsState("offline");
    socket.onclose = () => {
      // Only mark offline if we never connected; if we were live the
      // app is probably tearing down (page unmount).
      setWsState((s) => (s === "live" ? "offline" : s));
    };
    socket.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data?.type !== "event" || !data.event) return;
        setEvents((prev) => {
          if (prev.some((e) => e.id === data.event.id)) return prev;
          return [data.event, ...prev].slice(0, 100);
        });
      } catch {
        /* ignore malformed frames */
      }
    };
    return () => {
      try { socket.close(); } catch { /* noop */ }
      wsRef.current = null;
    };
  }, [claimId]);

  const latest = events[0];

  return (
    <section
      data-testid="claim-timeline"
      className="rounded-sm border border-border bg-card p-6"
    >
      <header className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h2 className="font-display text-xl font-medium tracking-tight">
            Live timeline
          </h2>
          <p className="text-xs text-muted-foreground">
            Pushed in real-time as the clearinghouse processes the claim.
            Sandbox submissions auto-progress through synthetic acks every few seconds.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            data-testid="claim-timeline-ws-state"
            className={`flex items-center gap-1 rounded-sm px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
              wsState === "live"
                ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                : wsState === "connecting"
                ? "bg-muted text-muted-foreground"
                : "bg-amber-500/10 text-amber-700 dark:text-amber-300"
            }`}
          >
            {wsState === "live" ? <Wifi className="h-3 w-3" /> :
             wsState === "connecting" ? <Loader2 className="h-3 w-3 animate-spin" /> :
             <WifiOff className="h-3 w-3" />}
            {wsState === "live" ? "Live" :
             wsState === "connecting" ? "Connecting" : "Polling"}
          </span>
          <Button
            variant="ghost" size="sm"
            onClick={refresh}
            data-testid="claim-timeline-refresh-btn"
            className="h-7 rounded-sm"
            title="Refresh now"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </header>

      {/* Latest pill — always reflects the freshest event */}
      {latest && (
        <div
          data-testid="claim-timeline-latest"
          className={`mb-3 rounded-sm border px-3 py-2 text-sm ${eventTone(latest.event_type)}`}
        >
          <div className="flex items-center gap-2">
            {eventIcon(latest.event_type)}
            <span className="font-medium">
              {EVENT_LABELS[latest.event_type] || latest.event_type}
            </span>
            <span className="ml-auto font-mono text-[11px] text-muted-foreground">
              {new Date(latest.occurred_at).toLocaleTimeString()}
            </span>
          </div>
          {latest.adapter_route && (
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              via {latest.adapter_route}
              {latest.payload?.synthetic && " · sandbox"}
            </p>
          )}
        </div>
      )}

      {/* Full history */}
      {loading ? (
        <p className="text-xs text-muted-foreground">Loading…</p>
      ) : events.length === 0 ? (
        <p data-testid="claim-timeline-empty" className="text-xs text-muted-foreground">
          No events yet — submit the claim to start the timeline.
        </p>
      ) : (
        <ul className="space-y-1.5" data-testid="claim-timeline-events">
          {events.map((ev) => (
            <li
              key={ev.id}
              data-testid={`claim-timeline-event-${ev.event_type}`}
              className={`rounded-sm border px-2.5 py-1.5 text-xs ${eventTone(ev.event_type)}`}
            >
              <div className="flex items-center gap-2">
                {eventIcon(ev.event_type)}
                <span className="font-medium">
                  {EVENT_LABELS[ev.event_type] || ev.event_type}
                </span>
                <span className="ml-auto flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  {new Date(ev.occurred_at).toLocaleTimeString()}
                </span>
              </div>
              {(ev.from_status || ev.to_status) && (
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  {ev.from_status || "—"} → {ev.to_status || "—"}
                </p>
              )}
              {ev.payload?.message && (
                <p className="mt-0.5 italic text-[11px] text-muted-foreground">
                  {ev.payload.message}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
