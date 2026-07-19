import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Download, FileText, Mail, Printer, RefreshCw, Send } from "lucide-react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { formatCents } from "../../utils/money";
import { formatDateTime } from "../../utils/time";
import {
  emailStatement,
  generateStatement,
  listStatements,
  statementPdfUrl,
} from "./useRemittance";

export default function PatientStatementsCard({ patientId }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listStatements(patientId));
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not load statements");
    } finally { setLoading(false); }
  }, [patientId]);

  useEffect(() => { refresh(); }, [refresh]);

  async function onGenerate() {
    setBusy(true);
    try {
      await generateStatement(patientId);
      toast.success("Statement generated");
      await refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not generate statement");
    } finally { setBusy(false); }
  }

  async function onSend(stmtId, channel) {
    setBusy(true);
    try {
      const r = await emailStatement(patientId, stmtId, { channel });
      const label = channel === "mail"
        ? "Queued for mail"
        : channel === "portal"
        ? "Visible in patient portal"
        : `Emailed to ${r.to} via ${r.provider}`;
      toast.success(label);
      await refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Delivery failed");
    } finally { setBusy(false); }
  }

  return (
    <section
      data-testid="patient-statements-card"
      className="rounded-sm border border-border bg-card p-6"
    >
      <header className="mb-4 flex items-center justify-between">
        <h2 className="flex items-center gap-2 font-display text-lg font-medium tracking-tight">
          <FileText className="h-4 w-4 text-muted-foreground" /> Statements
        </h2>
        <div className="flex gap-2">
          <Button
            size="sm" variant="ghost"
            onClick={refresh}
            data-testid="statements-refresh-btn"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          <Button
            size="sm"
            onClick={onGenerate}
            disabled={busy}
            className="rounded-sm"
            data-testid="statements-generate-btn"
          >
            {busy ? "Generating…" : "Generate statement"}
          </Button>
        </div>
      </header>

      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No statements generated yet.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            <tr>
              <th className="py-1 pr-2">Generated</th>
              <th className="py-1 pr-2">As of</th>
              <th className="py-1 pr-2 text-right">Balance</th>
              <th className="py-1 pr-2 text-right">Invoices</th>
              <th className="py-1 pr-2">Status</th>
              <th className="py-1" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                data-testid={`statement-row-${r.id}`}
                className="border-t border-border"
              >
                <td className="py-2 pr-2 text-xs text-muted-foreground">
                  {formatDateTime(r.generated_at)}
                </td>
                <td className="py-2 pr-2 text-xs">{r.as_of_date}</td>
                <td className="py-2 pr-2 text-right tabular-nums">
                  {formatCents(r.total_balance_cents)}
                </td>
                <td className="py-2 pr-2 text-right tabular-nums">
                  {r.invoice_count}
                </td>
                <td className="py-2 pr-2">
                  {r.sent_at ? (
                    <Badge
                      variant="outline"
                      data-testid={`statement-status-${r.id}`}
                      className="rounded-sm text-[10px] text-emerald-700 dark:text-emerald-300"
                    >
                      Sent · {r.sent_via}
                    </Badge>
                  ) : (
                    <Badge
                      variant="outline"
                      data-testid={`statement-status-${r.id}`}
                      className="rounded-sm text-[10px] text-muted-foreground"
                    >
                      Not sent
                    </Badge>
                  )}
                </td>
                <td className="py-2 text-right">
                  <a
                    href={statementPdfUrl(patientId, r.id)}
                    target="_blank" rel="noreferrer"
                    data-testid={`statement-pdf-${r.id}`}
                  >
                    <Button size="sm" variant="ghost" className="rounded-sm">
                      <Download className="mr-1 h-3.5 w-3.5" /> PDF
                    </Button>
                  </a>
                  <Button
                    size="sm" variant="ghost"
                    onClick={() => onSend(r.id, "email")}
                    disabled={busy}
                    data-testid={`statement-email-${r.id}`}
                    className="rounded-sm"
                  >
                    <Mail className="mr-1 h-3.5 w-3.5" /> Email
                  </Button>
                  <Button
                    size="sm" variant="ghost"
                    onClick={() => onSend(r.id, "mail")}
                    disabled={busy}
                    data-testid={`statement-mail-${r.id}`}
                    className="rounded-sm"
                  >
                    <Printer className="mr-1 h-3.5 w-3.5" /> Mail
                  </Button>
                  <Button
                    size="sm" variant="ghost"
                    onClick={() => onSend(r.id, "portal")}
                    disabled={busy}
                    data-testid={`statement-portal-${r.id}`}
                    className="rounded-sm"
                  >
                    <Send className="mr-1 h-3.5 w-3.5" /> Portal
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
