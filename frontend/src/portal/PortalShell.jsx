/**
 * PortalShell — minimal layout for patient-facing routes.
 *
 * Much lighter than the clinic-operations AppShell: a single top bar
 * with the clinic wordmark, a handful of portal links, and a sign-out
 * button. No clinical sidebar, no admin switches.
 *
 * Patients (role="patient") are routed here automatically on login.
 */
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { FileText, LogOut, User2 } from "lucide-react";
import { Button } from "../components/ui/button";
import { useAuth } from "../contexts/AuthContext";

function NavItem({ to, icon: Icon, label, testid }) {
  return (
    <NavLink
      to={to}
      data-testid={testid}
      end
      className={({ isActive }) =>
        `flex items-center gap-2 rounded-sm px-3 py-2 text-sm font-medium transition
         ${isActive
           ? "bg-primary/10 text-primary"
           : "text-muted-foreground hover:bg-muted hover:text-foreground"}`
      }
    >
      <Icon className="h-4 w-4" />
      {label}
    </NavLink>
  );
}

export default function PortalShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleSignOut = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div data-testid="portal-shell" className="min-h-screen bg-background">
      <header className="sticky top-0 z-10 border-b border-border bg-card/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <Link
            to="/portal"
            data-testid="portal-home-link"
            className="flex items-center gap-2 font-display text-lg font-medium tracking-tight"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-sm bg-primary text-primary-foreground">
              CC
            </span>
            Patient Portal
          </Link>
          <div className="flex items-center gap-3 text-sm">
            <span className="hidden text-muted-foreground sm:block">
              {user?.name || user?.email}
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={handleSignOut}
              data-testid="portal-signout-btn"
              className="h-8 rounded-sm"
            >
              <LogOut className="mr-1 h-3.5 w-3.5" />
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-6xl gap-6 px-6 py-8 sm:flex-row flex-col">
        <nav
          data-testid="portal-nav"
          className="flex w-full flex-row gap-1 sm:w-48 sm:flex-col sm:flex-shrink-0"
        >
          <NavItem to="/portal" icon={User2} label="Overview" testid="portal-nav-overview" />
          <NavItem to="/portal/statements" icon={FileText} label="Statements" testid="portal-nav-statements" />
        </nav>

        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
