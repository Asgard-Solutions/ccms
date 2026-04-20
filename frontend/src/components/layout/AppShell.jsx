import { useEffect, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import {
  ChevronDown,
  LogOut,
  Menu,
  Stethoscope,
  ShieldCheck,
} from "lucide-react";
import { useAuth } from "../../contexts/AuthContext";
import { visibleGroupsForRole } from "./navConfig";
import { Button } from "../ui/button";
import ThemeToggle from "../ThemeToggle";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../ui/alert-dialog";

const NAV_COLLAPSE_STORAGE_KEY = "ccms.sidebar.collapsed";

function useCollapsedSections() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const raw = window.localStorage.getItem(NAV_COLLAPSE_STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch {
      return {};
    }
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(
        NAV_COLLAPSE_STORAGE_KEY,
        JSON.stringify(collapsed),
      );
    } catch {
      /* ignore quota errors */
    }
  }, [collapsed]);
  function toggle(id) {
    setCollapsed((prev) => ({ ...prev, [id]: !prev[id] }));
  }
  return [collapsed, toggle];
}

function roleLabel(role) {
  return {
    admin: "Administrator",
    doctor: "Provider",
    staff: "Clinic Staff",
    patient: "Patient",
  }[role] || role;
}

function Sidebar({ role, open, onClose }) {
  const groups = visibleGroupsForRole(role);
  const [collapsed, toggleCollapsed] = useCollapsedSections();
  return (
    <>
      {open && (
        <div
          data-testid="sidebar-backdrop"
          className="fixed inset-0 z-40 bg-foreground/20 md:hidden"
          onClick={onClose}
        />
      )}
      <aside
        data-testid="app-sidebar"
        className={`fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-border bg-[var(--sidebar-bg)] text-[var(--sidebar-fg)] transition-transform duration-200 md:sticky md:top-0 md:h-screen md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-16 items-center gap-2 border-b border-border px-6">
          <div className="flex h-8 w-8 items-center justify-center rounded-sm bg-primary text-primary-foreground">
            <Stethoscope className="h-4 w-4" />
          </div>
          <div className="flex flex-col leading-tight">
            <span className="font-display text-sm font-semibold text-foreground">CCMS</span>
            <span className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">Clinic OS</span>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto py-2">
          {groups.map((group, gi) => {
            const isCollapsed = group.collapsible && !!collapsed[group.id];
            return (
              <div
                key={group.id}
                data-testid={`nav-group-${group.id}`}
                className={gi === 0 ? "pt-2" : "mt-2 border-t border-border pt-2"}
              >
                {group.collapsible ? (
                  <button
                    type="button"
                    data-testid={`nav-group-toggle-${group.id}`}
                    onClick={() => toggleCollapsed(group.id)}
                    aria-expanded={!isCollapsed}
                    className="flex w-full items-center justify-between px-6 py-2 text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground transition-colors hover:text-foreground"
                  >
                    <span>{group.label}</span>
                    <ChevronDown
                      className={`h-3.5 w-3.5 transition-transform ${
                        isCollapsed ? "-rotate-90" : "rotate-0"
                      }`}
                    />
                  </button>
                ) : (
                  <div className="px-6 py-2 text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
                    {group.label}
                  </div>
                )}

                {!isCollapsed && (
                  <div className="pb-1">
                    {group.items.map(({ to, label, icon: Icon, testId }) => (
                      <NavLink
                        key={to}
                        to={to}
                        end={to === "/"}
                        data-testid={testId}
                        onClick={onClose}
                        className={({ isActive }) =>
                          `flex items-center gap-3 border-l-2 px-6 py-2.5 text-sm font-medium transition-colors ${
                            isActive
                              ? "border-[var(--sidebar-active-indicator)] bg-[var(--sidebar-active-bg)] pl-[22px] text-[var(--sidebar-active-fg)]"
                              : "border-transparent text-muted-foreground hover:bg-[var(--sidebar-active-bg)] hover:text-foreground"
                          }`
                        }
                      >
                        <Icon className="h-4 w-4" />
                        {label}
                      </NavLink>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        <div className="border-t border-border px-6 py-4 text-xs text-muted-foreground">
          <div className="font-medium uppercase tracking-[0.15em]">HIPAA</div>
          <div className="mt-1 flex items-center gap-2">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
            audit · encryption · MFA ready
          </div>
        </div>
      </aside>
    </>
  );
}

export default function AppShell({ children }) {
  const { user, logout, idleWarning } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  if (!user) return null;

  const initials = user.name.split(" ").map((n) => n[0]).slice(0, 2).join("").toUpperCase();

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <Sidebar role={user.role} open={open} onClose={() => setOpen(false)} />

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          data-testid="app-topnav"
          className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-border bg-card/90 px-6 backdrop-blur"
        >
          <div className="flex items-center gap-3">
            <Button
              data-testid="sidebar-toggle"
              variant="ghost"
              size="icon"
              onClick={() => setOpen((s) => !s)}
              className="md:hidden"
            >
              <Menu className="h-5 w-5" />
            </Button>
            <div className="hidden md:flex md:flex-col md:leading-tight">
              <span className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">Welcome back</span>
              <span className="font-display text-base font-semibold">{user.name}</span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {user.mfa_enabled ? (
              <span
                data-testid="mfa-indicator"
                className="hidden rounded-sm bg-primary/10 px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-primary md:inline-flex"
              >
                <ShieldCheck className="mr-1 h-3 w-3" /> MFA on
              </span>
            ) : (
              user.role !== "patient" && (
                <Link
                  to="/security"
                  data-testid="mfa-setup-banner"
                  className="hidden rounded-sm bg-warning-soft px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-warning md:inline-flex"
                >
                  Enable MFA
                </Link>
              )
            )}

            <ThemeToggle />

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  data-testid="user-menu-trigger"
                  className="flex items-center gap-3 rounded-sm px-2 py-1 text-sm font-medium hover:bg-muted"
                >
                  <span className="flex h-9 w-9 items-center justify-center rounded-sm bg-primary/10 text-xs font-semibold text-primary">
                    {initials}
                  </span>
                  <span className="hidden flex-col text-left leading-tight sm:flex">
                    <span className="text-foreground">{user.name}</span>
                    <span className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
                      {roleLabel(user.role)}
                    </span>
                  </span>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>
                  <div className="flex flex-col">
                    <span className="text-foreground">{user.email}</span>
                    <span className="text-xs text-muted-foreground">{roleLabel(user.role)}</span>
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link to="/security" data-testid="menu-security">
                    <ShieldCheck className="mr-2 h-4 w-4" /> Security
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  data-testid="menu-logout"
                  onClick={async () => {
                    await logout();
                    navigate("/login");
                  }}
                  className="text-destructive focus:text-destructive"
                >
                  <LogOut className="mr-2 h-4 w-4" /> Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        <main className="flex-1 p-6 md:p-10">{children}</main>

        <AlertDialog open={!!idleWarning}>
          <AlertDialogContent data-testid="idle-warning" className="rounded-sm">
            <AlertDialogHeader>
              <AlertDialogTitle className="font-display">Are you still there?</AlertDialogTitle>
              <AlertDialogDescription>
                You will be signed out automatically in under a minute to protect
                patient data. Click Stay to continue.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogAction
                data-testid="idle-stay-btn"
                className="rounded-sm bg-primary hover:bg-[var(--primary-hover)]"
              >
                Stay signed in
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </div>
  );
}
