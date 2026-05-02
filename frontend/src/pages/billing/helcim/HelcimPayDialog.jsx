import { useEffect, useRef, useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../../../components/ui/dialog";
import { Button } from "../../../components/ui/button";
import {
  initializeHelcimCheckout,
  captureHelcimCheckout,
} from "./api";

const SCRIPT_ID = "helcim-pay-script";
const FRAME_ID = "helcim-pay-frame";

/**
 * Loads the HelcimPay.js script tag once. Returns a promise that
 * resolves when `window.appendHelcimPayIframe` is available.
 */
function loadHelcimPayScript(scriptUrl) {
  if (window.appendHelcimPayIframe) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let s = document.getElementById(SCRIPT_ID);
    if (s) {
      s.addEventListener("load", () => resolve(), { once: true });
      s.addEventListener("error", () => reject(new Error("Helcim script failed to load")), { once: true });
      return;
    }
    s = document.createElement("script");
    s.id = SCRIPT_ID;
    s.src = scriptUrl;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Helcim script failed to load"));
    document.head.appendChild(s);
  });
}

function removeIframe() {
  const f = document.getElementById(FRAME_ID);
  if (f) f.remove();
}

/**
 * HelcimPay modal — opens the Helcim-hosted iframe, listens for the
 * postMessage transaction result, and posts the result back to our
 * `/checkout/capture` endpoint to record it locally.
 */
export default function HelcimPayDialog({
  open, onClose, onSuccess,
  amountCents, currency = "USD",
  invoiceId, patientId, customerCode, description,
  paymentType = "purchase",
}) {
  const [phase, setPhase] = useState("idle"); // idle | initializing | awaiting | capturing | done | error
  const [error, setError] = useState(null);
  const sessionRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setPhase("initializing");
    setError(null);

    const handleMessage = async (event) => {
      // Helcim posts back a `helcim-pay-js-` typed event.
      if (typeof event.data !== "object" || !event.data) return;
      const t = event.data.eventName;
      if (typeof t !== "string" || !t.startsWith("helcim-pay-js-")) return;

      // SUCCESS
      if (t.endsWith("success")) {
        const payload = event.data.eventMessage;
        const session = sessionRef.current;
        if (!session) return;
        try {
          setPhase("capturing");
          let parsed = payload;
          if (typeof payload === "string") {
            try {
              parsed = JSON.parse(payload);
            } catch (_) {
              parsed = { raw: payload };
            }
          }
          const txn = parsed?.data?.data || parsed?.data || parsed || {};
          await captureHelcimCheckout({
            session_id: session.session_id,
            transaction_id: txn.transactionId ? String(txn.transactionId) : null,
            card_token: txn.cardToken || null,
            customer_code: txn.customerCode || null,
            approval_code: txn.approvalCode || null,
            amount: typeof txn.amount === "number" ? txn.amount : null,
            currency: txn.currency || currency,
            response: 1,
            response_message: txn.response || null,
            raw: parsed,
          });
          setPhase("done");
          toast.success("Payment approved.");
          onSuccess?.({ ...txn, session_id: session.session_id });
        } catch (e) {
          setPhase("error");
          setError(e?.response?.data?.detail || "Failed to record payment.");
        } finally {
          removeIframe();
        }
      }

      // ABORTED / DECLINED
      if (t.endsWith("aborted") || t.endsWith("error") || t.endsWith("declined")) {
        const session = sessionRef.current;
        const payload = event.data.eventMessage;
        if (session) {
          try {
            await captureHelcimCheckout({
              session_id: session.session_id,
              response: 0,
              response_message: typeof payload === "string" ? payload : "declined",
              raw: payload,
            });
          } catch (_) {
            // best-effort
          }
        }
        setPhase("error");
        setError("Payment was declined or cancelled.");
        removeIframe();
      }
    };

    (async () => {
      try {
        const session = await initializeHelcimCheckout({
          amount_cents: amountCents,
          currency,
          payment_type: paymentType,
          invoice_id: invoiceId || null,
          customer_code: customerCode || null,
          patient_id: patientId || null,
          description: description || null,
        });
        if (cancelled) return;
        sessionRef.current = session;
        await loadHelcimPayScript(session.script_url);
        if (cancelled) return;
        setPhase("awaiting");
        window.addEventListener("message", handleMessage);
        // Mount the Helcim iframe.
        if (typeof window.appendHelcimPayIframe === "function") {
          window.appendHelcimPayIframe(session.checkout_token);
        } else {
          throw new Error("HelcimPay.js failed to attach to window.");
        }
      } catch (e) {
        if (cancelled) return;
        setPhase("error");
        setError(e?.response?.data?.detail || e?.message || "Failed to start payment.");
      }
    })();

    return () => {
      cancelled = true;
      window.removeEventListener("message", handleMessage);
      removeIframe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleClose = () => {
    removeIframe();
    onClose?.();
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent data-testid="helcim-pay-dialog" className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-display flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-primary" />
            Take payment via Helcim
          </DialogTitle>
          <DialogDescription>
            Card data is captured directly by Helcim — it never touches our servers.
            Total: <span className="font-semibold text-foreground">${(amountCents / 100).toFixed(2)} {currency}</span>
          </DialogDescription>
        </DialogHeader>

        {phase === "initializing" && (
          <div data-testid="helcim-pay-initializing" className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Preparing secure session…
          </div>
        )}

        {phase === "awaiting" && (
          <div data-testid="helcim-pay-awaiting" className="text-sm text-muted-foreground">
            The Helcim secure form has opened. Complete the card entry, then this
            window will close automatically.
          </div>
        )}

        {phase === "capturing" && (
          <div data-testid="helcim-pay-capturing" className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Recording payment…
          </div>
        )}

        {phase === "done" && (
          <div data-testid="helcim-pay-done" className="rounded-sm border border-primary/30 bg-primary/5 p-3 text-sm text-primary">
            Payment recorded.
          </div>
        )}

        {phase === "error" && error && (
          <div data-testid="helcim-pay-error" className="rounded-sm border border-destructive/40 bg-destructive-soft p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <div className="mt-2 flex justify-end">
          <Button data-testid="helcim-pay-close" variant="outline" onClick={handleClose}>
            {phase === "done" ? "Close" : "Cancel"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
