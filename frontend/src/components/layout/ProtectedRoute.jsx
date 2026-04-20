import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../../contexts/AuthContext";

export default function ProtectedRoute({ children, roles }) {
  const { user } = useAuth();
  const location = useLocation();

  if (user === undefined) {
    return (
      <div
        data-testid="auth-loading"
        className="flex min-h-screen items-center justify-center bg-[#FAF9F6]"
      >
        <div className="flex items-center gap-3 text-sm text-[#5C6A61]">
          <span className="h-2 w-2 animate-pulse rounded-full bg-[#7B9A82]" />
          Loading your clinic…
        </div>
      </div>
    );
  }

  if (user === null) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (roles && roles.length && !roles.includes(user.role)) {
    return <Navigate to="/" replace />;
  }

  return children;
}
