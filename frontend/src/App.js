import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster } from "./components/ui/sonner";
import { AuthProvider } from "./contexts/AuthContext";
import ProtectedRoute from "./components/layout/ProtectedRoute";
import AppShell from "./components/layout/AppShell";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import Patients from "./pages/Patients";
import PatientDetail from "./pages/PatientDetail";
import Appointments from "./pages/Appointments";
import CalendarPage from "./pages/Calendar";
import Notifications from "./pages/Notifications";
import Security from "./pages/Security";
import AuditLog from "./pages/AuditLog";
import Compliance from "./pages/Compliance";
import Privacy from "./pages/Privacy";
import PasswordReset from "./pages/PasswordReset";
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
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/password-reset" element={<PasswordReset />} />
          <Route path="/" element={<Shell><Dashboard /></Shell>} />
          <Route path="/patients" element={<Shell><Patients /></Shell>} />
          <Route path="/patients/:id" element={<Shell><PatientDetail /></Shell>} />
          <Route path="/appointments" element={<Shell><Appointments /></Shell>} />
          <Route path="/calendar" element={<Shell roles={["admin", "doctor", "staff"]}><CalendarPage /></Shell>} />
          <Route path="/notifications" element={<Shell roles={["admin", "staff"]}><Notifications /></Shell>} />
          <Route path="/audit-log" element={<Shell roles={["admin"]}><AuditLog /></Shell>} />
          <Route path="/compliance" element={<Shell roles={["admin"]}><Compliance /></Shell>} />
          <Route path="/privacy" element={<Shell roles={["admin"]}><Privacy /></Shell>} />
          <Route path="/security" element={<Shell><Security /></Shell>} />
        </Routes>
      </BrowserRouter>
      <Toaster richColors position="top-right" />
    </AuthProvider>
  );
}
