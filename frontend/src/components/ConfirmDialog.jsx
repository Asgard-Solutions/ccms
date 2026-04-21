/**
 * ConfirmDialog — Shadcn AlertDialog wrapper used to replace window.confirm().
 *
 * Why this exists:
 *   window.confirm() is silently blocked in the preview iframe, so buttons
 *   that gated destructive actions on it never fired. This component gives
 *   us a consistent, accessible confirmation flow.
 *
 * Usage (controlled):
 *   const [open, setOpen] = useState(false);
 *   <Button onClick={() => setOpen(true)}>Delete</Button>
 *   <ConfirmDialog
 *     open={open}
 *     onOpenChange={setOpen}
 *     title="Delete item?"
 *     description="This cannot be undone."
 *     confirmLabel="Delete"
 *     destructive
 *     onConfirm={async () => { await api.delete(...); }}
 *     testId="item-delete-confirm"
 *   />
 */
import { useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "./ui/alert-dialog";

export default function ConfirmDialog({
  open,
  onOpenChange,
  title = "Are you sure?",
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  onConfirm,
  testId,
}) {
  const [busy, setBusy] = useState(false);

  const handleConfirm = async (e) => {
    // Prevent the AlertDialog from auto-closing until the async op finishes
    // so errors can keep the dialog open.
    e.preventDefault();
    if (!onConfirm) {
      onOpenChange?.(false);
      return;
    }
    setBusy(true);
    try {
      await onConfirm();
      onOpenChange?.(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AlertDialog open={open} onOpenChange={(v) => !busy && onOpenChange?.(v)}>
      <AlertDialogContent
        data-testid={testId}
        className="rounded-sm"
      >
        <AlertDialogHeader>
          <AlertDialogTitle className="font-display">{title}</AlertDialogTitle>
          {description && (
            <AlertDialogDescription>{description}</AlertDialogDescription>
          )}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel
            className="rounded-sm"
            data-testid={testId ? `${testId}-cancel` : undefined}
            disabled={busy}
          >
            {cancelLabel}
          </AlertDialogCancel>
          <AlertDialogAction
            className={`rounded-sm ${
              destructive ? "bg-destructive hover:brightness-95" : ""
            }`}
            data-testid={testId ? `${testId}-confirm` : undefined}
            onClick={handleConfirm}
            disabled={busy}
          >
            {busy ? "Working…" : confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
