import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import DashboardPanel from "./compliance/DashboardPanel";
import PoliciesRegister from "./compliance/registers/PoliciesRegister";
import RisksRegister from "./compliance/registers/RisksRegister";
import EvidenceRegister from "./compliance/registers/EvidenceRegister";
import IncidentsRegister from "./compliance/registers/IncidentsRegister";
import VendorsRegister from "./compliance/registers/VendorsRegister";
import AccessReviewsRegister from "./compliance/registers/AccessReviewsRegister";
import ControlsRegister from "./compliance/registers/ControlsRegister";
import AuditTrail from "./compliance/registers/AuditTrail";

const TABS = [
  { v: "dashboard", l: "Dashboard" },
  { v: "controls", l: "Controls" },
  { v: "policies", l: "Policies" },
  { v: "risks", l: "Risks" },
  { v: "evidence", l: "Evidence" },
  { v: "incidents", l: "Incidents" },
  { v: "access-reviews", l: "Access reviews" },
  { v: "vendors", l: "Vendors" },
  { v: "audit", l: "Audit trail" },
];

export default function Compliance() {
  const [tab, setTab] = useState("dashboard");

  return (
    <div data-testid="compliance-page" className="space-y-8 animate-in fade-in duration-300">
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Compliance
        </span>
        <h1 className="mt-2 font-display text-4xl font-medium tracking-tight">
          ISO 27001 &amp; SOC 2 control plane
        </h1>
        <p className="mt-3 max-w-3xl text-sm text-muted-foreground">
          A unified registry for the controls, policies, risks, evidence, incidents, vendors, and access
          reviews that underpin our HIPAA, SOC 2, and ISO 27001 posture. Every mutation is appended to
          per-record history and the full action stream lands in the immutable audit log.
        </p>
        <div
          data-testid="compliance-disclaimer"
          className="mt-4 flex items-start gap-2 rounded-sm border border-border bg-warning-soft p-3 text-xs text-warning"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
          <span>
            Application-layer controls only. Independent audit, HR, infrastructure, and legal evidence
            held outside this codebase remain in scope for a full certification.
          </span>
        </div>
      </header>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList data-testid="compliance-tabs" className="flex flex-wrap gap-1 rounded-sm">
          {TABS.map((t) => (
            <TabsTrigger
              key={t.v}
              value={t.v}
              data-testid={`compliance-tab-${t.v}`}
              className="text-xs uppercase tracking-wider"
            >
              {t.l}
            </TabsTrigger>
          ))}
        </TabsList>
        <div className="mt-6">
          <TabsContent value="dashboard"><DashboardPanel /></TabsContent>
          <TabsContent value="controls"><ControlsRegister /></TabsContent>
          <TabsContent value="policies"><PoliciesRegister /></TabsContent>
          <TabsContent value="risks"><RisksRegister /></TabsContent>
          <TabsContent value="evidence"><EvidenceRegister /></TabsContent>
          <TabsContent value="incidents"><IncidentsRegister /></TabsContent>
          <TabsContent value="access-reviews"><AccessReviewsRegister /></TabsContent>
          <TabsContent value="vendors"><VendorsRegister /></TabsContent>
          <TabsContent value="audit"><AuditTrail /></TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
