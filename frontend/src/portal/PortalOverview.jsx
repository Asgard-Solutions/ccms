/**
 * Portal overview — upcoming appointments (with Check-in), booking
 * requests, pending questionnaires.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  CalendarDays, CheckCircle2, ClipboardList, LogIn, Plus, Sparkles,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Skeleton } from "../components/ui/skeleton";
import {
  fetchPortalOverview, portalCheckIn,
} from "../api/portal";
import PortalVisitBriefCard from "./PortalVisitBriefCard";
import { formatDateTime } from "../utils/time";

function isToday(iso) {
  if (!iso) return false;
  const d = new Date(iso);
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

function Card({ children, className = "", testid }) {
  return (
    <section
      data-testid={testid}
      className={`rounded-md border border-border bg-card p-5 ${className}`}
    >
      {children}
    </section>
  );
}

export default function PortalOverview() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [checkingIn, setCheckingIn] = useState(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchPortalOverview());
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load overview");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCheckIn(appointmentId) {
    setCheckingIn(appointmentId);
    try {
      await portalCheckIn(appointmentId);
      toast.success("You're checked in. See you soon!");
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Check-in failed");
    } finally {
      setCheckingIn(null);
    }
  }

  if (loading) {
    return (
      <div className="space-y-4" data-testid="portal-overview-loading">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  const d = data || {
    upcoming_appointments: [],
    pending_booking_requests: [],
    pending_questionnaires: [],
  };

  return (
    <div data-testid="portal-overview" className="space-y-6">
      {/* AI visit brief — the card self-hides if the LLM has nothing
          useful to say (e.g., a brand-new patient with no prior visit). */}
      <PortalVisitBriefCard />

      {/* Upcoming appointments */}
      <Card testid="portal-upcoming-appointments">
        <header className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CalendarDays className="h-4 w-4 text-primary" />
            <h2 className="text-base font-medium">Upcoming appointments</h2>
          </div>
          <Link to="/portal/book" data-testid="portal-book-link">
            <Button size="sm" variant="outline" className="h-8 rounded-sm">
              <Plus className="mr-1 h-3.5 w-3.5" />
              Request new
            </Button>
          </Link>
        </header>
        {d.upcoming_appointments.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No upcoming appointments.
          </p>
        ) : (
          <ul className="space-y-2">
            {d.upcoming_appointments.map((a) => (
              <li
                key={a.id}
                data-testid={`portal-appt-row-${a.id}`}
                className="flex items-center justify-between rounded-sm border border-border/60 px-3 py-2.5"
              >
                <div>
                  <p className="font-medium text-sm">
                    {formatDateTime(a.start_time)}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Status: {a.status}
                    {a.arrived_via ? ` · arrived via ${a.arrived_via}` : ""}
                  </p>
                </div>
                {isToday(a.start_time) && a.status === "scheduled" && (
                  <Button
                    size="sm"
                    data-testid={`portal-checkin-btn-${a.id}`}
                    disabled={checkingIn === a.id}
                    onClick={() => handleCheckIn(a.id)}
                  >
                    <LogIn className="mr-1 h-3.5 w-3.5" />
                    Check in
                  </Button>
                )}
                {a.status === "arrived" && (
                  <Badge variant="outline" className="gap-1">
                    <CheckCircle2 className="h-3 w-3 text-green-600" />
                    Checked in
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Pending questionnaires */}
      <Card testid="portal-pending-questionnaires">
        <header className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ClipboardList className="h-4 w-4 text-primary" />
            <h2 className="text-base font-medium">Questionnaires</h2>
          </div>
          <Link to="/portal/questionnaires" data-testid="portal-questionnaires-link">
            <Button size="sm" variant="ghost" className="h-8 rounded-sm">
              See all
            </Button>
          </Link>
        </header>
        {d.pending_questionnaires.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing waiting for you.
          </p>
        ) : (
          <ul className="space-y-2">
            {d.pending_questionnaires.map((q) => (
              <li
                key={q.id}
                data-testid={`portal-q-row-${q.id}`}
                className="flex items-center justify-between rounded-sm border border-border/60 px-3 py-2.5"
              >
                <div>
                  <p className="font-medium text-sm">{q.template_title}</p>
                  <p className="text-xs text-muted-foreground">
                    Due {formatDateTime(q.due_at)}
                  </p>
                </div>
                <Link to={`/portal/questionnaires/${q.id}`}>
                  <Button
                    size="sm"
                    data-testid={`portal-q-open-${q.id}`}
                    className="rounded-sm"
                  >
                    Start
                  </Button>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Booking requests */}
      <Card testid="portal-pending-booking-requests">
        <header className="mb-4 flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" />
          <h2 className="text-base font-medium">Booking requests</h2>
        </header>
        {d.pending_booking_requests.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No pending requests. Use "Request new" above to book a visit.
          </p>
        ) : (
          <ul className="space-y-2">
            {d.pending_booking_requests.map((b) => (
              <li
                key={b.id}
                data-testid={`portal-booking-row-${b.id}`}
                className="rounded-sm border border-border/60 px-3 py-2.5"
              >
                <p className="text-sm font-medium">{b.reason || "Visit"}</p>
                <p className="text-xs text-muted-foreground">
                  {b.preferred_slots?.length || 0} preferred slot(s) · pending front-desk review
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
