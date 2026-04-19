import { useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import {
  Activity,
  CalendarDays,
  LayoutDashboard,
  LogOut,
  Menu,
  Users,
  BellRing,
  Stethoscope,
  Shield,
  ShieldCheck,
  ClipboardCheck,
  Scale,
  KeyRound,
} from "lucide-react";
import { useAuth } from "../../contexts/AuthContext";
import { Button } from "../ui/button";
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

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, roles: ["admin", "doctor", "staff", "patient"] },
  { to: "/patients", label: "Patients", icon: Users, roles: ["admin", "doctor", "staff", "patient"] },
  { to: "/appointments", label: "Appointments", icon: CalendarDays, roles: ["admin", "doctor", "staff", "patient"] },
  { to: "/calendar", label: "Calendar", icon: Activity, roles: ["admin", "doctor", "staff"] },
  { to: "/notifications", label: "Notifications", icon: BellRing, roles: ["admin", "staff"] },
  { to: "/audit-log", label: "Audit log", icon: Shield, roles: ["admin"] },
  { to: "/compliance", label: "Compliance", icon: ClipboardCheck, roles: ["admin"] },
  { to: "/privacy", label: "Privacy", icon: Scale, roles: ["admin"] },
  { to: "/security-config", label: "Security config", icon: KeyRound, roles: ["admin"] },
  { to: "/security", label: "Security", icon: ShieldCheck, roles: ["admin", "doctor", "staff", "patient"] },
];

function roleLabel(role) {
  return {
    admin: "Administrator",
    doctor: "Provider",
    staff: "Clinic Staff",
    patient: "Patient",
  }[role] || role;
}

function Sidebar({ role, open, onClose }) {
  const visible = NAV_ITEMS.filter((i) => i.roles.includes(role));
  return (
    <>
      {open && (
        <div
          data-testid="sidebar-backdrop"
          className="fixed inset-0 z-40 bg-black/20 md:hidden"
          onClick={onClose}
        />
      )}
      <aside
        data-testid="app-sidebar"
        className={`fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-stone-200 bg-[#FAF9F6] transition-transform duration-200 md:sticky md:top-0 md:h-screen md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-16 items-center gap-2 border-b border-stone-200 px-6">
          <div className="flex h-8 w-8 items-center justify-center rounded-sm bg-[#7B9A82] text-white">
            <Stethoscope className="h-4 w-4" />
          </div>
          <div className="flex flex-col leading-tight">
            <span className="font-['Outfit'] text-sm font-medium text-[#1F2924]">CCMS</span>
            <span className="text-[11px] uppercase tracking-[0.15em] text-[#5C6A61]">Clinic OS</span>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto py-4">
          {visible.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              data-testid={`nav-${label.toLowerCase().replace(/\s+/g, "-")}`}
              onClick={onClose}
              className={({ isActive }) =>
                `flex items-center gap-3 px-6 py-3 text-sm font-medium transition-colors ${
                  isActive
                    ? "border-l-2 border-[#7B9A82] bg-[#F5F5F0] pl-[22px] text-[#1F2924]"
                    : "border-l-2 border-transparent text-[#5C6A61] hover:bg-[#F5F5F0] hover:text-[#1F2924]"
                }`
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-stone-200 px-6 py-4 text-xs text-[#5C6A61]">
          <div className="font-medium uppercase tracking-[0.15em]">HIPAA</div>
          <div className="mt-1 flex items-center gap-2">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[#7B9A82]" />
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
    <div className="flex min-h-screen bg-[#FAF9F6] text-[#1F2924]">
      <Sidebar role={user.role} open={open} onClose={() => setOpen(false)} />

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          data-testid="app-topnav"
          className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-stone-200 bg-white/90 px-6 backdrop-blur"
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
              <span className="text-[11px] uppercase tracking-[0.15em] text-[#5C6A61]">Welcome back</span>
              <span className="font-['Outfit'] text-base font-medium">{user.name}</span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {user.mfa_enabled ? (
              <span
                data-testid="mfa-indicator"
                className="hidden rounded-sm bg-[#EDF2EE] px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-[#526B58] md:inline-flex"
              >
                <ShieldCheck className="mr-1 h-3 w-3" /> MFA on
              </span>
            ) : (
              user.role !== "patient" && (
                <Link
                  to="/security"
                  data-testid="mfa-setup-banner"
                  className="hidden rounded-sm bg-[#FDF6ED] px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-[#D4A373] md:inline-flex"
                >
                  Enable MFA
                </Link>
              )
            )}

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  data-testid="user-menu-trigger"
                  className="flex items-center gap-3 rounded-sm px-2 py-1 text-sm font-medium hover:bg-stone-100"
                >
                  <span className="flex h-9 w-9 items-center justify-center rounded-sm bg-[#EDF2EE] text-xs font-semibold text-[#526B58]">
                    {initials}
                  </span>
                  <span className="hidden flex-col text-left leading-tight sm:flex">
                    <span className="text-[#1F2924]">{user.name}</span>
                    <span className="text-[11px] uppercase tracking-[0.15em] text-[#5C6A61]">
                      {roleLabel(user.role)}
                    </span>
                  </span>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>
                  <div className="flex flex-col">
                    <span className="text-[#1F2924]">{user.email}</span>
                    <span className="text-xs text-[#5C6A61]">{roleLabel(user.role)}</span>
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
                  className="text-[#C76D54] focus:text-[#C76D54]"
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
              <AlertDialogTitle className="font-['Outfit']">Are you still there?</AlertDialogTitle>
              <AlertDialogDescription>
                You will be signed out automatically in under a minute to protect
                patient data. Click Stay to continue.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogAction
                data-testid="idle-stay-btn"
                className="rounded-sm bg-[#7B9A82] hover:bg-[#65826C]"
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
