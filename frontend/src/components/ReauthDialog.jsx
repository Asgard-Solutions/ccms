import { useState } from "react";
import { toast } from "sonner";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { useAuth } from "../contexts/AuthContext";

/**
 * Re-authentication dialog for sensitive actions (delete patient, add medical
 * record, admin operations). Calls /auth/reauth which sets a 5-minute cookie;
 * the caller can then immediately retry the sensitive request.
 */
export default function ReauthDialog({ open, title, description, onConfirmed, onClose }) {
  const { reauth, formatApiError } = useAuth();
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await reauth(password);
      toast.success("Identity confirmed — you have 5 minutes.");
      setPassword("");
      onConfirmed();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="reauth-dialog" className="max-w-md rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">{title || "Confirm it's you"}</DialogTitle>
          <DialogDescription>
            {description || "This action is logged to the audit trail. Please re-enter your password."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="reauth-pw">Password</Label>
            <Input
              id="reauth-pw"
              data-testid="reauth-password-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              required
              className="rounded-sm"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} className="rounded-sm">
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={submitting || !password}
              data-testid="reauth-confirm-btn"
              className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            >
              {submitting ? "Confirming…" : "Confirm"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
