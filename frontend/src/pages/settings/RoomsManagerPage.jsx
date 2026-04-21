import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Plus, Pencil, Archive, ArchiveRestore, Trash2 } from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";

const ROOM_TYPES = [
  { value: "exam",    label: "Exam" },
  { value: "consult", label: "Consult" },
  { value: "xray",    label: "X-Ray" },
  { value: "therapy", label: "Therapy" },
  { value: "other",   label: "Other" },
];

function typeLabel(t) {
  return ROOM_TYPES.find((x) => x.value === t)?.label || t;
}

/**
 * Rooms Manager — clinic-settings page where admins create, rename,
 * re-type, reorder, deactivate, and (when unused) delete rooms.
 *
 * Backed by `GET/POST/PATCH/DELETE /api/rooms` and tenant-scoped. Rooms
 * with any historical assignment can only be deactivated, never hard
 * deleted — preserves the audit/operational trail.
 */
export default function RoomsManagerPage() {
  const [locations, setLocations] = useState([]);
  const [locationId, setLocationId] = useState("");
  const [rooms, setRooms] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/tenancy/me/context");
        const locs = data?.locations || [];
        setLocations(locs);
        if (locs.length && !locationId) setLocationId(locs[0].id);
      } catch (err) {
        toast.error(err.response?.data?.detail || "Failed to load locations");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!locationId) return;
    (async () => {
      setRooms(null);
      try {
        const { data } = await api.get("/rooms", { params: { location_id: locationId } });
        setRooms(data || []);
      } catch (err) {
        toast.error(err.response?.data?.detail || "Failed to load rooms");
        setRooms([]);
      }
    })();
  }, [locationId]);

  const sortedRooms = useMemo(
    () => [...(rooms || [])].sort((a, b) =>
      (a.sort_order - b.sort_order) || a.name.localeCompare(b.name)),
    [rooms],
  );

  async function saveRoom(form) {
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        type: form.type,
        sort_order: Number(form.sort_order) || 0,
        notes: form.notes || null,
      };
      if (dialog?.mode === "edit") {
        const { data } = await api.patch(`/rooms/${dialog.room.id}`, payload);
        setRooms((xs) => (xs || []).map((r) => (r.id === data.id ? data : r)));
        toast.success("Room updated");
      } else {
        const { data } = await api.post("/rooms", { ...payload, location_id: locationId });
        setRooms((xs) => [...(xs || []), data]);
        toast.success("Room created");
      }
      setDialog(null);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function toggleActive(room) {
    try {
      const { data } = await api.patch(`/rooms/${room.id}`, { is_active: !room.is_active });
      setRooms((xs) => (xs || []).map((r) => (r.id === data.id ? data : r)));
      toast.success(data.is_active ? "Room reactivated" : "Room deactivated");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to update");
    }
  }

  async function deleteRoom(room) {
    if (!window.confirm(`Delete room "${room.name}"? This can only succeed if the room has never been used.`)) return;
    try {
      await api.delete(`/rooms/${room.id}`);
      setRooms((xs) => (xs || []).filter((r) => r.id !== room.id));
      toast.success("Room deleted");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Deactivate instead");
    }
  }

  return (
    <div data-testid="rooms-manager-page" className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Clinic settings
          </span>
          <h1 className="mt-1 font-display text-3xl font-medium tracking-tight">Rooms</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage exam, consult, x-ray, therapy, and other physical spaces per location.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {locations.length > 1 && (
            <Select value={locationId} onValueChange={setLocationId}>
              <SelectTrigger data-testid="rooms-location-filter" className="h-10 w-56 rounded-sm">
                <SelectValue placeholder="Select location" />
              </SelectTrigger>
              <SelectContent>
                {locations.map((l) => (
                  <SelectItem key={l.id} value={l.id} data-testid={`rooms-location-${l.id}`}>
                    {l.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button
            data-testid="rooms-new-btn"
            onClick={() => setDialog({ mode: "create", room: { name: "", type: "exam", sort_order: 0, notes: "" } })}
            className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            disabled={!locationId}
          >
            <Plus className="mr-1.5 h-4 w-4" />
            New room
          </Button>
        </div>
      </div>

      {rooms === null ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-14 rounded-sm" />)}
        </div>
      ) : sortedRooms.length === 0 ? (
        <div
          data-testid="rooms-empty"
          className="rounded-sm border border-dashed border-border bg-card p-10 text-center text-sm text-muted-foreground"
        >
          No rooms yet. Click "New room" to add one.
        </div>
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-sm border border-border bg-card">
          {sortedRooms.map((r) => (
            <li
              key={r.id}
              data-testid={`rooms-row-${r.id}`}
              className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 text-sm"
            >
              <div className="min-w-0 flex items-center gap-3">
                <span className="font-mono text-xs text-muted-foreground">#{r.sort_order}</span>
                <div className="min-w-0">
                  <p data-testid={`rooms-row-name-${r.id}`} className="truncate font-medium">
                    {r.name}
                    {!r.is_active && (
                      <Badge variant="outline" className="ml-2 rounded-sm text-[10px]">
                        Inactive
                      </Badge>
                    )}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    <span data-testid={`rooms-row-type-${r.id}`}>{typeLabel(r.type)}</span>
                    {r.notes ? ` · ${r.notes}` : ""}
                  </p>
                </div>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  data-testid={`rooms-edit-${r.id}`}
                  onClick={() => setDialog({ mode: "edit", room: { ...r } })}
                  className="rounded-sm"
                >
                  <Pencil className="mr-1 h-3 w-3" />
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  data-testid={`rooms-toggle-${r.id}`}
                  onClick={() => toggleActive(r)}
                  className="rounded-sm"
                >
                  {r.is_active ? (
                    <><Archive className="mr-1 h-3 w-3" /> Deactivate</>
                  ) : (
                    <><ArchiveRestore className="mr-1 h-3 w-3" /> Reactivate</>
                  )}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  data-testid={`rooms-delete-${r.id}`}
                  onClick={() => deleteRoom(r)}
                  className="rounded-sm text-destructive hover:bg-destructive-soft"
                >
                  <Trash2 className="mr-1 h-3 w-3" />
                  Delete
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <RoomDialog
        open={!!dialog}
        mode={dialog?.mode}
        initial={dialog?.room}
        saving={saving}
        onSave={saveRoom}
        onClose={() => setDialog(null)}
      />
    </div>
  );
}

function RoomDialog({ open, mode, initial, saving, onSave, onClose }) {
  const [form, setForm] = useState({ name: "", type: "exam", sort_order: 0, notes: "" });
  useEffect(() => {
    if (open) {
      setForm({
        name: initial?.name || "",
        type: initial?.type || "exam",
        sort_order: initial?.sort_order ?? 0,
        notes: initial?.notes || "",
      });
    }
  }, [open, initial]);
  const update = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="rooms-dialog" className="max-w-md rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">
            {mode === "edit" ? "Edit room" : "New room"}
          </DialogTitle>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!form.name.trim()) return;
            onSave(form);
          }}
        >
          <div className="space-y-1">
            <Label>Name</Label>
            <Input
              data-testid="rooms-dialog-name"
              autoFocus
              value={form.name}
              onChange={(e) => update("name")(e.target.value)}
              required
              maxLength={80}
              className="rounded-sm"
            />
          </div>
          <div className="space-y-1">
            <Label>Type</Label>
            <Select value={form.type} onValueChange={update("type")}>
              <SelectTrigger data-testid="rooms-dialog-type" className="rounded-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROOM_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value} data-testid={`rooms-dialog-type-${t.value}`}>
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Display order</Label>
            <Input
              data-testid="rooms-dialog-sort"
              type="number"
              value={form.sort_order}
              onChange={(e) => update("sort_order")(e.target.value)}
              className="rounded-sm"
            />
          </div>
          <div className="space-y-1">
            <Label>Notes (optional)</Label>
            <Input
              data-testid="rooms-dialog-notes"
              value={form.notes}
              onChange={(e) => update("notes")(e.target.value)}
              maxLength={500}
              className="rounded-sm"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} className="rounded-sm">
              Cancel
            </Button>
            <Button
              type="submit"
              data-testid="rooms-dialog-save"
              disabled={saving || !form.name.trim()}
              className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
            >
              {saving ? "Saving…" : mode === "edit" ? "Save" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
