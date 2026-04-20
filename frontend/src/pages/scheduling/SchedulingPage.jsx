import { useState } from "react";
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
import DayView from "./DayView";
import WeekView from "./WeekView";
import MonthView from "./MonthView";
import YearView from "./YearView";
import BookDialog from "./BookDialog";
import { useScheduling } from "./useScheduling";
import { useClinicHours } from "./useClinicHours";

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
    prev,
    next,
    today,
    invalidate,
    goToDay,
    goToMonth,
  } = useScheduling({ view: "week" });

  const { hours: clinicHours, loading: hoursLoading } = useClinicHours();

  const [dialog, setDialog] = useState({ open: false, initial: null, defaultStart: null });
  const [confirmCancel, setConfirmCancel] = useState(null);

  const openNew = () => setDialog({ open: true, initial: null, defaultStart: null });
  const openNewAt = (d) => setDialog({ open: true, initial: null, defaultStart: d });
  const openReschedule = (a) => setDialog({ open: true, initial: a, defaultStart: null });

  async function doCancel(a) {
    try {
      await api.post(`/appointments/${a.id}/cancel`);
      toast.success("Appointment cancelled — notifications queued");
      invalidate();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to cancel");
    } finally {
      setConfirmCancel(null);
    }
  }

  return (
    <div data-testid="scheduling-page" className="space-y-8 animate-in fade-in duration-300">
      <SchedulingToolbar
        view={view}
        date={date}
        onViewChange={setView}
        onPrev={prev}
        onNext={next}
        onToday={today}
        onNew={openNew}
        canBook={canBook}
      />

      {loading && appointments.length === 0 ? (
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
              onOpenAppointment={(a) => {
                if (canBook && a.status === "scheduled") openReschedule(a);
              }}
              onCreateAt={(d) => openNewAt(d)}
            />
          )}
          {view === "week" && (
            <WeekView
              date={date}
              appointments={appointments}
              onOpenDay={(d) => goToDay(d)}
              onOpenAppointment={(a) => {
                if (canBook && a.status === "scheduled") openReschedule(a);
                else goToDay(new Date(a.start_time));
              }}
            />
          )}
          {view === "month" && (
            <MonthView date={date} appointments={appointments} onOpenDay={(d) => goToDay(d)} />
          )}
          {view === "year" && (
            <YearView
              date={date}
              appointments={appointments}
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
        onClose={() => setDialog({ open: false, initial: null, defaultStart: null })}
        onCancelAppointment={(a) => {
          setDialog({ open: false, initial: null, defaultStart: null });
          setConfirmCancel(a);
        }}
        onSaved={() => {
          invalidate();
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
