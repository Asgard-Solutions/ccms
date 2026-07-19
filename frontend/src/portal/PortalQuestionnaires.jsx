/**
 * Patient-facing questionnaire list.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { CheckCircle2, ClipboardList } from "lucide-react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { listMyQuestionnaires } from "../api/portal";
import { formatDateTime } from "../utils/time";

export default function PortalQuestionnaires() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setRows(await listMyQuestionnaires());
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div data-testid="portal-questionnaires-page" className="space-y-4">
      <header>
        <h1 className="text-2xl font-display tracking-tight">Questionnaires</h1>
        <p className="text-sm text-muted-foreground">
          Forms your clinician has asked you to complete.
        </p>
      </header>
      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : rows.length === 0 ? (
        <div className="rounded-md border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          <ClipboardList className="mx-auto mb-2 h-6 w-6" />
          Nothing assigned right now.
        </div>
      ) : (
        <ul className="space-y-2">
          {rows.map((r) => (
            <li
              key={r.id}
              data-testid={`portal-q-list-row-${r.id}`}
              className="flex items-center justify-between rounded-sm border border-border/60 bg-card px-4 py-3"
            >
              <div>
                <p className="font-medium text-sm">{r.template_title}</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {r.status === "completed"
                    ? `Completed ${formatDateTime(r.completed_at)}`
                    : `Due ${formatDateTime(r.due_at)}`}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {r.status === "completed" ? (
                  <Badge variant="outline" className="gap-1">
                    <CheckCircle2 className="h-3 w-3 text-green-600" />
                    Score: {r.score}
                  </Badge>
                ) : (
                  <Link to={`/portal/questionnaires/${r.id}`}>
                    <Button size="sm" data-testid={`portal-q-start-${r.id}`}>
                      Start
                    </Button>
                  </Link>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
