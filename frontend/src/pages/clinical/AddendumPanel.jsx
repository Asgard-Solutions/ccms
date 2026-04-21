/**
 * AddendumPanel — Phase 8.
 *
 * Renders beneath a signed parent artifact (follow-up note, initial exam,
 * re-exam). Shows all existing addenda for that parent and lets writers
 * author new ones. Once an addendum is signed, its body becomes
 * immutable — UI reflects this with a lock icon.
 *
 * Permissions are enforced server-side; the UI mirrors the rules so
 * buttons don't appear for users who can't use them:
 *   - Create: any writer (admin/doctor) with a signed parent.
 *   - Edit draft / sign / delete draft: only the addendum author or
 *     an admin.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Lock,
  MessageSquarePlus,
  PenLine,
  ShieldCheck,
  Trash2,
  Loader2,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Skeleton } from "../../components/ui/skeleton";
import { formatDateTime } from "../../utils/time";

/**
 * @param {object} props
 * @param {string} props.patientId
 * @param {"follow_up_note"|"initial_exam"|"re_exam"} props.parentType
 * @param {string} props.parentId
 * @param {boolean} props.parentSigned
 * @param {boolean} props.canWrite
 * @param {{id?:string, role?:string}} [props.currentUser]
 * @param {() => void} [props.onReauthNeeded]
 */
export default function AddendumPanel({
  patientId,
  parentType,
  parentId,
  parentSigned,
  canWrite,
  currentUser,
  onReauthNeeded,
}) {
  const [rows, setRows] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(
        `/patients/${patientId}/clinical/${parentType}/${parentId}/addenda`,
      );
      setRows(data);
    } catch (e) {
      toast.error(formatApiError(e));
      setRows([]);
    }
  }, [patientId, parentType, parentId]);

  useEffect(() => {
    load();
  }, [load]);

  const reauthAware = (err) => {
    if (err?.response?.status === 401 && /re-auth/i.test(err.response?.data?.detail || "")) {
      onReauthNeeded?.();
      return true;
    }
    return false;
  };

  const canAuthorOnRow = (row) => {
    if (!currentUser) return false;
    if (currentUser.role === "admin") return true;
    return row.author_id === currentUser.id;
  };

  const signRow = async (row) => {
    try {
      await api.post(`/patients/${patientId}/clinical/addenda/${row.id}/sign`);
      toast.success("Addendum signed");
      load();
    } catch (e) {
      if (!reauthAware(e)) toast.error(formatApiError(e));
    }
  };

  const deleteRow = async (row) => {
    if (!window.confirm("Delete this draft addendum?")) return;
    try {
      await api.delete(`/patients/${patientId}/clinical/addenda/${row.id}`);
      toast.success("Draft addendum deleted");
      load();
    } catch (e) {
      if (!reauthAware(e)) toast.error(formatApiError(e));
    }
  };

  return (
    <section
      data-testid="addendum-panel"
      className="rounded-lg border border-border bg-card p-5"
    >
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-lg font-semibold text-foreground">
            Addenda
          </h3>
          <p className="text-xs text-muted-foreground">
            Append-only clarifications authored after the parent artifact was
            signed. Each addendum is individually signed and locked.
          </p>
        </div>
        {canWrite && parentSigned && (
          <Button
            size="sm"
            onClick={() => setCreateOpen(true)}
            data-testid="addendum-create-btn"
            className="rounded-sm"
          >
            <MessageSquarePlus className="mr-1.5 h-3.5 w-3.5" />
            New addendum
          </Button>
        )}
      </div>

      {!parentSigned ? (
        <p
          data-testid="addendum-parent-not-signed"
          className="text-xs text-muted-foreground"
        >
          Sign this note first to enable addenda.
        </p>
      ) : rows === null ? (
        <Skeleton className="h-16 rounded-lg" />
      ) : rows.length === 0 ? (
        <p data-testid="addendum-empty" className="text-xs text-muted-foreground">
          No addenda on this note.
        </p>
      ) : (
        <ol data-testid="addendum-list" className="space-y-3">
          {rows.map((a) => {
            const locked = a.status === "signed";
            const authorable = canAuthorOnRow(a);
            return (
              <li
                key={a.id}
                data-testid={`addendum-row-${a.id}`}
                className="rounded-sm border border-border bg-muted/30 p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    variant="outline"
                    data-testid={`addendum-row-${a.id}-status`}
                    className={`text-[10px] uppercase tracking-wider ${
                      locked
                        ? "border-success/40 bg-success-soft text-success"
                        : "border-warning/40 bg-warning-soft text-warning"
                    }`}
                  >
                    {locked ? (
                      <Lock className="mr-1 h-3 w-3" />
                    ) : (
                      <PenLine className="mr-1 h-3 w-3" />
                    )}
                    {locked ? "Signed" : "Draft"}
                  </Badge>
                  <span className="text-xs font-semibold text-foreground">
                    {a.reason}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    by {a.author_name || a.author_id} ·{" "}
                    {locked
                      ? `signed ${formatDateTime(a.signed_at)}`
                      : `drafted ${formatDateTime(a.created_at)}`}
                  </span>
                </div>
                <p className="mt-2 whitespace-pre-wrap text-sm text-foreground">
                  {a.narrative}
                </p>
                {!locked && authorable && canWrite && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setEditing(a)}
                      data-testid={`addendum-row-${a.id}-edit-btn`}
                      className="rounded-sm"
                    >
                      <PenLine className="mr-1.5 h-3 w-3" />
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="default"
                      onClick={() => signRow(a)}
                      data-testid={`addendum-row-${a.id}-sign-btn`}
                      className="rounded-sm"
                    >
                      <ShieldCheck className="mr-1.5 h-3 w-3" />
                      Sign
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => deleteRow(a)}
                      data-testid={`addendum-row-${a.id}-delete-btn`}
                      className="rounded-sm"
                    >
                      <Trash2 className="mr-1.5 h-3 w-3" />
                      Delete
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}

      <AddendumEditDialog
        mode="create"
        open={createOpen}
        onOpenChange={setCreateOpen}
        patientId={patientId}
        parentType={parentType}
        parentId={parentId}
        onSaved={() => {
          setCreateOpen(false);
          load();
        }}
        onReauthNeeded={onReauthNeeded}
      />
      <AddendumEditDialog
        mode="edit"
        open={!!editing}
        addendum={editing}
        onOpenChange={(v) => !v && setEditing(null)}
        patientId={patientId}
        onSaved={() => {
          setEditing(null);
          load();
        }}
        onReauthNeeded={onReauthNeeded}
      />
    </section>
  );
}

function AddendumEditDialog({
  mode,
  open,
  onOpenChange,
  patientId,
  parentType,
  parentId,
  addendum,
  onSaved,
  onReauthNeeded,
}) {
  const [reason, setReason] = useState("");
  const [narrative, setNarrative] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (mode === "edit" && addendum) {
      setReason(addendum.reason || "");
      setNarrative(addendum.narrative || "");
    } else {
      setReason("");
      setNarrative("");
    }
  }, [open, mode, addendum]);

  const submit = async () => {
    if (reason.trim().length < 3) {
      toast.error("Reason must be at least 3 characters");
      return;
    }
    if (narrative.trim().length < 10) {
      toast.error("Narrative must be at least 10 characters");
      return;
    }
    setSaving(true);
    try {
      if (mode === "create") {
        await api.post(
          `/patients/${patientId}/clinical/${parentType}/${parentId}/addenda`,
          { reason: reason.trim(), narrative: narrative.trim() },
        );
        toast.success("Addendum drafted");
      } else {
        await api.patch(
          `/patients/${patientId}/clinical/addenda/${addendum.id}`,
          { reason: reason.trim(), narrative: narrative.trim() },
        );
        toast.success("Addendum updated");
      }
      onSaved();
    } catch (e) {
      if (e?.response?.status === 401 && /re-auth/i.test(e.response?.data?.detail || "")) {
        onReauthNeeded?.();
      } else {
        toast.error(formatApiError(e));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="addendum-edit-dialog" className="max-w-lg rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">
            {mode === "create" ? "New addendum" : "Edit draft addendum"}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Reason
            </Label>
            <Input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Clarify mechanism of injury"
              data-testid="addendum-reason"
              className="rounded-sm"
            />
          </div>
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Narrative
            </Label>
            <Textarea
              rows={6}
              value={narrative}
              onChange={(e) => setNarrative(e.target.value)}
              placeholder="Describe the clarification, update, or additional finding..."
              data-testid="addendum-narrative"
              className="rounded-sm"
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            className="rounded-sm"
          >
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={saving}
            data-testid="addendum-save-btn"
            className="rounded-sm"
          >
            {saving ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <PenLine className="mr-1.5 h-3.5 w-3.5" />
            )}
            {mode === "create" ? "Save draft" : "Save changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
