import {
  BellRing,
  BookOpen,
  Building2,
  CalendarDays,
  ClipboardCheck,
  ClipboardList,
  Coins,
  FileBarChart,
  FileStack,
  KeyRound,
  Landmark,
  LayoutDashboard,
  Receipt,
  Scale,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Table2,
  TrendingDown,
  Unlock,
  Upload,
  UserCog,
  UserRound,
  Users,
  Wallet,
} from "lucide-react";

/**
 * Sidebar nav is config-driven so we can add new items, reorder groups, or
 * change role gating without touching the layout component.
 *
 * Each group:
 *   - id: stable key used for collapse state + testid
 *   - label: visible section header
 *   - collapsible: whether the section can be folded (Settings/Governance)
 *   - items: NavItem[]
 *
 * Each item:
 *   - to: route path (kept stable — this refactor does NOT change URLs)
 *   - label: visible name in the sidebar
 *   - testId: stable data-testid for the NavLink
 *   - icon: lucide icon component
 *   - roles: allowed user.role values
 */

export const NAV_GROUPS = [
  {
    id: "operations",
    label: "Operations",
    collapsible: false,
    items: [
      {
        to: "/",
        label: "Dashboard",
        testId: "nav-dashboard",
        icon: LayoutDashboard,
        roles: ["admin", "doctor", "staff", "patient"],
      },
      {
        to: "/patients",
        label: "Patients",
        testId: "nav-patients",
        icon: Users,
        roles: ["admin", "doctor", "staff", "patient"],
      },
      {
        to: "/scheduling",
        label: "Scheduling",
        testId: "nav-scheduling",
        icon: CalendarDays,
        roles: ["admin", "doctor", "staff", "patient"],
      },
      {
        to: "/scheduling/flow-board",
        label: "Flow Board",
        testId: "nav-flow-board",
        icon: ClipboardCheck,
        roles: ["admin", "doctor", "staff"],
      },
    ],
  },
  {
    id: "financial",
    label: "Financial",
    collapsible: false,
    items: [
      {
        to: "/billing",
        label: "Billing",
        testId: "nav-billing",
        icon: Receipt,
        roles: ["admin", "doctor", "staff"],
      },
      {
        to: "/billing/claims",
        label: "Claims",
        testId: "nav-claims",
        icon: FileStack,
        roles: ["admin", "doctor", "staff"],
      },
      {
        to: "/billing/denials",
        label: "Denials",
        testId: "nav-denials",
        icon: ShieldAlert,
        roles: ["admin", "doctor", "staff"],
      },
      {
        to: "/billing/ar-aging",
        label: "A/R Aging",
        testId: "nav-ar-aging",
        icon: TrendingDown,
        roles: ["admin", "doctor", "staff"],
      },
      {
        to: "/billing/remittances/new",
        label: "Remittance Posting",
        testId: "nav-remittance-posting",
        icon: Wallet,
        roles: ["admin", "staff"],
      },
      {
        to: "/billing/remittances/import",
        label: "835 Imports",
        testId: "nav-835-imports",
        icon: Upload,
        roles: ["admin", "staff"],
      },
    ],
  },
  {
    id: "insights",
    label: "Insights",
    collapsible: false,
    items: [
      {
        to: "/reports",
        label: "Reports",
        testId: "nav-reports",
        icon: BookOpen,
        roles: ["admin", "doctor", "staff"],
      },
    ],
  },
  {
    id: "settings",
    label: "Settings",
    collapsible: true,
    items: [
      {
        to: "/settings/clinic",
        label: "Clinic Settings",
        testId: "nav-clinic-settings",
        icon: Building2,
        roles: ["admin"],
      },
      {
        to: "/settings/appointment-types",
        label: "Appointment Types",
        testId: "nav-appointment-types",
        icon: ClipboardList,
        roles: ["admin"],
      },
      {
        to: "/settings/payers",
        label: "Payers",
        testId: "nav-payers",
        icon: Landmark,
        roles: ["admin"],
      },
      {
        to: "/settings/fee-schedules",
        label: "Fee Schedules",
        testId: "nav-fee-schedules",
        icon: Coins,
        roles: ["admin"],
      },
      {
        to: "/notifications",
        label: "Notifications",
        testId: "nav-notifications",
        icon: BellRing,
        roles: ["admin", "staff"],
      },
    ],
  },
  {
    id: "governance",
    label: "Governance",
    collapsible: true,
    items: [
      {
        to: "/audit-log",
        label: "Audit Log",
        testId: "nav-audit-log",
        icon: Shield,
        roles: ["admin"],
      },
      {
        to: "/compliance",
        label: "Compliance",
        testId: "nav-compliance",
        icon: ClipboardCheck,
        roles: ["admin"],
      },
      {
        to: "/privacy",
        label: "Privacy",
        testId: "nav-privacy",
        icon: Scale,
        roles: ["admin"],
      },
      {
        to: "/security",
        label: "My account",
        testId: "nav-security-dashboard",
        icon: UserRound,
        roles: ["admin", "doctor", "staff", "patient"],
      },
      {
        to: "/security-config",
        label: "Security Settings",
        testId: "nav-security-settings",
        icon: KeyRound,
        roles: ["admin"],
      },
      {
        to: "/roles",
        label: "Roles",
        testId: "nav-roles",
        icon: UserCog,
        roles: ["admin"],
      },
      {
        to: "/permissions",
        label: "Permissions",
        testId: "nav-permissions",
        icon: Table2,
        roles: ["admin"],
      },
      {
        to: "/access-review",
        label: "Access Review",
        testId: "nav-access-review",
        icon: FileBarChart,
        roles: ["admin"],
      },
      {
        to: "/elevation",
        label: "Elevation",
        testId: "nav-elevation",
        icon: Unlock,
        roles: ["admin", "doctor", "staff"],
      },
    ],
  },
];

/**
 * Filter groups + items by the current user role. A group is dropped entirely
 * when it has no visible items so the sidebar stays clean for patients etc.
 */
export function visibleGroupsForRole(role) {
  return NAV_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((i) => i.roles.includes(role)),
  })).filter((g) => g.items.length > 0);
}
