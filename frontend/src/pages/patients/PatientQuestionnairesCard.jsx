/**
 * Per-patient questionnaires card — shown on the patient chart.
 *
 * Staff can (a) assign a new questionnaire, (b) see completed scores,
 * (c) open pending ones to see the expected delivery status.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { ClipboardList, Plus, Send } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Badge } from "../../components/ui/badge";
import {
  assignQuestionnaire, fetchQuestionnaireTemplates,
  staffListAssignments,
} from "../../api/portal";
import { formatDateTime } from "../../utils/time";

function AssignDialog({ patientId, onClose, onDone }) {
  const [templates, setTemplates] = useState([]);
  const [picked, setPicked] = useState("");
  const [saving, setSaving] = useState(false);
  const [sendSms, setSendSms] = useState(true);

  useEffect(() => {
    fetchQuestionnaireTemplates().then(setTemplates).catch(() => {});
  }, []);

  async function assign() {
    if (!picked) return;
    setSaving(true);
    try {
      await assignQuestionnaire({
        patient_id: patientId,
        template_id: picked,
        send_sms: sendSms,
      });
      toast.success("Questionnaire assigned.");
      onDone();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Assign failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      data-testid="assign-q-dialog"
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-6"
    >
      <div className="w-full max-w-md rounded-md border border-border bg-card p-6 space-y-3">
        <h3 className="text-lg font-medium">Assign questionnaire</h3>
        <ul className="space-y-2">
          {templates.map((t) => (
            <li key={t.id}>
              <label
                className={`flex items-start gap-2 rounded-sm border p-3 cursor-pointer
                  ${picked === t.id ? "border-primary bg-primary/5" : "border-border/60 hover:bg-muted/40"}`}
                data-testid={`assign-q-tpl-${t.id}`}
              >
                <input
                  type="radio"
                  name="tpl"
                  value={t.id}
                  checked={picked === t.id}
                  onChange={() => setPicked(t.id)}
                  className="mt-1"
                />
                <div>
                  <p className="text-sm font-medium">{t.title}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {t.description}
                  </p>
                </div>
              </label>
            </li>
          ))}
        </ul>
        <label className="flex items-center gap-2 text-sm pt-2">
          <input
            type="checkbox"
            checked={sendSms}
            onChange={(e) => setSendSms(e.target.checked)}
            data-testid="assign-q-send-sms"
          />
          Notify patient via SMS (log-only if Twilio not configured)
        </label>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose} data-testid="assign-q-cancel">
            Cancel
          </Button>
          <Button
            onClick={assign}
            disabled={!picked || saving}
            data-testid="assign-q-confirm"
          >
            <Send className="mr-1 h-3.5 w-3.5" />
            {saving ? "Assigning…" : "Assign"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function PatientQuestionnairesCard({ patientId }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAssign, setShowAssign] = useState(false);

  const load = useCallback(async () => {
    if (!patientId) return;
    try {
      setRows(await staffListAssignments({ patient_id: patientId }));
    } catch (_) { /* perms */ }
    finally { setLoading(false); }
  }, [patientId]);

  useEffect(() => { load(); }, [load]);

  return (
    <section
      data-testid="patient-questionnaires-card"
      className="rounded-md border border-border bg-card p-5"
    >
      <header className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <ClipboardList className="h-4 w-4 text-primary" />
          <h3 className="font-medium">Questionnaires</h3>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setShowAssign(true)}
          data-testid="assign-q-open-btn"
          className="h-8 rounded-sm"
        >
          <Plus className="mr-1 h-3.5 w-3.5" />
          Assign
        </Button>
      </header>
      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No questionnaires yet.</p>
      ) : (
        <ul className="divide-y divide-border/60 text-sm">
          {rows.map((r) => (
            <li
              key={r.id}
              data-testid={`patient-q-row-${r.id}`}
              className="py-2 flex items-center justify-between gap-3"
            >
              <div className="min-w-0">
                <p className="font-medium truncate">{r.template_id.toUpperCase()}</p>
                <p className="text-xs text-muted-foreground">
                  Assigned {formatDateTime(r.assigned_at)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {r.status === "completed" ? (
                  <Badge variant="outline">
                    Score {r.score} · {r.interpretation}
                  </Badge>
                ) : (
                  <Badge variant="secondary">Pending</Badge>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {showAssign && (
        <AssignDialog
          patientId={patientId}
          onClose={() => setShowAssign(false)}
          onDone={() => { setShowAssign(false); load(); }}
        />
      )}
    </section>
  );
}
