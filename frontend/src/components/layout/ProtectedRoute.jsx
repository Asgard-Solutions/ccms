import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../../contexts/AuthContext";

export default function ProtectedRoute({ children, roles, portal = false }) {
  const { user } = useAuth();
  const location = useLocation();

  if (user === undefined) {
    return (
      <div
        data-testid="auth-loading"
        className="flex min-h-screen items-center justify-center bg-background"
      >
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
          Loading your clinic…
        </div>
      </div>
    );
  }

  if (user === null) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  // Role gating:
  //   * `portal: true` routes are patient-only
  //   * non-portal routes reject `patient` role and push them to /portal
  if (portal) {
    if (user.role !== "patient") return <Navigate to="/" replace />;
  } else if (user.role === "patient") {
    return <Navigate to="/portal" replace />;
  }

  if (roles && roles.length && !roles.includes(user.role)) {
    return <Navigate to="/" replace />;
  }

  return children;
}
