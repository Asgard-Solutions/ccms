import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, DoorOpen, DoorClosed, Home } from "lucide-react";
import { api } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../../components/ui/popover";

/**
 * Reusable room-assignment control.
 *
 * Renders:
 *   - a `Current room` badge with an explicit "override" pill when the
 *     backend flagged the last assignment as forced.
 *   - an "Assign / Change room" popover listing the active rooms for the
 *     appointment's location with live occupancy indicators.
 *   - a "Clear room" action that optionally returns the patient to the
 *     waiting room.
 *
 * Props:
 *   - appointment: full AppointmentPublic object
 *   - rooms: Room[] (active, scoped to the appointment's location)
 *   - occupantByRoomId: Map<roomId, {appointment_id, patient_name}>
 *   - onUpdated(updated): callback with the latest appointment
 *   - compact: small layout for the flow board
 */
export default function RoomAssignmentControl({
  appointment,
  rooms,
  occupantByRoomId,
  onUpdated,
  compact = false,
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [isOverride, setIsOverride] = useState(false);

  // When the appointment is loaded, check the most recent history row to
  // detect an active override. We lazy-fetch the history only when the
  // component mounts OR the room changes — keeps network chatty to a min.
  useEffect(() => {
    let cancelled = false;
    if (!appointment?.id || !appointment?.current_room_id) {
      setIsOverride(false);
      return;
    }
    (async () => {
      try {
        const { data } = await api.get(`/appointments/${appointment.id}/room-history`);
        const last = (data || [])
          .filter((h) => h.to_room_id === appointment.current_room_id)
          .slice(-1)[0];
        if (!cancelled) setIsOverride(!!last?.forced);
      } catch {
        /* non-fatal */
      }
    })();
    return () => { cancelled = true; };
  }, [appointment?.id, appointment?.current_room_id]);

  const scopedRooms = useMemo(() => {
    if (!appointment?.location_id) return rooms;
    return (rooms || []).filter((r) => r.location_id === appointment.location_id);
  }, [rooms, appointment?.location_id]);

  async function assign(roomId, { force = false, reason = null } = {}) {
    if (!appointment?.id) return;
    setBusy(true);
    try {
      const { data } = await api.post(`/appointments/${appointment.id}/room`, {
        room_id: roomId,
        force,
        reason,
      });
      onUpdated?.(data);
      toast.success(force ? "Room assigned (override)" : "Room assigned");
      setOpen(false);
    } catch (err) {
      const status = err.response?.status;
      const detail = err.response?.data?.detail || "Failed to assign";
      if (status === 409 && !force) {
        const reasonText = window.prompt(
          `${detail}\n\nType a reason to override single-occupancy and assign anyway:`,
        );
        if (reasonText && reasonText.trim()) {
          await assign(roomId, { force: true, reason: reasonText.trim() });
        }
      } else {
        toast.error(detail);
      }
    } finally {
      setBusy(false);
    }
  }

  async function clearRoom({ returnToWaiting = false } = {}) {
    if (!appointment?.id) return;
    setBusy(true);
    try {
      const { data } = await api.post(
        `/appointments/${appointment.id}/clear-room`,
        null,
        { params: { return_to_waiting: returnToWaiting } },
      );
      onUpdated?.(data);
      toast.success(returnToWaiting ? "Returned to waiting room" : "Room cleared");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to clear");
    } finally {
      setBusy(false);
    }
  }

  const current = appointment?.current_room_id
    ? {
        id: appointment.current_room_id,
        name: appointment.current_room_name,
        type: appointment.current_room_type,
      }
    : null;

  const size = compact ? "sm" : "default";
  const btnSize = compact ? "h-7 rounded-sm px-2 text-[11px]" : "rounded-sm";

  return (
    <div
      data-testid="room-assignment-control"
      className={`flex flex-wrap items-center gap-1.5 ${compact ? "text-[11px]" : "text-xs"}`}
    >
      {current ? (
        <>
          <Badge
            data-testid={`room-current-${appointment.id}`}
            variant="outline"
            className="rounded-sm"
          >
            <Home className="mr-1 h-3 w-3" />
            Room: {current.name || current.id.slice(0, 6)}
          </Badge>
          {isOverride && (
            <Badge
              data-testid={`room-override-${appointment.id}`}
              variant="destructive"
              className="rounded-sm"
              title="This room was assigned via single-occupancy override — a conflict may still be active"
            >
              <AlertTriangle className="mr-1 h-3 w-3" />
              Override
            </Badge>
          )}
        </>
      ) : (
        <Badge
          data-testid={`room-none-${appointment.id}`}
          variant="outline"
          className="rounded-sm text-muted-foreground"
        >
          No room
        </Badge>
      )}

      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            size={size}
            variant="outline"
            data-testid={`room-assign-btn-${appointment.id}`}
            disabled={busy}
            className={btnSize}
          >
            <DoorOpen className="mr-1 h-3 w-3" />
            {current ? "Change" : "Assign"}
          </Button>
        </PopoverTrigger>
        <PopoverContent
          data-testid={`room-picker-${appointment.id}`}
          className="w-64 rounded-sm p-2"
        >
          <p className="mb-1.5 px-1 text-[11px] uppercase tracking-wider text-muted-foreground">
            Active rooms
          </p>
          <div className="max-h-72 space-y-1 overflow-y-auto">
            {scopedRooms.length === 0 && (
              <p
                data-testid={`room-picker-empty-${appointment.id}`}
                className="rounded-sm border border-dashed border-border p-2 text-center text-xs text-muted-foreground"
              >
                No active rooms for this location. Add rooms in Clinic
                Settings → Rooms.
              </p>
            )}
            {scopedRooms.map((r) => {
              const occ = occupantByRoomId?.get(r.id);
              const occupied = !!occ && occ.appointment_id !== appointment.id;
              return (
                <button
                  key={r.id}
                  type="button"
                  disabled={busy}
                  data-testid={`room-picker-option-${appointment.id}-${r.id}`}
                  onClick={() => assign(r.id)}
                  className={`flex w-full items-center justify-between rounded-sm border px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted ${
                    r.id === current?.id ? "border-primary bg-primary/10" : "border-border"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="font-medium">{r.name}</span>
                    <span className="ml-1 text-[10px] text-muted-foreground">
                      {r.type}
                    </span>
                  </span>
                  {occupied ? (
                    <Badge
                      variant="destructive"
                      className="rounded-sm text-[9px]"
                      title={`Occupied by ${occ?.patient_name || "another patient"}`}
                    >
                      Occupied
                    </Badge>
                  ) : r.id === current?.id ? (
                    <Badge variant="outline" className="rounded-sm text-[9px]">
                      Current
                    </Badge>
                  ) : (
                    <DoorClosed className="h-3 w-3 text-muted-foreground" />
                  )}
                </button>
              );
            })}
          </div>
        </PopoverContent>
      </Popover>

      {current && (
        <>
          <Button
            type="button"
            size={size}
            variant="ghost"
            data-testid={`room-return-waiting-btn-${appointment.id}`}
            disabled={busy}
            onClick={() => clearRoom({ returnToWaiting: true })}
            className={btnSize}
          >
            Return to waiting
          </Button>
          <Button
            type="button"
            size={size}
            variant="ghost"
            data-testid={`room-clear-btn-${appointment.id}`}
            disabled={busy}
            onClick={() => clearRoom({ returnToWaiting: false })}
            className={btnSize}
          >
            Clear
          </Button>
        </>
      )}
    </div>
  );
}
