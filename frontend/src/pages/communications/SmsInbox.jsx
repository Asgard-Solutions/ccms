/**
 * Staff two-way SMS inbox — /communications/sms.
 *
 * Two-pane layout: thread list on the left, conversation panel on the
 * right with a reply box at the bottom.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  ArrowLeft, MessageSquare, MessageSquareText, Send,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Skeleton } from "../../components/ui/skeleton";
import {
  listSmsThreads, listThreadMessages, sendSms,
} from "../../api/sms";
import { formatDateTime } from "../../utils/time";

function ThreadRow({ thread, active, onClick }) {
  return (
    <button
      type="button"
      data-testid={`sms-thread-row-${thread.id}`}
      onClick={onClick}
      className={`w-full text-left px-4 py-3 border-b border-border/60 transition
        ${active ? "bg-muted" : "hover:bg-muted/50"}`}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium truncate">{thread.peer}</p>
        {thread.unread_count > 0 && (
          <span className="text-[10px] font-bold rounded-full bg-primary text-primary-foreground px-1.5 py-0.5 min-w-[20px] text-center">
            {thread.unread_count}
          </span>
        )}
      </div>
      <p className="text-xs text-muted-foreground truncate mt-0.5">
        {thread.last_message_preview || "—"}
      </p>
      <p className="text-[10px] text-muted-foreground mt-0.5">
        {formatDateTime(thread.last_message_at)}
      </p>
    </button>
  );
}

function MessageBubble({ message }) {
  const inbound = message.direction === "inbound";
  return (
    <div
      data-testid={`sms-message-${message.id}`}
      className={`flex ${inbound ? "justify-start" : "justify-end"}`}
    >
      <div
        className={`max-w-[80%] rounded-md px-3 py-2 text-sm
          ${inbound
            ? "bg-muted text-foreground"
            : "bg-primary text-primary-foreground"}`}
      >
        <p className="whitespace-pre-wrap">{message.body}</p>
        <p className={`mt-1 text-[10px] ${inbound ? "text-muted-foreground" : "text-primary-foreground/70"}`}>
          {formatDateTime(message.created_at)}
        </p>
      </div>
    </div>
  );
}

export default function SmsInbox() {
  const [threads, setThreads] = useState([]);
  const [active, setActive] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loadingThreads, setLoadingThreads] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);

  const loadThreads = useCallback(async () => {
    try {
      setThreads(await listSmsThreads(100));
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load threads");
    } finally {
      setLoadingThreads(false);
    }
  }, []);

  const loadMessages = useCallback(async (threadId) => {
    setLoadingMessages(true);
    try {
      const r = await listThreadMessages(threadId);
      setMessages(r.messages || []);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to open thread");
    } finally {
      setLoadingMessages(false);
    }
  }, []);

  useEffect(() => { loadThreads(); }, [loadThreads]);

  useEffect(() => {
    if (active) loadMessages(active.id);
    else setMessages([]);
  }, [active, loadMessages]);

  async function handleSend(e) {
    e.preventDefault();
    if (!active || !reply.trim()) return;
    setSending(true);
    try {
      await sendSms({
        to: active.peer,
        body: reply.trim(),
        patient_id: active.patient_id || null,
      });
      setReply("");
      await loadMessages(active.id);
      await loadThreads();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Send failed");
    } finally {
      setSending(false);
    }
  }

  return (
    <div
      data-testid="sms-inbox-page"
      className="grid grid-cols-1 md:grid-cols-[320px_1fr] gap-0 h-[calc(100vh-12rem)] min-h-[480px] rounded-md border border-border overflow-hidden bg-card"
    >
      {/* Thread list */}
      <aside
        data-testid="sms-thread-list"
        className={`flex flex-col border-r border-border ${active ? "hidden md:flex" : "flex"}`}
      >
        <header className="px-4 py-3 border-b border-border flex items-center gap-2">
          <MessageSquareText className="h-4 w-4 text-primary" />
          <h2 className="font-medium">Inbox</h2>
        </header>
        <div className="flex-1 overflow-y-auto">
          {loadingThreads ? (
            <div className="p-4 space-y-2">
              <Skeleton className="h-12" /><Skeleton className="h-12" />
            </div>
          ) : threads.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">
              <MessageSquare className="mx-auto h-6 w-6 mb-2" />
              No conversations yet.
            </div>
          ) : (
            threads.map((t) => (
              <ThreadRow
                key={t.id}
                thread={t}
                active={active?.id === t.id}
                onClick={() => setActive(t)}
              />
            ))
          )}
        </div>
      </aside>

      {/* Message panel */}
      <section
        data-testid="sms-message-panel"
        className={`flex flex-col ${active ? "flex" : "hidden md:flex"}`}
      >
        {!active ? (
          <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
            Pick a conversation on the left.
          </div>
        ) : (
          <>
            <header className="px-4 py-3 border-b border-border flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                className="md:hidden"
                onClick={() => setActive(null)}
                data-testid="sms-back-btn"
              >
                <ArrowLeft className="h-4 w-4" />
              </Button>
              <div>
                <p className="font-medium text-sm">{active.peer}</p>
                <p className="text-xs text-muted-foreground">
                  {active.patient_id ? `Patient #${active.patient_id.slice(0, 8)}` : "Unknown number"}
                </p>
              </div>
            </header>
            <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-muted/20">
              {loadingMessages ? (
                <Skeleton className="h-32" />
              ) : (
                messages.map((m) => <MessageBubble key={m.id} message={m} />)
              )}
            </div>
            <form
              onSubmit={handleSend}
              className="border-t border-border p-3 flex items-center gap-2"
              data-testid="sms-reply-form"
            >
              <Input
                data-testid="sms-reply-input"
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                placeholder="Type a message…"
                disabled={sending}
                maxLength={1600}
              />
              <Button
                type="submit"
                disabled={sending || !reply.trim()}
                data-testid="sms-reply-send-btn"
              >
                <Send className="h-4 w-4" />
              </Button>
            </form>
          </>
        )}
      </section>
    </div>
  );
}
