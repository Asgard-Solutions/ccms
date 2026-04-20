import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "../api/client";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { KeyRound, Check, X, Clock, Shield } from "lucide-react";

function statusBadge(status) {
  const cls = {
    pending: "surface-warning text-[#D4A373]",
    approved: "surface-sage text-sage-deep",
    rejected: "bg-[#FDE8E3] text-danger-soft",
    expired: "bg-stone-100 text-stone-500",
    used: "surface-sage text-sage-deep",
    revoked: "bg-stone-100 text-stone-500",
  }[status] || "bg-stone-100";
  return <Badge className={cls} data-testid={`elevation-status-${status}`}>{status}</Badge>;
}

export default function Elevation() {
  const [rows, setRows] = useState([]);
  const [perms, setPerms] = useState([]);
  const [dlg, setDlg] = useState(false);
  const [form, setForm] = useState({ permission_key: "", reason: "", ttl_minutes: 30 });
  const [reviewDlg, setReviewDlg] = useState(null);

  const load = async () => {
    try {
      const { data } = await api.get("/authz/elevation");
      setRows(data);
    } catch (err) {
      toast.error("Unable to load elevation requests");
    }
  };

  useEffect(() => {
    load();
    (async () => {
      try {
        const { data } = await api.get("/authz/permissions");
        setPerms(data);
      } catch {
        /* non-admin users just won't see permission list */
      }
    })();
  }, []);

  const createRequest = async () => {
    if (!form.permission_key || form.reason.trim().length < 10) {
      toast.error("Pick a permission and provide a 10+ character justification.");
      return;
    }
    try {
      await api.post("/authz/elevation/request", form);
      toast.success("Elevation request submitted");
      setDlg(false);
      setForm({ permission_key: "", reason: "", ttl_minutes: 30 });
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Request failed");
    }
  };

  const decide = async (decision) => {
    if (!reviewDlg) return;
    try {
      await api.post(`/authz/elevation/${reviewDlg.id}/decision`, {
        decision,
        reason: reviewDlg.decisionReason || null,
      });
      toast.success(`Elevation ${decision}`);
      setReviewDlg(null);
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Decision failed");
    }
  };

  const cancel = async (row) => {
    if (!window.confirm("Cancel this elevation request?")) return;
    try {
      await api.delete(`/authz/elevation/${row.id}`);
      toast.success("Cancelled");
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Cancel failed");
    }
  };

  return (
    <div data-testid="elevation-page" className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-['Outfit'] text-3xl font-medium text-strong">
            Elevation requests
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-strong">
            Request time-bound, justified, approver-gated access to
            high-sensitivity permissions. All approvals and uses are audited.
          </p>
        </div>
        <Button
          data-testid="elevation-new-btn"
          onClick={() => setDlg(true)}
          className="bg-sage hover:bg-sage-hover"
        >
          <KeyRound className="mr-2 h-4 w-4" /> Request elevation
        </Button>
      </header>

      <Card className="rounded-sm">
        <CardHeader>
          <CardTitle className="text-base font-normal">Requests</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-muted-strong">
                <th className="px-2 py-1">Status</th>
                <th className="px-2 py-1">Permission</th>
                <th className="px-2 py-1">Requester</th>
                <th className="px-2 py-1">Reason</th>
                <th className="px-2 py-1">TTL</th>
                <th className="px-2 py-1">Created</th>
                <th className="px-2 py-1">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.id}
                  data-testid={`elevation-row-${r.id}`}
                  className="border-t border-stone-100 align-top"
                >
                  <td className="px-2 py-2">{statusBadge(r.status)}</td>
                  <td className="px-2 py-2 font-mono text-[11px] text-strong">
                    {r.permission_key}
                  </td>
                  <td className="px-2 py-2">
                    <div>{r.requester_email}</div>
                    <div className="text-[11px] text-muted-strong">{r.requester_role}</div>
                  </td>
                  <td className="px-2 py-2 max-w-md text-sm text-muted-strong">{r.reason}</td>
                  <td className="px-2 py-2 text-xs">
                    <Clock className="mr-1 inline h-3 w-3" />
                    {r.ttl_minutes}m
                  </td>
                  <td className="px-2 py-2 text-xs text-stone-400">
                    {new Date(r.created_at).toLocaleString()}
                  </td>
                  <td className="px-2 py-2 space-x-2">
                    {r.status === "pending" && (
                      <>
                        <Button
                          data-testid={`elevation-review-btn-${r.id}`}
                          size="sm"
                          variant="outline"
                          onClick={() => setReviewDlg({ ...r, decisionReason: "" })}
                          className="rounded-sm"
                        >
                          <Shield className="mr-1 h-3 w-3" /> Review
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => cancel(r)}
                          className="rounded-sm text-danger-soft"
                        >
                          Cancel
                        </Button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-3 py-6 text-center text-sm text-muted-strong">
                    No elevation requests yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {/* Create dialog */}
      <Dialog open={dlg} onOpenChange={setDlg}>
        <DialogContent data-testid="elevation-request-dialog" className="rounded-sm">
          <DialogHeader>
            <DialogTitle>Request elevation</DialogTitle>
            <DialogDescription>
              Request temporary access to a high-sensitivity permission. An
              approver (different person) must approve. Access expires after
              the TTL you request.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs uppercase tracking-wider text-muted-strong">
                Permission key
              </label>
              <Input
                data-testid="elevation-perm-input"
                list="elevation-perm-list"
                value={form.permission_key}
                onChange={(e) =>
                  setForm((f) => ({ ...f, permission_key: e.target.value }))
                }
                placeholder="audit_log.export"
              />
              <datalist id="elevation-perm-list">
                {perms.map((p) => (
                  <option key={p.key} value={p.key} />
                ))}
              </datalist>
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-muted-strong">
                Business justification (min 10 chars)
              </label>
              <Textarea
                data-testid="elevation-reason-input"
                value={form.reason}
                onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
                rows={3}
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-muted-strong">
                TTL (minutes, 5–240)
              </label>
              <Input
                data-testid="elevation-ttl-input"
                type="number"
                min={5}
                max={240}
                value={form.ttl_minutes}
                onChange={(e) =>
                  setForm((f) => ({ ...f, ttl_minutes: parseInt(e.target.value || "30", 10) }))
                }
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDlg(false)}>
              Cancel
            </Button>
            <Button
              data-testid="elevation-submit"
              onClick={createRequest}
              disabled={
                !form.permission_key || form.reason.trim().length < 10
              }
              className="bg-sage hover:bg-sage-hover"
            >
              Submit
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Review dialog */}
      <Dialog open={!!reviewDlg} onOpenChange={(o) => !o && setReviewDlg(null)}>
        <DialogContent data-testid="elevation-review-dialog" className="rounded-sm">
          <DialogHeader>
            <DialogTitle>Review elevation request</DialogTitle>
            <DialogDescription>
              {reviewDlg?.requester_email} → <code>{reviewDlg?.permission_key}</code>
              <div className="mt-2 rounded-sm bg-stone-50 p-2 text-sm text-muted-strong">
                {reviewDlg?.reason}
              </div>
            </DialogDescription>
          </DialogHeader>
          <Textarea
            data-testid="elevation-decision-reason"
            placeholder="Optional approval/rejection reason"
            value={reviewDlg?.decisionReason || ""}
            onChange={(e) =>
              setReviewDlg((r) => (r ? { ...r, decisionReason: e.target.value } : r))
            }
          />
          <DialogFooter>
            <Button
              data-testid="elevation-reject"
              variant="outline"
              onClick={() => decide("reject")}
              className="rounded-sm text-danger-soft"
            >
              <X className="mr-1 h-4 w-4" /> Reject
            </Button>
            <Button
              data-testid="elevation-approve"
              onClick={() => decide("approve")}
              className="bg-sage hover:bg-sage-hover"
            >
              <Check className="mr-1 h-4 w-4" /> Approve
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
