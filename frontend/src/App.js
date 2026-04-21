import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "./components/ui/sonner";
import { AuthProvider } from "./contexts/AuthContext";
import { PermissionsProvider } from "./contexts/PermissionsContext";
import { ProvidersProvider } from "./contexts/ProvidersContext";
import { ThemeProvider } from "./contexts/ThemeContext";
import { ReauthProvider } from "./components/ReauthGate";
import ProtectedRoute from "./components/layout/ProtectedRoute";
import AppShell from "./components/layout/AppShell";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import Patients from "./pages/Patients";
import PatientDetail from "./pages/PatientDetail";
import InitialExamEditor from "./pages/clinical/InitialExamEditor";
import FollowUpNoteEditor from "./pages/clinical/FollowUpNoteEditor";
import TreatmentPlanEditor from "./pages/clinical/TreatmentPlanEditor";
import ReExamEditor from "./pages/clinical/ReExamEditor";
import Scheduling from "./pages/Scheduling";
import FlowBoardPage from "./pages/scheduling/FlowBoardPage";
import ProviderQueuePage from "./pages/scheduling/ProviderQueuePage";
import Notifications from "./pages/Notifications";
import Security from "./pages/Security";
import AuditLog from "./pages/AuditLog";
import Compliance from "./pages/Compliance";
import Privacy from "./pages/Privacy";
import SecurityConfig from "./pages/SecurityConfig";
import PasswordReset from "./pages/PasswordReset";
import PermissionMatrix from "./pages/PermissionMatrix";
import RoleManagement from "./pages/RoleManagement";
import AccessReview from "./pages/AccessReview";
import Elevation from "./pages/Elevation";
import ThemePreview from "./pages/ThemePreview";
import ClinicSettings from "./pages/ClinicSettings";
import AppointmentTypesPage from "./pages/AppointmentTypesPage";
import RoomsManagerPage from "./pages/settings/RoomsManagerPage";
import PayersPage from "./pages/PayersPage";
import FeeSchedulesPage from "./pages/FeeSchedulesPage";
import BillingDashboard from "./pages/billing/BillingDashboard";
import InvoicesList from "./pages/billing/InvoicesList";
import InvoiceDetail from "./pages/billing/InvoiceDetail";
import PatientLedgerPage from "./pages/billing/PatientLedgerPage";
import ClaimsQueue from "./pages/billing/ClaimsQueue";
import ClaimDetail from "./pages/billing/ClaimDetail";
import RemittancePosting from "./pages/billing/RemittancePosting";
import RemittanceImport from "./pages/billing/RemittanceImport";
import RemittanceDetail from "./pages/billing/RemittanceDetail";
import DenialsQueue from "./pages/billing/DenialsQueue";
import ArAgingReport from "./pages/billing/ArAgingReport";
import ReportsLandingPage from "./pages/reports/ReportsLandingPage";
import ReportViewer from "./pages/reports/ReportViewer";
import "./App.css";

function Shell({ children, roles }) {
  return (
    <ProtectedRoute roles={roles}>
      <AppShell>{children}</AppShell>
    </ProtectedRoute>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <PermissionsProvider>
          <ProvidersProvider>
            <ReauthProvider>
            <BrowserRouter>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/register" element={<Register />} />
              <Route path="/password-reset" element={<PasswordReset />} />
              <Route path="/" element={<Shell><Dashboard /></Shell>} />
              <Route path="/patients" element={<Shell><Patients /></Shell>} />
              <Route path="/patients/:id" element={<Shell><PatientDetail /></Shell>} />
              <Route path="/patients/:pid/clinical/exams/:eid" element={<Shell roles={["admin", "doctor", "staff"]}><InitialExamEditor /></Shell>} />
              <Route path="/patients/:pid/clinical/follow-up/:nid" element={<Shell roles={["admin", "doctor", "staff"]}><FollowUpNoteEditor /></Shell>} />
              <Route path="/patients/:pid/clinical/treatment-plans/:tpid" element={<Shell roles={["admin", "doctor", "staff"]}><TreatmentPlanEditor /></Shell>} />
              <Route path="/patients/:pid/clinical/re-exams/:rid" element={<Shell roles={["admin", "doctor", "staff"]}><ReExamEditor /></Shell>} />
              <Route path="/scheduling" element={<Shell><Scheduling /></Shell>} />
              <Route path="/scheduling/flow-board" element={<Shell roles={["admin", "doctor", "staff"]}><FlowBoardPage /></Shell>} />
              <Route path="/scheduling/provider-queue" element={<Shell roles={["admin", "doctor", "staff"]}><ProviderQueuePage /></Shell>} />
              <Route path="/settings/clinic" element={<Shell roles={["admin"]}><ClinicSettings /></Shell>} />
              <Route path="/settings/appointment-types" element={<Shell roles={["admin"]}><AppointmentTypesPage /></Shell>} />
              <Route path="/settings/rooms" element={<Shell roles={["admin"]}><RoomsManagerPage /></Shell>} />
              <Route path="/settings/payers" element={<Shell roles={["admin"]}><PayersPage /></Shell>} />
              <Route path="/settings/fee-schedules" element={<Shell roles={["admin"]}><FeeSchedulesPage /></Shell>} />
              <Route path="/billing" element={<Shell roles={["admin", "doctor", "staff"]}><BillingDashboard /></Shell>} />
              <Route path="/billing/invoices" element={<Shell roles={["admin", "doctor", "staff"]}><InvoicesList /></Shell>} />
              <Route path="/billing/invoices/:id" element={<Shell roles={["admin", "doctor", "staff"]}><InvoiceDetail /></Shell>} />
              <Route path="/billing/patients/:id/ledger" element={<Shell roles={["admin", "doctor", "staff"]}><PatientLedgerPage /></Shell>} />
              <Route path="/billing/claims" element={<Shell roles={["admin", "doctor", "staff"]}><ClaimsQueue /></Shell>} />
              <Route path="/billing/claims/:id" element={<Shell roles={["admin", "doctor", "staff"]}><ClaimDetail /></Shell>} />
              <Route path="/billing/remittances/new" element={<Shell roles={["admin", "staff"]}><RemittancePosting /></Shell>} />
              <Route path="/billing/remittances/import" element={<Shell roles={["admin", "staff"]}><RemittanceImport /></Shell>} />
              <Route path="/billing/remittances/:id" element={<Shell roles={["admin", "doctor", "staff"]}><RemittanceDetail /></Shell>} />
              <Route path="/billing/denials" element={<Shell roles={["admin", "doctor", "staff"]}><DenialsQueue /></Shell>} />
              <Route path="/billing/ar-aging" element={<Shell roles={["admin", "doctor", "staff"]}><ArAgingReport /></Shell>} />
              <Route path="/reports" element={<Shell roles={["admin", "doctor", "staff"]}><ReportsLandingPage /></Shell>} />
              <Route path="/reports/:name" element={<Shell roles={["admin", "doctor", "staff"]}><ReportViewer /></Shell>} />
              <Route path="/appointments" element={<Navigate to="/scheduling" replace />} />
              <Route path="/calendar" element={<Navigate to="/scheduling" replace />} />
              <Route path="/notifications" element={<Shell roles={["admin", "staff"]}><Notifications /></Shell>} />
              <Route path="/audit-log" element={<Shell roles={["admin"]}><AuditLog /></Shell>} />
              <Route path="/compliance" element={<Shell roles={["admin"]}><Compliance /></Shell>} />
              <Route path="/privacy" element={<Shell roles={["admin"]}><Privacy /></Shell>} />
              <Route path="/security-config" element={<Shell roles={["admin"]}><SecurityConfig /></Shell>} />
              <Route path="/roles" element={<Shell roles={["admin"]}><RoleManagement /></Shell>} />
              <Route path="/permissions" element={<Shell roles={["admin"]}><PermissionMatrix /></Shell>} />
              <Route path="/access-review" element={<Shell roles={["admin"]}><AccessReview /></Shell>} />
              <Route path="/elevation" element={<Shell><Elevation /></Shell>} />
              <Route path="/security" element={<Shell><Security /></Shell>} />
              <Route path="/account" element={<Shell><Security /></Shell>} />
              <Route path="/settings/theme-preview" element={<Shell><ThemePreview /></Shell>} />
            </Routes>
          </BrowserRouter>
          <Toaster richColors position="top-right" />
          </ReauthProvider>
          </ProvidersProvider>
        </PermissionsProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}
