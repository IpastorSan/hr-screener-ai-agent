import { CalendarCheckIcon, ClockIcon, ExternalLinkIcon, MapPinIcon, VideoIcon } from "lucide-react";
import type { ChatLocale, Ui } from "@/lib/types";
import { widgetCopy } from "./copy";
import { meetLabel, safeMeetUrl } from "./meet-link";
import { slotDay } from "./slot-picker";

/** The booked interview under Ana's confirmation. Display only: replying in the chat moves it.
 * In person: the location and its address. Video: the Google Meet link (new tab), or a note
 * that it comes by email while the calendar event has none. */
export function BookingCard({ ui, locale }: { ui: Extract<Ui, { kind: "booking" }>; locale?: ChatLocale }) {
  const copy = widgetCopy(locale);
  const time = ui.startsAt.slice(11, 16);
  const video = ui.mode === "video";
  const meet = video ? safeMeetUrl(ui.meetUrl) : null;
  return (
    <div
      data-testid="booking-card"
      data-mode={video ? "video" : "onsite"}
      className="flex w-full max-w-sm flex-col gap-3 rounded-[14px] border bg-card px-4 py-3.5"
    >
      <p className="inline-flex items-center gap-1.5 text-xs font-bold tracking-wider text-muted-foreground uppercase">
        <CalendarCheckIcon aria-hidden className="size-4 text-primary" />
        {copy.booked}
      </p>
      <div className="flex flex-col gap-0.5">
        <p className="font-heading text-lg leading-tight font-medium first-letter:uppercase">{slotDay(ui.startsAt, copy.intl)}</p>
        <p className="inline-flex items-center gap-1.5 text-sm tabular-nums">
          <ClockIcon aria-hidden className="size-3.5 text-muted-foreground" />
          {time}
        </p>
      </div>
      {video ? (
        <div className="flex items-start gap-1.5 border-t border-dashed pt-2.5 text-sm">
          <VideoIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
          <div className="flex min-w-0 flex-col gap-1">
            <span className="font-medium">{copy.videoCall}</span>
            {meet ? (
              <a
                href={meet}
                target="_blank"
                rel="noopener noreferrer"
                data-testid="meet-link"
                className="inline-flex w-fit items-center gap-1.5 rounded-lg bg-accent px-2.5 py-1.5 font-bold text-accent-foreground underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                {copy.meetLink}
                <ExternalLinkIcon aria-hidden className="size-3.5" />
                <span className="sr-only">{copy.newTab}</span>
              </a>
            ) : (
              <span className="text-muted-foreground">{copy.meetByEmail}</span>
            )}
            {meet && <span className="truncate text-xs text-muted-foreground tabular-nums">{meetLabel(meet)}</span>}
          </div>
        </div>
      ) : (
        <div className="flex items-start gap-1.5 border-t border-dashed pt-2.5 text-sm">
          <MapPinIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
          <p className="flex flex-col">
            <span className="font-medium">{ui.location}</span>
            {ui.address && <span className="text-muted-foreground">{ui.address}</span>}
          </p>
        </div>
      )}
      <p className="text-xs text-muted-foreground">{copy.bookedHint}</p>
    </div>
  );
}
