import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ClipboardList, Pencil, Plus, RotateCcw, Save, Trash2, X } from "lucide-react";
import { api } from "../api/client";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import { Switch } from "../components/ui/switch";

const EMPTY_DRAFT = {
  name: "",
  default_duration_minutes: 30,
  description: "",
  is_active: true,
  sort_order: 0,
};

/**
 * Appointment Types manager — embedded inside Clinic Settings.
 *
 * Admin-only CRUD UI for tenant-scoped appointment types, each with a
 * display name + default duration (minutes) used to auto-compute the
 * end-time in the Book Appointment modal.
 */
export default function AppointmentTypesManager() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newDraft, setNewDraft] = useState(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      const { data } = await api.get("/appointment-types");
      setRows(data || []);
    } catch {
      toast.error("Could not load appointment types");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  function validDraft(d) {
    if (!d.name.trim()) return "Name is required";
    const mins = Number(d.default_duration_minutes);
    if (!Number.isFinite(mins) || mins < 5 || mins > 480) {
      return "Duration must be 5 – 480 minutes";
    }
    return null;
  }

  async function onCreate() {
    const err = validDraft(newDraft);
    if (err) {
      toast.error(err);
      return;
    }
    setSaving(true);
    try {
      await api.post("/appointment-types", {
        name: newDraft.name.trim(),
        default_duration_minutes: Number(newDraft.default_duration_minutes),
        description: newDraft.description?.trim() || null,
        is_active: newDraft.is_active,
        sort_order: Number(newDraft.sort_order) || 0,
      });
      toast.success(`Added "${newDraft.name.trim()}"`);
      setNewDraft(EMPTY_DRAFT);
      setCreating(false);
      await refresh();
    } catch (e) {
      const detail = e.response?.data?.detail;
      toast.error(
        Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : detail || "Create failed"
      );
    } finally {
      setSaving(false);
    }
  }

  function startEdit(row) {
    setEditingId(row.id);
    setEditDraft({
      name: row.name,
      default_duration_minutes: row.default_duration_minutes,
      description: row.description || "",
      is_active: row.is_active,
      sort_order: row.sort_order ?? 0,
    });
  }

  async function onSaveEdit() {
    const err = validDraft(editDraft);
    if (err) {
      toast.error(err);
      return;
    }
    setSaving(true);
    try {
      await api.put(`/appointment-types/${editingId}`, {
        name: editDraft.name.trim(),
        default_duration_minutes: Number(editDraft.default_duration_minutes),
        description: editDraft.description?.trim() || null,
        sort_order: Number(editDraft.sort_order) || 0,
      });
      toast.success("Saved");
      setEditingId(null);
      await refresh();
    } catch (e) {
      const detail = e.response?.data?.detail;
      toast.error(
        Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : detail || "Save failed"
      );
    } finally {
      setSaving(false);
    }
  }

  async function onDeactivate(row) {
    try {
      await api.delete(`/appointment-types/${row.id}`);
      toast.success(`Deactivated "${row.name}"`);
      await refresh();
    } catch {
      toast.error("Could not deactivate");
    }
  }

  async function onReactivate(row) {
    try {
      await api.post(`/appointment-types/${row.id}/reactivate`);
      toast.success(`Reactivated "${row.name}"`);
      await refresh();
    } catch {
      toast.error("Could not reactivate");
    }
  }

  return (
    <section
      data-testid="appointment-types-manager"
      className="rounded-sm border border-border bg-card p-5"
    >
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ClipboardList className="h-4 w-4 text-primary" />
          <h2 className="font-display text-lg font-medium">Appointment types</h2>
        </div>
        {!creating && (
          <Button
            type="button"
            variant="outline"
            data-testid="appt-type-new-btn"
            onClick={() => setCreating(true)}
            className="rounded-sm"
          >
            <Plus className="mr-2 h-4 w-4" /> New type
          </Button>
        )}
      </div>

      <p className="mb-4 text-xs text-muted-foreground">
        Each type provides a default duration that auto-fills the end time
        in the Book Appointment modal. Inactive types are hidden from
        booking but kept for historical reference.
      </p>

      {creating && (
        <div
          data-testid="appt-type-new-form"
          className="mb-5 rounded-sm border border-dashed border-border bg-muted/40 p-4"
        >
          <div className="grid grid-cols-1 gap-3 md:grid-cols-[2fr_1fr_3fr_auto]">
            <div className="space-y-1">
              <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Name
              </Label>
              <Input
                data-testid="appt-type-new-name"
                value={newDraft.name}
                onChange={(e) => setNewDraft((d) => ({ ...d, name: e.target.value }))}
                placeholder="Initial appointment"
                className="rounded-sm"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Duration (min)
              </Label>
              <Input
                type="number"
                min={5}
                max={480}
                step={5}
                data-testid="appt-type-new-duration"
                value={newDraft.default_duration_minutes}
                onChange={(e) =>
                  setNewDraft((d) => ({ ...d, default_duration_minutes: e.target.value }))
                }
                className="rounded-sm"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Description (optional)
              </Label>
              <Textarea
                rows={1}
                data-testid="appt-type-new-description"
                value={newDraft.description}
                onChange={(e) => setNewDraft((d) => ({ ...d, description: e.target.value }))}
                className="rounded-sm"
              />
            </div>
            <div className="flex items-end gap-2">
              <Button
                type="button"
                onClick={onCreate}
                disabled={saving}
                data-testid="appt-type-new-save-btn"
                className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
              >
                <Save className="mr-2 h-4 w-4" /> {saving ? "Saving…" : "Save"}
              </Button>
              <Button
                type="button"
                variant="ghost"
                data-testid="appt-type-new-cancel-btn"
                onClick={() => {
                  setCreating(false);
                  setNewDraft(EMPTY_DRAFT);
                }}
                className="rounded-sm"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-sm border border-border">
        <table className="w-full text-sm">
          <thead className="border-b border-border bg-muted/50 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-4 py-2 text-left">Name</th>
              <th className="px-4 py-2 text-left">Duration</th>
              <th className="px-4 py-2 text-left">Description</th>
              <th className="px-4 py-2 text-left">Active</th>
              <th className="px-4 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td className="px-4 py-3 text-xs text-muted-foreground" colSpan={5}>
                  Loading…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td
                  data-testid="appt-type-empty"
                  className="px-4 py-6 text-center text-xs text-muted-foreground"
                  colSpan={5}
                >
                  No appointment types yet. Click{" "}
                  <span className="font-semibold">New type</span> to add one.
                </td>
              </tr>
            ) : (
              rows.map((row) => {
                const isEditing = editingId === row.id;
                return (
                  <tr
                    key={row.id}
                    data-testid={`appt-type-row-${row.id}`}
                    className={`border-b border-border last:border-b-0 ${
                      row.is_active ? "" : "opacity-60"
                    }`}
                  >
                    <td className="px-4 py-3 font-medium">
                      {isEditing ? (
                        <Input
                          data-testid={`appt-type-edit-name-${row.id}`}
                          value={editDraft.name}
                          onChange={(e) => setEditDraft((d) => ({ ...d, name: e.target.value }))}
                          className="rounded-sm"
                        />
                      ) : (
                        row.name
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">
                      {isEditing ? (
                        <Input
                          type="number"
                          min={5}
                          max={480}
                          step={5}
                          data-testid={`appt-type-edit-duration-${row.id}`}
                          value={editDraft.default_duration_minutes}
                          onChange={(e) =>
                            setEditDraft((d) => ({
                              ...d,
                              default_duration_minutes: e.target.value,
                            }))
                          }
                          className="w-24 rounded-sm"
                        />
                      ) : (
                        `${row.default_duration_minutes} min`
                      )}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {isEditing ? (
                        <Textarea
                          rows={1}
                          data-testid={`appt-type-edit-description-${row.id}`}
                          value={editDraft.description}
                          onChange={(e) =>
                            setEditDraft((d) => ({ ...d, description: e.target.value }))
                          }
                          className="rounded-sm"
                        />
                      ) : (
                        row.description || "—"
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <Switch
                        data-testid={`appt-type-active-${row.id}`}
                        checked={!!row.is_active}
                        onCheckedChange={(v) =>
                          v ? onReactivate(row) : onDeactivate(row)
                        }
                        disabled={isEditing}
                      />
                    </td>
                    <td className="px-4 py-3 text-right">
                      {isEditing ? (
                        <div className="inline-flex gap-1">
                          <Button
                            type="button"
                            size="sm"
                            onClick={onSaveEdit}
                            disabled={saving}
                            data-testid={`appt-type-save-${row.id}`}
                            className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
                          >
                            <Save className="h-4 w-4" />
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            onClick={() => setEditingId(null)}
                            data-testid={`appt-type-cancel-edit-${row.id}`}
                            className="rounded-sm"
                          >
                            <X className="h-4 w-4" />
                          </Button>
                        </div>
                      ) : (
                        <div className="inline-flex gap-1">
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            onClick={() => startEdit(row)}
                            data-testid={`appt-type-edit-${row.id}`}
                            className="rounded-sm"
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          {row.is_active ? (
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              onClick={() => onDeactivate(row)}
                              data-testid={`appt-type-deactivate-${row.id}`}
                              className="rounded-sm text-destructive hover:bg-destructive-soft"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          ) : (
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              onClick={() => onReactivate(row)}
                              data-testid={`appt-type-reactivate-${row.id}`}
                              className="rounded-sm"
                            >
                              <RotateCcw className="h-4 w-4" />
                            </Button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
