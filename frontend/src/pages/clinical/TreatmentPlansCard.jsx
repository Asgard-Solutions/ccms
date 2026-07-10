/**
 * TreatmentPlansCard — chart-level plan of care list on the Clinical tab.
 *
 * Shows active plan + historical plans with status chip, frequency,
 * visit progress bar. Provider can launch the editor to create a new
 * plan or open any existing one.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Loader2, PlusCircle, Target, Activity } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import TreatmentPlanProgress from "./TreatmentPlanProgress";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Skeleton } from "../../components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { formatDateTime } from "../../utils/time";

const STATUS_TONE = {
  active: "border-success/40 bg-success-soft text-success",
  on_hold: "border-warning/40 bg-warning-soft text-warning",
  completed: "border-primary/30 bg-primary/10 text-primary",
  discharged: "border-border bg-muted text-muted-foreground",
  cancelled: "border-destructive/30 bg-destructive/10 text-destructive",
};

const STATUS_LABEL = {
  active: "Active",
  on_hold: "On hold",
  completed: "Completed",
  discharged: "Discharged",
  cancelled: "Cancelled",
};

export default function TreatmentPlansCard({ patientId, canWrite, episodes = [], onReauthNeeded }) {
  const [rows, setRows] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("Plan of care");
  const [newEpisodeId, setNewEpisodeId] = useState("");
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/patients/${patientId}/clinical/treatment-plans`);
      setRows(data);
    } catch (e) {
      toast.error(formatApiError(e));
      setRows([]);
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleReauthAware = (err) => {
    if (err?.response?.status === 401 && /re-auth/i.test(err.response?.data?.detail || "")) {
      onReauthNeeded?.();
      return true;
    }
    return false;
  };

  const activeEpisodes = useMemo(
    () => (episodes || []).filter((e) => e.status === "active"),
    [episodes],
  );
  const defaultEpisodeId = useMemo(() => activeEpisodes[0]?.id || "", [activeEpisodes]);

  const openCreateDialog = () => {
    setNewTitle("Plan of care");
    setNewEpisodeId(defaultEpisodeId);
    setCreateOpen(true);
  };

  const submitCreate = async () => {
    const title = newTitle.trim();
    if (title.length < 2) {
      toast.error("Plan title is required");
      return;
    }
    setCreating(true);
    try {
      const body = { title };
      if (newEpisodeId) body.episode_id = newEpisodeId;
      const { data } = await api.post(`/patients/${patientId}/clinical/treatment-plans`, body);
      toast.success("Plan created");
      setCreateOpen(false);
      navigate(`/patients/${patientId}/clinical/treatment-plans/${data.id}`);
    } catch (e) {
      if (!handleReauthAware(e)) toast.error(formatApiError(e));
    } finally {
      setCreating(false);
    }
  };

  return (
    <section data-testid="treatment-plans-card" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-lg font-semibold text-foreground">
            Treatment Plans
          </h3>
          <p className="text-sm text-muted-foreground">
            Plan of care — goals, frequency, duration and discharge criteria.
            One active plan per episode.
          </p>
        </div>
        {canWrite && (
          <Button
            size="sm"
            onClick={openCreateDialog}
            data-testid="plan-create-btn"
            className="rounded-sm"
          >
            <PlusCircle className="mr-1.5 h-3.5 w-3.5" />
            New plan
          </Button>
        )}
      </div>

      {rows === null ? (
        <div className="space-y-2">
          <Skeleton className="h-20 rounded-lg" />
        </div>
      ) : rows.length === 0 ? (
        <div
          data-testid="plans-empty"
          className="rounded-lg border border-dashed border-border bg-card p-8 text-center"
        >
          <Target className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 font-display text-base font-semibold text-foreground">
            No treatment plan yet
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            Start one when the episode moves from initial exam to active care.
          </p>
        </div>
      ) : (
        <div data-testid="plans-list" className="space-y-2">
          {rows.map((p) => {
            const tone = STATUS_TONE[p.plan_status] || STATUS_TONE.active;
            const pg = p.progress || { visits_completed: 0, total_visits: null, percent: null };
            return (
              <button
                key={p.id}
                type="button"
                onClick={() =>
                  navigate(`/patients/${patientId}/clinical/treatment-plans/${p.id}`)
                }
                data-testid={`plan-row-${p.id}`}
                className="block w-full rounded-lg border border-border bg-card p-4 text-left transition-colors hover:bg-muted/40"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Activity className="h-4 w-4 text-muted-foreground" />
                      <span className="font-display text-base font-semibold text-foreground">
                        {p.title}
                      </span>
                      <Badge
                        variant="outline"
                        data-testid={`plan-row-${p.id}-status`}
                        className={`text-[10px] uppercase tracking-wider ${tone}`}
                      >
                        {STATUS_LABEL[p.plan_status] || p.plan_status}
                      </Badge>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                      {p.episode_title && <span>Episode · {p.episode_title}</span>}
                      {p.responsible_provider_name && (
                        <span>Provider · {p.responsible_provider_name}</span>
                      )}
                      <span>Started · {formatDateTime(p.start_date)}</span>
                      {p.frequency_visits_per_week && (
                        <span>{p.frequency_visits_per_week}x/wk</span>
                      )}
                      {p.expected_duration_weeks && (
                        <span>{p.expected_duration_weeks} wks</span>
                      )}
                      {p.re_exam_date && <span>Re-exam · {p.re_exam_date}</span>}
                    </div>
                  </div>
                  <div
                    data-testid={`plan-row-${p.id}-progress`}
                    className="shrink-0 text-right"
                  >
                    <div className="font-display text-sm font-semibold text-foreground">
                      {pg.visits_completed}/{pg.total_visits ?? "—"}
                    </div>
                    <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      {pg.percent != null ? `${pg.percent}%` : "—"} · visits
                    </div>
                  </div>
                </div>
                {/* Legacy thin bar replaced by segmented progress below. */}
                <div className="mt-3">
                  <TreatmentPlanProgress
                    plan={{
                      visits_completed: pg.visits_completed,
                      visits_scheduled: pg.visits_scheduled,
                      total_visits_planned: pg.total_visits,
                    }}
                    testId={`plan-row-${p.id}-segmented`}
                  />
                </div>
              </button>
            );
          })}
        </div>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent
          data-testid="plan-create-dialog"
          className="max-w-md rounded-sm"
        >
          <DialogHeader>
            <DialogTitle className="font-display">New treatment plan</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Plan title
              </Label>
              <Input
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="e.g. 6-week LBP plan"
                data-testid="plan-create-title"
                className="rounded-sm"
                autoFocus
              />
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Episode
              </Label>
              {activeEpisodes.length === 0 ? (
                <p
                  data-testid="plan-create-no-episode"
                  className="text-xs text-muted-foreground"
                >
                  No active episodes — the plan will be created without episode
                  linkage. You can link it later from the editor.
                </p>
              ) : (
                <Select value={newEpisodeId} onValueChange={setNewEpisodeId}>
                  <SelectTrigger
                    data-testid="plan-create-episode"
                    className="rounded-sm"
                  >
                    <SelectValue placeholder="Select an active episode" />
                  </SelectTrigger>
                  <SelectContent>
                    {activeEpisodes.map((ep) => (
                      <SelectItem key={ep.id} value={ep.id}>
                        {ep.title || ep.case_type || ep.id.slice(0, 8)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setCreateOpen(false)}
              className="rounded-sm"
            >
              Cancel
            </Button>
            <Button
              onClick={submitCreate}
              disabled={creating}
              data-testid="plan-create-submit"
              className="rounded-sm"
            >
              {creating ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <PlusCircle className="mr-1.5 h-3.5 w-3.5" />
              )}
              Create plan
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
