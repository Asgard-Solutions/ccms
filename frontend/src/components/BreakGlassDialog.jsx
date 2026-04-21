import { useState } from "react";
import { Shield } from "lucide-react";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { Label } from "./ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";

/**
 * Break-glass dialog: captures a clinical reason for an access that would
 * normally be out-of-scope. Reason is sent to the backend which writes an
 * `emergency_access` audit row.
 */
export default function BreakGlassDialog({ open, onClose, onSubmit, title, description }) {
  const [reason, setReason] = useState("");
  const tooShort = reason.trim().length < 8;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="break-glass-dialog" className="max-w-md rounded-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-display">
            <Shield className="h-5 w-5 text-destructive" />
            {title || "Break-glass access"}
          </DialogTitle>
          <DialogDescription>
            {description ||
              "This patient is outside your normal scope. Your reason will be written to the audit log and reviewed."}
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!tooShort) onSubmit(reason.trim());
          }}
          className="space-y-3"
        >
          <div className="space-y-1">
            <Label>Reason (8+ characters)</Label>
            <Textarea
              data-testid="break-glass-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder="Covering for Dr. Monroe while she is off-service today…"
              autoFocus
              className="rounded-sm"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} className="rounded-sm">
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={tooShort}
              data-testid="break-glass-submit"
              className="rounded-sm bg-destructive text-destructive-foreground hover:brightness-95"
            >
              Record & continue
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
