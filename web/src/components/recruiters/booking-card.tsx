import { CalendarCheckIcon, MapPinIcon, UserRoundIcon, VideoIcon } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { meetLabel, safeMeetUrl } from "@/components/widgets/meet-link";
import type { BookingOut } from "@/lib/recruiter-types";
import { formatDateTime, formatLocalDay, interviewerLabel, MODE_LABEL } from "./labels";

/** The interview the candidate booked in the chat, in the location's local time. */
export function BookingCard({ booking }: { booking: BookingOut }) {
  const local = booking.local_start;
  const day = formatLocalDay(local);
  const start = local.slice(11, 16);
  const end = new Date(booking.ends_at).getTime() - new Date(booking.starts_at).getTime();
  const minutes = Math.round(end / 60000);
  const meet = safeMeetUrl(booking.meet_url);
  const interviewer = interviewerLabel(booking);
  return (
    <Card size="sm" className="border-t-4 border-t-primary" data-testid="booking">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CalendarCheckIcon className="size-4 text-primary" aria-hidden />
          Entrevista
        </CardTitle>
        <CardDescription>Reservada por el candidato en el chat · {formatDateTime(booking.booked_at)}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <div>
          <p className="font-heading text-lg leading-tight font-medium first-letter:uppercase">{day}</p>
          <p className="text-sm tabular-nums">
            {start} · {minutes} min <span className="text-muted-foreground">(hora local)</span>
          </p>
        </div>
        {booking.mode === "video" ? (
          <p className="flex items-start gap-1.5 text-sm" data-testid="booking-video">
            <VideoIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            <span className="flex min-w-0 flex-col">
              <span className="font-medium">Videollamada · {booking.location_name ?? booking.location_id}</span>
              {meet ? (
                <a href={meet} target="_blank" rel="noopener noreferrer" className="truncate text-primary underline-offset-4 hover:underline">
                  {meetLabel(meet)}
                </a>
              ) : (
                <span className="text-muted-foreground">Enlace de Meet pendiente</span>
              )}
            </span>
          </p>
        ) : (
          <p className="flex items-start gap-1.5 text-sm">
            <MapPinIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            <span className="flex flex-col">
              <span className="font-medium">{booking.location_name ?? booking.location_id}</span>
              {booking.address && <span className="text-muted-foreground">{booking.address}</span>}
            </span>
          </p>
        )}
        <p className="flex items-start gap-1.5 text-sm" data-testid="booking-recruiter">
          <UserRoundIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
          <span>
            {interviewer ? (
              <>
                Con <span className="font-medium">{interviewer}</span>
              </>
            ) : (
              <span className="text-muted-foreground">Sin reclutador asignado</span>
            )}
            {booking.mode && <span className="text-muted-foreground"> · {MODE_LABEL[booking.mode]}</span>}
          </span>
        </p>
      </CardContent>
    </Card>
  );
}
