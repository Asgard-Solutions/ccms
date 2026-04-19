import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Plus, Search, User2 } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import { formatDate } from "../utils/time";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Skeleton } from "../components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { Label } from "../components/ui/label";

const STAFF_ROLES = ["admin", "doctor", "staff"];

function PatientFormDialog({ open, onClose, onCreated }) {
  const [form, setForm] = useState({
    first_name: "",
    last_name: "",
    email: "",
    phone: "",
    date_of_birth: "",
    address: "",
    emergency_contact: "",
    notes: "",
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
      const { data } = await api.post("/patients", payload);
      toast.success(`Patient ${data.first_name} ${data.last_name} created`);
      onCreated(data);
      onClose();
      setForm({
        first_name: "",
        last_name: "",
        email: "",
        phone: "",
        date_of_birth: "",
        address: "",
        emergency_contact: "",
        notes: "",
      });
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to create patient");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="patient-create-dialog" className="max-w-lg rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-['Outfit']">New patient</DialogTitle>
          <DialogDescription>
            Add intake details. You can refine them later from the patient profile.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <Label htmlFor="fn">First name</Label>
            <Input
              id="fn"
              data-testid="patient-first-name"
              required
              value={form.first_name}
              onChange={update("first_name")}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ln">Last name</Label>
            <Input
              id="ln"
              data-testid="patient-last-name"
              required
              value={form.last_name}
              onChange={update("last_name")}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="em">Email</Label>
            <Input
              id="em"
              data-testid="patient-email"
              type="email"
              value={form.email}
              onChange={update("email")}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ph">Phone</Label>
            <Input
              id="ph"
              data-testid="patient-phone"
              value={form.phone}
              onChange={update("phone")}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="dob">Date of birth</Label>
            <Input
              id="dob"
              data-testid="patient-dob"
              type="date"
              value={form.date_of_birth}
              onChange={update("date_of_birth")}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="em-c">Emergency contact</Label>
            <Input
              id="em-c"
              data-testid="patient-emergency"
              value={form.emergency_contact}
              onChange={update("emergency_contact")}
            />
          </div>
          <div className="col-span-2 space-y-1">
            <Label htmlFor="addr">Address</Label>
            <Input
              id="addr"
              data-testid="patient-address"
              value={form.address}
              onChange={update("address")}
            />
          </div>
          <div className="col-span-2 space-y-1">
            <Label htmlFor="notes">Intake notes</Label>
            <Input
              id="notes"
              data-testid="patient-notes"
              value={form.notes}
              onChange={update("notes")}
            />
          </div>

          <DialogFooter className="col-span-2 mt-2">
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              className="rounded-sm"
              data-testid="patient-cancel-btn"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={submitting}
              data-testid="patient-submit-btn"
              className="rounded-sm bg-[#7B9A82] hover:bg-[#65826C]"
            >
              {submitting ? "Saving…" : "Save patient"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function Patients() {
  const { user } = useAuth();
  const canCreate = STAFF_ROLES.includes(user.role);
  const [patients, setPatients] = useState(null);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get("/patients");
        if (!cancelled) setPatients(data);
      } catch {
        if (!cancelled) setPatients([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    if (!patients) return null;
    const q = search.trim().toLowerCase();
    if (!q) return patients;
    return patients.filter((p) =>
      [p.first_name, p.last_name, p.email, p.phone]
        .filter(Boolean)
        .some((v) => v.toLowerCase().includes(q))
    );
  }, [patients, search]);

  return (
    <div data-testid="patients-page" className="space-y-8 animate-in fade-in duration-300">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-[#5C6A61]">
            Patient directory
          </span>
          <h1 className="mt-2 font-['Outfit'] text-4xl font-medium tracking-tight text-[#1F2924]">
            Patients
          </h1>
          <p className="mt-2 text-sm text-[#5C6A61]">
            Every patient, their history, and their upcoming visits in one place.
          </p>
        </div>
        {canCreate && (
          <Button
            data-testid="patients-new-btn"
            onClick={() => setOpen(true)}
            className="h-11 rounded-sm bg-[#7B9A82] px-5 hover:bg-[#65826C]"
          >
            <Plus className="mr-2 h-4 w-4" /> New patient
          </Button>
        )}
      </header>

      <div className="relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#A3AFA7]" />
        <Input
          data-testid="patients-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name, email, or phone…"
          className="h-11 rounded-sm border-stone-200 pl-9"
        />
      </div>

      {filtered === null ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16 rounded-sm" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-sm border border-dashed border-stone-200 bg-white p-16 text-center">
          <User2 className="mx-auto h-10 w-10 text-[#A3AFA7]" />
          <p className="mt-4 font-['Outfit'] text-lg text-[#1F2924]">
            No patients {search ? "match your search" : "yet"}
          </p>
          <p className="mt-1 text-sm text-[#5C6A61]">
            {canCreate && !search && "Start by creating your first patient record."}
          </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-sm border border-stone-200 bg-white">
          <table className="w-full text-left">
            <thead className="border-b border-stone-200 bg-[#FAF9F6]">
              <tr className="text-xs font-semibold uppercase tracking-wider text-[#5C6A61]">
                <th className="px-6 py-3">Name</th>
                <th className="px-6 py-3">Contact</th>
                <th className="px-6 py-3">DOB</th>
                <th className="px-6 py-3">Added</th>
                <th className="px-6 py-3" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((p) => (
                <tr
                  key={p.id}
                  data-testid={`patient-row-${p.id}`}
                  className="border-b border-stone-100 last:border-b-0 hover:bg-[#F5F5F0]/50"
                >
                  <td className="px-6 py-4">
                    <div className="font-medium text-[#1F2924]">
                      {p.first_name} {p.last_name}
                    </div>
                    <div className="text-xs text-[#5C6A61]">
                      {p.gender || "—"}
                    </div>
                  </td>
                  <td className="px-6 py-4 text-sm text-[#5C6A61]">
                    <div>{p.email || "—"}</div>
                    <div className="text-xs">{p.phone || "—"}</div>
                  </td>
                  <td className="px-6 py-4 text-sm text-[#5C6A61]">
                    {p.date_of_birth ? formatDate(p.date_of_birth) : "—"}
                  </td>
                  <td className="px-6 py-4 text-sm text-[#5C6A61]">
                    {formatDate(p.created_at)}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <Button
                      variant="ghost"
                      asChild
                      className="text-[#526B58] hover:bg-[#EDF2EE]"
                    >
                      <Link
                        to={`/patients/${p.id}`}
                        data-testid={`patient-open-${p.id}`}
                      >
                        Open
                      </Link>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {canCreate && (
        <PatientFormDialog
          open={open}
          onClose={() => setOpen(false)}
          onCreated={(p) => setPatients((xs) => [p, ...(xs || [])])}
        />
      )}
    </div>
  );
}
