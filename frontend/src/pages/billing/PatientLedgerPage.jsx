import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import PatientLedgerCard from "./PatientLedgerCard";

export default function PatientLedgerPage() {
  const { id } = useParams();
  return (
    <div data-testid="patient-ledger-page" className="space-y-6">
      <header>
        <Link
          to={`/patients/${id}`}
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to patient
        </Link>
        <h1 className="mt-1 font-display text-4xl font-medium tracking-tight">
          Patient ledger
        </h1>
      </header>
      <PatientLedgerCard patientId={id} title="Activity" />
    </div>
  );
}
