import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, FileText, Plus } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import { formatDate, formatDateTime, relativeFromNow } from "../utils/time";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { Label } from "../components/ui/label";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";

function RecordDialog({ open, onClose, patientId, onAdded }) {
  const [form, setForm] = useState({
    record_type: "assessment",
    title: "",
    description: "",
    diagnosis: "",
    treatment: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const update = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const payload = Object.fromEntries(
        Object.entries(form).filter(([, v]) => v && v.toString().trim() !== "")
      );
      const { data } = await api.post(`/patients/${patientId}/records`, {
        record_type: form.record_type,
        title: form.title,
        ...payload,
      });
      toast.success("Medical record added");
      onAdded(data);
      onClose();
      setForm({
        record_type: "assessment",
        title: "",
        description: "",
        diagnosis: "",
        treatment: "",
      });
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to add record");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="record-create-dialog" className="max-w-lg rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-['Outfit']">Add medical record</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1">
            <Label>Type</Label>
            <Select
              value={form.record_type}
              onValueChange={(v) => setForm({ ...form, record_type: v })}
            >
              <SelectTrigger data-testid="record-type" className="rounded-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="assessment">Assessment</SelectItem>
                <SelectItem value="treatment">Treatment</SelectItem>
                <SelectItem value="note">Note</SelectItem>
                <SelectItem value="diagnosis">Diagnosis</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Title</Label>
            <Input
              required
              data-testid="record-title"
              value={form.title}
              onChange={update("title")}
            />
          </div>
          <div className="space-y-1">
            <Label>Description</Label>
            <Textarea
              data-testid="record-description"
              value={form.description}
              onChange={update("description")}
              rows={3}
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <Label>Diagnosis</Label>
              <Input
                data-testid="record-diagnosis"
                value={form.diagnosis}
                onChange={update("diagnosis")}
              />
            </div>
            <div className="space-y-1">
              <Label>Treatment</Label>
              <Input
                data-testid="record-treatment"
                value={form.treatment}
                onChange={update("treatment")}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              className="rounded-sm"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={submitting}
              data-testid="record-submit-btn"
              className="rounded-sm bg-[#7B9A82] hover:bg-[#65826C]"
            >
              {submitting ? "Saving…" : "Add record"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function PatientDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const canAddRecord = user.role === "admin" || user.role === "doctor";
  const [patient, setPatient] = useState(null);
  const [records, setRecords] = useState(null);
  const [appointments, setAppointments] = useState(null);
  const [recDialog, setRecDialog] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [pRes, rRes, aRes] = await Promise.all([
          api.get(`/patients/${id}`),
          api.get(`/patients/${id}/records`),
          api.get("/appointments", { params: { patient_id: id } }),
        ]);
        if (cancelled) return;
        setPatient(pRes.data);
        setRecords(rRes.data);
        setAppointments(aRes.data);
      } catch (err) {
        toast.error(err.response?.data?.detail || "Failed to load patient");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (!patient) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-32" />
      </div>
    );
  }

  return (
    <div data-testid="patient-detail-page" className="space-y-10 animate-in fade-in duration-300">
      <div>
        <Button variant="ghost" asChild className="text-[#526B58]">
          <Link to="/patients" data-testid="patient-back-link">
            <ArrowLeft className="mr-2 h-4 w-4" /> All patients
          </Link>
        </Button>
      </div>

      <header className="flex flex-wrap items-start justify-between gap-6">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
            Patient profile
          </span>
          <h1 className="mt-2 font-['Outfit'] text-4xl font-medium tracking-tight text-[#1F2924]">
            {patient.first_name} {patient.last_name}
          </h1>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm text-[#5C6A61]">
            {patient.date_of_birth && (
              <span>DOB {formatDate(patient.date_of_birth)}</span>
            )}
            {patient.phone && <span>{patient.phone}</span>}
            {patient.email && <span>{patient.email}</span>}
            {patient.gender && <span>{patient.gender}</span>}
          </div>
        </div>
      </header>

      <section className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <div className="rounded-sm border border-stone-200 bg-white p-6">
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
            Address
          </span>
          <p className="mt-3 text-sm leading-relaxed text-[#1F2924]">
            {patient.address || "—"}
          </p>
        </div>
        <div className="rounded-sm border border-stone-200 bg-white p-6">
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
            Emergency contact
          </span>
          <p className="mt-3 text-sm leading-relaxed text-[#1F2924]">
            {patient.emergency_contact || "—"}
          </p>
        </div>
        <div className="rounded-sm border border-stone-200 bg-white p-6">
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
            Intake notes
          </span>
          <p className="mt-3 text-sm leading-relaxed text-[#1F2924]">
            {patient.notes || "—"}
          </p>
        </div>
      </section>

      <section>
        <div className="mb-4 flex items-end justify-between">
          <div>
            <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
              Clinical history
            </span>
            <h2 className="mt-1 font-['Outfit'] text-2xl font-medium tracking-tight">
              Medical records
            </h2>
          </div>
          {canAddRecord && (
            <Button
              onClick={() => setRecDialog(true)}
              data-testid="record-new-btn"
              className="rounded-sm bg-[#7B9A82] hover:bg-[#65826C]"
            >
              <Plus className="mr-2 h-4 w-4" /> Add record
            </Button>
          )}
        </div>

        {records === null ? (
          <Skeleton className="h-32" />
        ) : records.length === 0 ? (
          <div className="rounded-sm border border-dashed border-stone-200 bg-white p-12 text-center text-sm text-[#5C6A61]">
            No medical records yet.
          </div>
        ) : (
          <ol className="relative space-y-4 border-l border-stone-200 pl-6">
            {records.map((r) => (
              <li
                key={r.id}
                data-testid={`record-${r.id}`}
                className="relative rounded-sm border border-stone-200 bg-white p-5"
              >
                <span className="absolute -left-[33px] top-5 flex h-5 w-5 items-center justify-center rounded-sm bg-[#EDF2EE] text-[#526B58]">
                  <FileText className="h-3 w-3" />
                </span>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <span className="rounded-sm bg-[#F5F5F0] px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-[#5C6A61]">
                      {r.record_type}
                    </span>
                    <h3 className="mt-2 font-['Outfit'] text-lg font-medium text-[#1F2924]">
                      {r.title}
                    </h3>
                  </div>
                  <div className="text-xs text-[#5C6A61]">
                    {formatDateTime(r.recorded_at)} · {r.recorded_by_name || "—"}
                  </div>
                </div>
                {r.description && (
                  <p className="mt-3 text-sm leading-relaxed text-[#1F2924]">
                    {r.description}
                  </p>
                )}
                <div className="mt-3 grid grid-cols-2 gap-4 text-sm">
                  {r.diagnosis && (
                    <div>
                      <span className="text-[11px] uppercase tracking-wider text-[#5C6A61]">
                        Diagnosis
                      </span>
                      <div className="text-[#1F2924]">{r.diagnosis}</div>
                    </div>
                  )}
                  {r.treatment && (
                    <div>
                      <span className="text-[11px] uppercase tracking-wider text-[#5C6A61]">
                        Treatment
                      </span>
                      <div className="text-[#1F2924]">{r.treatment}</div>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section>
        <div className="mb-4">
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
            Scheduling
          </span>
          <h2 className="mt-1 font-['Outfit'] text-2xl font-medium tracking-tight">
            Appointments
          </h2>
        </div>
        {appointments === null ? (
          <Skeleton className="h-24" />
        ) : appointments.length === 0 ? (
          <div className="rounded-sm border border-dashed border-stone-200 bg-white p-10 text-center text-sm text-[#5C6A61]">
            No appointments for this patient.
          </div>
        ) : (
          <ul className="space-y-2">
            {appointments.map((a) => (
              <li
                key={a.id}
                className="flex items-center justify-between rounded-sm border border-stone-200 bg-white px-5 py-4 text-sm"
              >
                <div>
                  <div className="font-medium text-[#1F2924]">
                    {formatDateTime(a.start_time)}
                  </div>
                  <div className="text-xs text-[#5C6A61]">
                    with {a.provider_name} · {relativeFromNow(a.start_time)}
                  </div>
                </div>
                <span
                  className={`rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
                    a.status === "cancelled"
                      ? "bg-[#FBF1EE] text-[#C76D54]"
                      : a.status === "completed"
                      ? "bg-[#F5F5F0] text-[#5C6A61]"
                      : "bg-[#EDF2EE] text-[#526B58]"
                  }`}
                >
                  {a.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {canAddRecord && (
        <RecordDialog
          open={recDialog}
          onClose={() => setRecDialog(false)}
          patientId={id}
          onAdded={(rec) => setRecords((xs) => [rec, ...(xs || [])])}
        />
      )}
    </div>
  );
}
