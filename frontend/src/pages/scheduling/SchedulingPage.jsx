import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../../api/client";
import { useAuth } from "../../contexts/AuthContext";
import { Skeleton } from "../../components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import SchedulingToolbar from "./SchedulingToolbar";
import NLBookCard from "./NLBookCard";
import DayView from "./DayView";
import WeekView from "./WeekView";
import MonthView from "./MonthView";
import YearView from "./YearView";
import BookDialog from "./BookDialog";
import { useScheduling } from "./useScheduling";
import { useClinicHours } from "./useClinicHours";
import { useAppointmentCounts } from "./useAppointmentCounts";

const STAFF_ROLES = ["admin", "doctor", "staff"];

export default function SchedulingPage() {
  const { user } = useAuth();
  const canBook = STAFF_ROLES.includes(user.role);
  const {
    view,
    setView,
    date,
    setDate,
    appointments,
    loading,
    providerId,
    setProviderId,
    includeCancelled,
    setIncludeCancelled,
    prev,
    next,
    today,
    invalidate,
    goToDay,
    goToMonth,
  } = useScheduling({ view: "week" });

  const { hours: clinicHours, loading: hoursLoading } = useClinicHours();
  const {
    countsByDate,
    loading: countsLoading,
    invalidate: invalidateCounts,
  } = useAppointmentCounts({
    view,
    date,
    providerId,
    includeCancelled,
    enabled: view !== "day",
  });

  const invalidateAll = () => {
    invalidate();
    invalidateCounts();
  };

  const [dialog, setDialog] = useState({ open: false, initial: null, defaultStart: null });
  const [confirmCancel, setConfirmCancel] = useState(null);

  const openNew = () => setDialog({ open: true, initial: null, defaultStart: null });
  const openNewAt = (d) => setDialog({ open: true, initial: null, defaultStart: d });
  const openReschedule = (a) => setDialog({ open: true, initial: a, defaultStart: null });

  // Consume URL params coming from the Checkout page's "Book follow-up"
  // button. Pre-fill BookDialog with patient/provider/type + optional
  // follow-up suggestion id so we can mark it resolved on save.
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const patientId = params.get("patient_id");
    const providerId = params.get("provider_id");
    const appointmentTypeId = params.get("appointment_type_id");
    const dateParam = params.get("date");
    const suggestionId = params.get("follow_up_suggestion_id");
    if (!patientId && !providerId && !suggestionId) return;
    let start = null;
    if (dateParam) {
      const d = new Date(dateParam);
      if (!Number.isNaN(d.getTime())) {
        // Seed with a reasonable mid-morning slot on the suggested date.
        d.setHours(9, 0, 0, 0);
        start = d;
        setDate(d);
      }
    }
    setDialog({
      open: true,
      initial: null,
      defaultStart: start,
      defaultPatientId: patientId,
      defaultProviderId: providerId,
      defaultAppointmentTypeId: appointmentTypeId,
      followUpSuggestionId: suggestionId,
    });
    // Clean the URL so a refresh doesn't re-open the dialog.
    navigate("/scheduling", { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function doCancel(a) {
    try {
      await api.post(`/appointments/${a.id}/cancel`);
      toast.success("Appointment cancelled — notifications queued");
      invalidateAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to cancel");
    } finally {
      setConfirmCancel(null);
    }
  }

  const showSkeleton = view === "day"
    ? loading && appointments.length === 0
    : countsLoading && Object.keys(countsByDate).length === 0;

  return (
    <div data-testid="scheduling-page" className="space-y-8 animate-in fade-in duration-300">
      {canBook && (
        <NLBookCard onBooked={() => invalidateAll()} />
      )}
      <SchedulingToolbar
        view={view}
        date={date}
        providerId={providerId}
        onProviderChange={setProviderId}
        includeCancelled={includeCancelled}
        onIncludeCancelledChange={setIncludeCancelled}
        onViewChange={setView}
        onPrev={prev}
        onNext={next}
        onToday={today}
        onNew={openNew}
        canBook={canBook}
      />

      {showSkeleton ? (
        <Skeleton data-testid="scheduling-skeleton" className="h-[560px] rounded-sm" />
      ) : (
        <>
          {view === "day" && (
            <DayView
              date={date}
              appointments={appointments}
              canBook={canBook}
              hours={clinicHours}
              hoursLoading={hoursLoading}
              hoursConfigured={!!clinicHours}
              includeCancelled={includeCancelled}
              onOpenAppointment={(a) => {
                // Staff may open any appointment — the reschedule dialog
                // doubles as the arrival/workflow panel.
                if (canBook) openReschedule(a);
              }}
              onCreateAt={(d) => openNewAt(d)}
            />
          )}
          {view === "week" && (
            <WeekView
              date={date}
              countsByDate={countsByDate}
              canBook={canBook}
              hours={clinicHours}
              includeCancelled={includeCancelled}
              onOpenDay={(d) => goToDay(d)}
              onOpenAppointment={(a) => {
                if (canBook) openReschedule(a);
                else goToDay(new Date(a.start_time));
              }}
              onCreateAt={(d) => openNewAt(d)}
            />
          )}
          {view === "month" && (
            <MonthView
              date={date}
              countsByDate={countsByDate}
              canBook={canBook}
              onOpenDay={(d) => goToDay(d)}
              onOpenAppointment={(a) => {
                if (canBook) openReschedule(a);
                else goToDay(new Date(a.start_time));
              }}
              onCreateAt={(d) => openNewAt(d)}
            />
          )}
          {view === "year" && (
            <YearView
              date={date}
              countsByDate={countsByDate}
              onOpenDay={(d) => goToDay(d)}
              onOpenMonth={(d) => goToMonth(d)}
            />
          )}
        </>
      )}

      <BookDialog
        open={dialog.open}
        initial={dialog.initial}
        defaultStart={dialog.defaultStart}
        defaultPatientId={dialog.defaultPatientId}
        defaultProviderId={dialog.defaultProviderId}
        defaultAppointmentTypeId={dialog.defaultAppointmentTypeId}
        followUpSuggestionId={dialog.followUpSuggestionId}
        onClose={() => setDialog({ open: false, initial: null, defaultStart: null })}
        onCancelAppointment={(a) => {
          setDialog({ open: false, initial: null, defaultStart: null });
          setConfirmCancel(a);
        }}
        onSaved={() => {
          invalidateAll();
          // If creating from day view on a different date, jump to that appt's day.
          if (!dialog.initial) setDate(new Date(date));
        }}
      />

      <AlertDialog open={!!confirmCancel} onOpenChange={(v) => !v && setConfirmCancel(null)}>
        <AlertDialogContent data-testid="scheduling-cancel-confirm" className="rounded-sm">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Cancel appointment?</AlertDialogTitle>
            <AlertDialogDescription>
              This will publish an <code className="text-primary">appointment.cancelled</code>{" "}
              event and queue a mock notification. The slot will open up immediately.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-sm">Keep it</AlertDialogCancel>
            <AlertDialogAction
              data-testid="scheduling-cancel-confirm-btn"
              onClick={() => confirmCancel && doCancel(confirmCancel)}
              className="rounded-sm bg-destructive hover:brightness-95"
            >
              Cancel appointment
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
