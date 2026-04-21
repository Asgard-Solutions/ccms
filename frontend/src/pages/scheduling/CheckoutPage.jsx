import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  CheckCircle2,
  ChevronRight,
  Clock,
  FileText,
  Home,
  LogOut,
  Play,
  PlayCircle,
  RefreshCw,
  Stethoscope,
} from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Label } from "../../components/ui/label";
import { Skeleton } from "../../components/ui/skeleton";
import { Textarea } from "../../components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

/**
 * Checkout — front-desk operational view for patients in the
 * ready_for_checkout / at-checkout-counter / recently checked-out stages.
 *
 * Sections:
 *   • Ready for Checkout   — status=ready_for_checkout
 *   • At Checkout Counter  — status=ready_for_checkout/completed and
 *                            location_type=checkout (started but not yet
 *                            finalised).
 *   • Recently Checked Out — status=checked_out within last 2h; shows
 *                            Mark Departed.
 *
 * Complete Checkout opens a dialog that captures `checkout_notes` /
 * `checkout_summary` and lives behind POST /api/appointments/{id}/checkout.
 * The backend publishes `appointment.checkout` on the event bus —
 * future payment/invoice/print/follow-up hooks plug in there.
 *
 * Explicit non-goals in this phase: no payment capture, no invoice
 * generation, no copay collection. The notes+summary are the clean hook
 * point for those next phases.
 */

const POLL_INTERVAL_MS = 20000;

function isoDate(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
}

function humanDuration(from) {
  if (!from) return null;
  const diffMs = Date.now() - new Date(from).getTime();
  if (diffMs < 0) return "just now";
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "<1m";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem ? `${hrs}h ${rem}m` : `${hrs}h`;
}

export default function CheckoutPage() {
  const [appointments, setAppointments] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [completeDialog, setCompleteDialog] = useState(null);

  // Tick every minute so "ago" labels stay fresh without refetch.
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 60000);
    return () => clearInterval(t);
  }, []);

  async function fetchData({ silent = false } = {}) {
    if (silent) setRefreshing(true);
    try {
      const today = isoDate(new Date());
      const params = { from: `${today}T00:00:00Z`, to: `${today}T23:59:59Z` };
      const { data } = await api.get("/appointments", { params });
      setAppointments(data || []);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to load");
      setAppointments([]);
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => { fetchData(); }, []);
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") fetchData({ silent: true });
    }, POLL_INTERVAL_MS);
    return () => clearInterval(t);
  }, []);

  const readyForCheckout = useMemo(
    () => (appointments || [])
      .filter((a) => a.status === "ready_for_checkout" && a.current_location_type !== "checkout")
      .sort((a, b) => new Date(a.ready_for_checkout_at || 0) - new Date(b.ready_for_checkout_at || 0)),
    [appointments],
  );
  const atCheckout = useMemo(
    () => (appointments || [])
      .filter((a) => (a.status === "ready_for_checkout" || a.status === "completed")
                  && a.current_location_type === "checkout")
      .sort((a, b) => new Date(a.checkout_started_at || 0) - new Date(b.checkout_started_at || 0)),
    [appointments],
  );
  const recentlyCheckedOut = useMemo(
    () => (appointments || [])
      .filter((a) => a.status === "checked_out"
                  && a.checked_out_at
                  && Date.now() - new Date(a.checked_out_at).getTime() < 2 * 60 * 60 * 1000)
      .sort((a, b) => new Date(b.checked_out_at) - new Date(a.checked_out_at)),
    [appointments],
  );

  async function runAction(appt, endpoint, payload = {}) {
    try {
      const { data } = await api.post(`/appointments/${appt.id}/${endpoint}`, payload);
      setAppointments((prev) =>
        (prev || []).map((a) => (a.id === data.id ? { ...a, ...data } : a)),
      );
      toast.success("Updated");
      return data;
    } catch (err) {
      toast.error(err.response?.data?.detail || "Action failed");
      return null;
    }
  }

  async function completeCheckout(appt, { checkout_notes, checkout_summary }) {
    // If the appointment is still in ready_for_checkout, advance to completed
    // first (required precondition) — minimal-click for front desk.
    if (appt.status === "ready_for_checkout") {
      const done = await runAction(appt, "complete");
      if (!done) return;
    }
    const data = await runAction(appt, "checkout", { checkout_notes, checkout_summary });
    if (data) setCompleteDialog(null);
  }

  return (
    <div data-testid="checkout-page" className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Front desk
          </span>
          <h1 className="mt-1 font-display text-3xl font-medium tracking-tight">Checkout</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Finish the operational end of each visit — notes and summary
            feed downstream payment / follow-up hooks.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          data-testid="checkout-refresh"
          onClick={() => fetchData({ silent: true })}
          disabled={refreshing}
          className="rounded-sm"
        >
          <RefreshCw className={`mr-1.5 h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <CheckoutSection
        testid="checkout-ready"
        title="Ready for Checkout"
        subtitle="Provider marked these patients ready — send them to the counter."
        rows={readyForCheckout}
        loading={appointments === null}
        emptyText="No patients waiting."
        renderActions={(a) => (
          <div className="flex gap-1.5">
            <Button
              size="sm"
              data-testid={`checkout-start-${a.id}`}
              onClick={() => runAction(a, "start-checkout")}
              className="h-8 rounded-sm bg-primary px-3 text-xs hover:bg-[var(--primary-hover)]"
            >
              <PlayCircle className="mr-1 h-3 w-3" />
              Start checkout
            </Button>
            <Button
              size="sm"
              variant="outline"
              data-testid={`checkout-complete-from-ready-${a.id}`}
              onClick={() => setCompleteDialog({ appointment: a })}
              className="h-8 rounded-sm px-3 text-xs"
            >
              <CheckCircle2 className="mr-1 h-3 w-3" />
              Complete now
            </Button>
          </div>
        )}
      />

      <CheckoutSection
        testid="checkout-at-counter"
        title="At Checkout Counter"
        subtitle="Patient is at the counter — finalize when done."
        rows={atCheckout}
        loading={appointments === null}
        emptyText="No patients at the counter."
        renderActions={(a) => (
          <Button
            size="sm"
            data-testid={`checkout-complete-${a.id}`}
            onClick={() => setCompleteDialog({ appointment: a })}
            className="h-8 rounded-sm bg-primary px-3 text-xs hover:bg-[var(--primary-hover)]"
          >
            <CheckCircle2 className="mr-1 h-3 w-3" />
            Complete checkout
          </Button>
        )}
      />

      <CheckoutSection
        testid="checkout-recent"
        title="Recently Checked Out"
        subtitle="Still physically in the clinic — mark departed on their way out."
        rows={recentlyCheckedOut}
        loading={appointments === null}
        emptyText="No recent checkouts."
        renderActions={(a) =>
          a.current_location_type === "departed" ? (
            <Badge
              data-testid={`checkout-departed-${a.id}`}
              variant="outline"
              className="rounded-sm"
            >
              <LogOut className="mr-1 h-3 w-3" />
              Departed
            </Badge>
          ) : (
            <Button
              size="sm"
              data-testid={`checkout-depart-${a.id}`}
              onClick={() => runAction(a, "depart")}
              variant="outline"
              className="h-8 rounded-sm px-3 text-xs"
            >
              <LogOut className="mr-1 h-3 w-3" />
              Mark departed
            </Button>
          )
        }
      />

      <CompleteCheckoutDialog
        open={!!completeDialog}
        appointment={completeDialog?.appointment || null}
        onClose={() => setCompleteDialog(null)}
        onSubmit={(values) => completeCheckout(completeDialog.appointment, values)}
      />
    </div>
  );
}

function CheckoutSection({ testid, title, subtitle, rows, loading, emptyText, renderActions }) {
  return (
    <section data-testid={testid} className="rounded-sm border border-border bg-card">
      <header className="flex items-center justify-between gap-2 border-b border-border px-5 py-3">
        <div className="flex items-center gap-2">
          <h2 className="font-display text-base font-medium">{title}</h2>
          <span
            data-testid={`${testid}-count`}
            className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
          >
            {rows.length}
          </span>
        </div>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </header>
      <div className="divide-y divide-border">
        {loading ? (
          <div className="space-y-2 p-4">
            {[0, 1].map((i) => <Skeleton key={i} className="h-20 rounded-sm" />)}
          </div>
        ) : rows.length === 0 ? (
          <div
            data-testid={`${testid}-empty`}
            className="px-5 py-10 text-center text-sm text-muted-foreground"
          >
            {emptyText}
          </div>
        ) : (
          rows.map((a) => (
            <CheckoutRow key={a.id} appointment={a} actions={renderActions(a)} />
          ))
        )}
      </div>
    </section>
  );
}

function CheckoutRow({ appointment, actions }) {
  const apptTime = appointment.start_time
    ? new Date(appointment.start_time).toLocaleTimeString(undefined, {
        hour: "numeric", minute: "2-digit",
      })
    : null;
  const timings = [
    { key: "provider_done", label: "Provider done", at: appointment.completed_at || appointment.ready_for_checkout_at },
    { key: "at_counter",    label: "At counter",    at: appointment.checkout_started_at },
    { key: "checked_out",   label: "Checked out",   at: appointment.checked_out_at },
  ].filter((t) => t.at);

  const intake = appointment.intake_status || "not_started";
  return (
    <article
      data-testid={`checkout-row-${appointment.id}`}
      className="flex flex-wrap items-center justify-between gap-3 px-5 py-4"
    >
      <div className="min-w-0 space-y-1.5">
        <p className="truncate font-medium">
          <span data-testid={`checkout-row-name-${appointment.id}`}>
            {appointment.patient_name || "Unknown patient"}
          </span>
          {apptTime && <span className="ml-2 text-xs text-muted-foreground">{apptTime}</span>}
          {appointment.provider_name && (
            <span className="ml-1 text-xs text-muted-foreground">· {appointment.provider_name}</span>
          )}
        </p>

        <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
          <Badge
            variant="outline"
            data-testid={`checkout-row-status-${appointment.id}`}
            className="rounded-sm"
          >
            {String(appointment.status || "").replaceAll("_", " ")}
          </Badge>
          {appointment.current_location_type && (
            <Badge
              variant="outline"
              data-testid={`checkout-row-location-${appointment.id}`}
              className="rounded-sm"
            >
              {appointment.current_location_type.replaceAll("_", " ")}
            </Badge>
          )}
          <Badge
            variant="outline"
            data-testid={`checkout-row-intake-${appointment.id}`}
            className={`rounded-sm ${
              intake === "completed" ? "text-emerald-700 dark:text-emerald-300"
              : intake === "in_progress" ? "text-amber-700 dark:text-amber-300"
              : "text-muted-foreground"
            }`}
          >
            <FileText className="mr-1 h-2.5 w-2.5" />
            Intake {intake.replaceAll("_", " ")}
          </Badge>
          {appointment.current_room_name && (
            <Badge variant="outline" className="rounded-sm">
              <Home className="mr-1 h-2.5 w-2.5" />
              Room: {appointment.current_room_name}
            </Badge>
          )}
        </div>

        {timings.length > 0 && (
          <div className="flex flex-wrap items-center gap-3 text-[10px] text-muted-foreground">
            {timings.map((t) => (
              <span
                key={t.key}
                data-testid={`checkout-row-timing-${t.key}-${appointment.id}`}
                className="inline-flex items-center gap-1"
              >
                <Clock className="h-2.5 w-2.5" />
                {t.label} · {humanDuration(t.at)} ago
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="shrink-0">{actions}</div>
    </article>
  );
}

function CompleteCheckoutDialog({ open, appointment, onSubmit, onClose }) {
  const [notes, setNotes] = useState("");
  const [summary, setSummary] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open) {
      setNotes(appointment?.checkout_notes || "");
      setSummary(appointment?.checkout_summary || "");
    }
  }, [open, appointment]);

  async function submit(e) {
    e.preventDefault();
    setSaving(true);
    try {
      await onSubmit({
        checkout_notes: notes.trim() || null,
        checkout_summary: summary.trim() || null,
      });
    } finally {
      setSaving(false);
    }
  }

  if (!appointment) return null;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="checkout-dialog" className="max-w-xl rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Complete checkout</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          {/* Summary context */}
          <div className="rounded-sm border border-border bg-muted/50 px-4 py-3 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="font-medium">{appointment.patient_name || "Unknown patient"}</p>
                <p className="text-xs text-muted-foreground">
                  {appointment.start_time
                    ? new Date(appointment.start_time).toLocaleString(undefined, {
                        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
                      })
                    : null}
                  {appointment.provider_name ? ` · ${appointment.provider_name}` : ""}
                </p>
              </div>
              <Badge
                variant="outline"
                data-testid="checkout-dialog-status"
                className="rounded-sm"
              >
                {String(appointment.status || "").replaceAll("_", " ")}
              </Badge>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <Stethoscope className="h-3 w-3" />
                Provider: {appointment.completed_at ? "complete" : "in progress"}
              </span>
              <ChevronRight className="h-3 w-3" />
              <span className="inline-flex items-center gap-1">
                <Play className="h-3 w-3" />
                Ready: {humanDuration(appointment.ready_for_checkout_at) || "—"} ago
              </span>
              <ChevronRight className="h-3 w-3" />
              <span className="inline-flex items-center gap-1">
                <FileText className="h-3 w-3" />
                Intake: {(appointment.intake_status || "not_started").replaceAll("_", " ")}
              </span>
              {appointment.current_room_name && (
                <>
                  <ChevronRight className="h-3 w-3" />
                  <span className="inline-flex items-center gap-1">
                    <Home className="h-3 w-3" />
                    Room: {appointment.current_room_name}
                  </span>
                </>
              )}
            </div>
          </div>

          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1">
              <Label htmlFor="checkout-notes">Checkout notes (operational)</Label>
              <Textarea
                id="checkout-notes"
                data-testid="checkout-dialog-notes"
                value={notes}
                maxLength={2000}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Follow-up booked, receipt printed, paid co-pay…"
                rows={3}
                className="rounded-sm"
              />
              <p className="text-[10px] text-muted-foreground">
                Stored encrypted at rest. Max 2,000 characters.
              </p>
            </div>
            <div className="space-y-1">
              <Label htmlFor="checkout-summary">Visit summary (optional)</Label>
              <Textarea
                id="checkout-summary"
                data-testid="checkout-dialog-summary"
                value={summary}
                maxLength={4000}
                onChange={(e) => setSummary(e.target.value)}
                placeholder="Printable summary for the patient / downstream systems."
                rows={4}
                className="rounded-sm"
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose} className="rounded-sm">
                Cancel
              </Button>
              <Button
                type="submit"
                data-testid="checkout-dialog-submit"
                disabled={saving}
                className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
              >
                {saving ? "Completing…" : "Complete checkout"}
              </Button>
            </DialogFooter>
          </form>
        </div>
      </DialogContent>
    </Dialog>
  );
}
