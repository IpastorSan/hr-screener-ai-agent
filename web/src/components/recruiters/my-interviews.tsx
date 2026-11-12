"use client";

import { CalendarXIcon, MapPinIcon, PhoneIcon, RefreshCwIcon, TriangleAlertIcon, VideoIcon } from "lucide-react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { listInterviews } from "@/lib/recruiter-api";
import type { InterviewItem } from "@/lib/recruiter-types";
import { CALENDAR_ISSUE_LABEL, formatLocalDay, interviewerLabel, MODE_LABEL } from "./labels";
import { useSession } from "./session";
import { StaleNotice } from "./stale-notice";
import { usePolling } from "./use-polling";

function byDay(items: InterviewItem[]): [string, InterviewItem[]][] {
  const days = new Map<string, InterviewItem[]>();
  for (const item of items) {
    const key = item.local_start.slice(0, 10);
    days.set(key, [...(days.get(key) ?? []), item]);
  }
  return [...days.entries()];
}

function minutes(item: InterviewItem): number {
  return Math.round((new Date(item.ends_at).getTime() - new Date(item.starts_at).getTime()) / 60000);
}

function InterviewRow({ item }: { item: InterviewItem }) {
  const video = item.mode === "video";
  return (
    <li>
      <Link
        href={`/recruiters/${item.candidate_id}`}
        className="flex items-start gap-4 rounded-xl bg-card p-3 ring-1 ring-foreground/10 outline-none hover:ring-foreground/20 focus-visible:ring-3 focus-visible:ring-ring/50 sm:items-center sm:p-4"
      >
        <div className="flex w-14 shrink-0 flex-col items-start sm:w-16">
          <span className="font-heading text-lg leading-none font-medium tabular-nums">{item.local_start.slice(11, 16)}</span>
          <span className="mt-1 text-xs text-muted-foreground tabular-nums">{minutes(item)} min</span>
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-1 sm:flex-row sm:items-center sm:gap-4">
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">{item.candidate_name ?? "Sin nombre"}</p>
            <p className="flex items-center gap-1 truncate text-xs text-muted-foreground">
              <PhoneIcon aria-hidden className="size-3 shrink-0" />
              {item.phone ?? item.email ?? "—"}
            </p>
          </div>
          <div className="flex min-w-0 flex-col text-sm text-muted-foreground sm:w-64">
            <p className="flex min-w-0 items-center gap-1">
              {video ? <VideoIcon aria-hidden className="size-3.5 shrink-0" /> : <MapPinIcon aria-hidden className="size-3.5 shrink-0" />}
              <span className="truncate">{video ? "Videollamada" : (item.location_name ?? item.location_id)}</span>
            </p>
            {!video && interviewerLabel(item) && <p className="truncate text-xs">Con {interviewerLabel(item)}</p>}
          </div>
        </div>
        {item.calendar_issue && (
          <Badge variant="destructive" className="shrink-0" title="Cambiada en Google Calendar">
            <TriangleAlertIcon data-icon="inline-start" />
            {CALENDAR_ISSUE_LABEL[item.calendar_issue]}
          </Badge>
        )}
        {item.mode && (
          <Badge variant="outline" className="shrink-0">
            {MODE_LABEL[item.mode]}
          </Badge>
        )}
      </Link>
    </li>
  );
}

function Agenda({ items, testId }: { items: InterviewItem[]; testId: string }) {
  return (
    <div className="flex flex-col gap-6" data-testid={testId}>
      {byDay(items).map(([day, dayItems]) => (
        <section key={day} aria-label={formatLocalDay(dayItems[0].local_start)} className="flex flex-col gap-2">
          <h3 className="flex items-baseline gap-2 font-heading text-base font-medium">
            {formatLocalDay(dayItems[0].local_start)}
            <span className="text-xs font-normal text-muted-foreground tabular-nums">
              {dayItems.length} {dayItems.length === 1 ? "entrevista" : "entrevistas"}
            </span>
          </h3>
          <ul className="flex flex-col gap-2">
            {dayItems.map((item) => (
              <InterviewRow key={item.booking_id} item={item} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** The signed-in recruiter's upcoming video interviews, grouped by local day, and below them
 * the in-person ones at their locations (every location for an admin), which the location's
 * manager runs: for them to see only. */
export function MyInterviews() {
  const { me } = useSession();
  const list = usePolling("mine", (signal) => listInterviews({ signal }), 30_000);
  if (list.error && !list.data) {
    return (
      <div className="flex flex-col items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-6 py-12 text-center">
        <p className="font-medium">No se pudieron cargar tus entrevistas.</p>
        <Button variant="outline" size="sm" onClick={list.refresh}>
          <RefreshCwIcon data-icon="inline-start" />
          Reintentar
        </Button>
      </div>
    );
  }
  if (!list.data) {
    return (
      <div aria-busy role="status" aria-label="Cargando entrevistas" className="flex flex-col gap-2">
        <Skeleton className="h-5 w-40" />
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-16 rounded-xl" />
        ))}
      </div>
    );
  }
  const notice = <StaleNotice stale={list.stale} onRetry={list.refresh} />;
  if (list.data.length === 0) {
    return (
      <>
        {notice}
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-14 text-center">
          <CalendarXIcon className="size-8 text-muted-foreground" aria-hidden />
          <p className="font-heading text-lg font-medium">No tienes entrevistas próximas</p>
          <p className="max-w-sm text-sm text-muted-foreground">Cuando un candidato reserve una entrevista en una de tus ubicaciones, aparecerá aquí.</p>
        </div>
      </>
    );
  }
  const own = list.data.filter((item) => item.mode === "video");
  const inPerson = list.data.filter((item) => item.mode !== "video");
  return (
    <>
      {notice}
      {own.length > 0 ? (
        <Agenda items={own} testId="my-interviews" />
      ) : (
        <p className="rounded-xl border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">No tienes videollamadas próximas.</p>
      )}
      {inPerson.length > 0 && (
        <section className="mt-8 flex flex-col gap-3" aria-labelledby="in-person-heading">
          <div className="flex flex-col gap-1 border-t pt-6">
            <h2 id="in-person-heading" className="font-heading text-xl font-normal tracking-tight">
              {me.is_admin ? "En persona en todos los locales" : "En persona en mis locales"}
            </h2>
            <p className="text-sm text-muted-foreground">Las hace la gerencia de cada local. Aquí solo las ves.</p>
          </div>
          <Agenda items={inPerson} testId="in-person-interviews" />
        </section>
      )}
    </>
  );
}
