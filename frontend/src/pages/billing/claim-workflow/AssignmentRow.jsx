import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { UserCog } from "lucide-react";
import { Button } from "../../../components/ui/button";
import { Label } from "../../../components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../../components/ui/select";
import { useAuth } from "../../../contexts/AuthContext";
import {
  fetchAssignableUsers,
  updateClaimAssignment,
} from "../useClaims";

const UNASSIGNED = "__unassigned__";

/** Assignee picker — renders a tenant-scoped dropdown of billable
 *  staff so users never see a raw user-id. The pre-selected option
 *  and the read-only summary both use the server-resolved display
 *  name. */
export function AssignmentRow({ claim, assignee, onSaved }) {
  const { user } = useAuth();
  const [value, setValue] = useState(claim.assigned_to || "");
  const [users, setUsers] = useState([]);
  const [loadingUsers, setLoadingUsers] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => { setValue(claim.assigned_to || ""); }, [claim.assigned_to]);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const rows = await fetchAssignableUsers();
        if (alive) setUsers(rows);
      } catch (e) {
        if (alive) toast.error(
          e?.response?.data?.detail || "Could not load assignable users",
        );
      } finally {
        if (alive) setLoadingUsers(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  async function save(explicit) {
    const next = explicit === undefined ? (value || null) : explicit;
    setSaving(true);
    try {
      await updateClaimAssignment(claim.id, next);
      toast.success(next ? "Assignment saved" : "Assignment cleared");
      onSaved?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not update assignment");
    } finally { setSaving(false); }
  }

  const isMine = user?.id && claim.assigned_to === user.id;
  const currentLabel = useMemo(() => {
    if (!claim.assigned_to) return "Unassigned";
    if (assignee?.name) return assignee.name;
    const hit = users.find((u) => u.id === claim.assigned_to);
    if (hit?.name) return hit.name;
    return "Unknown user";
  }, [claim.assigned_to, assignee, users]);

  return (
    <div
      data-testid="claim-assignment-row"
      className="mb-4 flex flex-wrap items-end gap-3 rounded-sm bg-muted/40 p-3"
    >
      <div className="flex items-center gap-2">
        <UserCog className="h-4 w-4 text-muted-foreground" />
        <Label htmlFor="cw-assignee" className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
          Assignee
        </Label>
      </div>
      <div className="flex-1 min-w-[18rem]">
        <Select
          value={value || UNASSIGNED}
          onValueChange={(v) => setValue(v === UNASSIGNED ? "" : v)}
          disabled={loadingUsers || saving}
        >
          <SelectTrigger data-testid="claim-assignee-select" className="h-9">
            <SelectValue placeholder={loadingUsers ? "Loading users…" : "Select assignee"}>
              {currentLabel}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={UNASSIGNED}>
              <span className="text-muted-foreground">Unassigned</span>
            </SelectItem>
            {users.map((u) => (
              <SelectItem key={u.id} value={u.id} data-testid={`claim-assignee-option-${u.id}`}>
                <span className="flex flex-col">
                  <span>{u.name}</span>
                  <span className="text-[11px] text-muted-foreground capitalize">
                    {u.role?.replace(/_/g, " ")} · {u.email}
                  </span>
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Button
        size="sm"
        onClick={() => save()}
        disabled={saving || (value || "") === (claim.assigned_to || "")}
        data-testid="claim-assignee-save"
        className="rounded-sm"
      >
        {saving ? "Saving…" : "Save"}
      </Button>
      {user?.id && !isMine && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => save(user.id)}
          disabled={saving}
          data-testid="claim-assignee-self-assign"
          className="rounded-sm"
        >
          Assign to me
        </Button>
      )}
      {claim.assigned_to && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => save(null)}
          disabled={saving}
          data-testid="claim-assignee-clear"
          className="rounded-sm text-xs text-muted-foreground"
        >
          Unassign
        </Button>
      )}
    </div>
  );
}
