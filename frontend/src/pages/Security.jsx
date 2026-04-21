/**
 * Account — Self-service settings with Profile + Security tabs.
 *
 * Preserves the legacy /security route (still mounted under this page)
 * so every existing test-id, deep-link, and nav entry keeps working.
 * The original Security content is now rendered under the Security tab
 * via SecurityTab; the new Profile tab hosts self-service personal
 * details editing.
 */
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { BadgeCheck, ShieldCheck, User2 } from "lucide-react";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../components/ui/tabs";
import { useAuth } from "../contexts/AuthContext";
import ProfileTab from "./account/ProfileTab";
import SecurityTab from "./account/SecurityTab";
import LicensesTab from "./account/LicensesTab";

const CLINICIAN_ROLES = new Set(["admin", "doctor"]);
const VALID_TABS = new Set(["profile", "security", "licenses"]);

function tabFromLocation(location) {
  const search = new URLSearchParams(location.search);
  const raw = (search.get("tab") || "").toLowerCase();
  if (VALID_TABS.has(raw)) return raw;
  // Deep links from nav (e.g. /security) default to the Security tab.
  if (location.pathname.startsWith("/security")) return "security";
  return "profile";
}

export default function Account() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const isClinician = CLINICIAN_ROLES.has(user?.role);
  const [tab, setTab] = useState(() => tabFromLocation(location));

  const changeTab = (next) => {
    setTab(next);
    const search = new URLSearchParams(location.search);
    search.set("tab", next);
    navigate(`${location.pathname}?${search.toString()}`, { replace: true });
  };

  return (
    <div
      data-testid="security-page"
      className="space-y-8 animate-in fade-in duration-300"
    >
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Account settings
        </span>
        <h1 className="mt-2 font-display text-4xl font-medium tracking-tight">
          My account
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          Manage your profile and account security. Profile details —
          including your preferred signature block — appear on clinical
          notes and audit rows. Password and MFA protect every record.
        </p>
      </header>

      <Tabs
        value={tab}
        onValueChange={changeTab}
        data-testid="account-tabs"
        className="space-y-6"
      >
        <TabsList className="rounded-sm bg-card">
          <TabsTrigger
            value="profile"
            data-testid="account-tab-profile"
            className="rounded-sm"
          >
            <User2 className="mr-1.5 h-3.5 w-3.5" />
            Profile
          </TabsTrigger>
          <TabsTrigger
            value="security"
            data-testid="account-tab-security"
            className="rounded-sm"
          >
            <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />
            Security
          </TabsTrigger>
          {isClinician && (
            <TabsTrigger
              value="licenses"
              data-testid="account-tab-licenses"
              className="rounded-sm"
            >
              <BadgeCheck className="mr-1.5 h-3.5 w-3.5" />
              Licenses
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="profile">
          <ProfileTab />
        </TabsContent>

        <TabsContent value="security">
          <SecurityTab />
        </TabsContent>

        {isClinician && (
          <TabsContent value="licenses">
            <LicensesTab />
          </TabsContent>
        )}
      </Tabs>

      <p className="text-[11px] text-muted-foreground">
        Looking for organisation-wide security policies?{" "}
        <Link
          to="/security-config"
          className="underline underline-offset-2 hover:text-foreground"
        >
          Security settings
        </Link>{" "}
        (admins only).
      </p>
    </div>
  );
}
