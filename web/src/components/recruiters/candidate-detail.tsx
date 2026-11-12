"use client";

import Link from "next/link";
import { ArrowLeftIcon, BotIcon, CalendarClockIcon, DownloadIcon, RefreshCwIcon, SparklesIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { downloadExport, getCandidate, updateCandidate } from "@/lib/recruiter-api";
import type { CandidateDetail, DisqualifyReason, ReviewStatus, SpecField } from "@/lib/recruiter-types";
import { cn } from "@/lib/utils";
import { formatValue, useAgentSpec } from "./agent-spec";
import { CompanionBadge, QualityFlagChip, ScreeningBadge, SentimentIcon } from "./badges";
import { BookingCard } from "./booking-card";
import { CopyButton } from "./copy-button";
import { fieldLabel, valueOf } from "./field-values";
import { CHANNEL_LABEL, COUNTRY_LABEL, formatDateTime, formatRelative, LANGUAGE_LABEL, MODE_LABEL, REVIEW_LABEL, REASON_LABEL, TALENT_POOL_LABEL } from "./labels";
import { Conversation } from "./transcript";
import { StaleNotice } from "./stale-notice";
import { usePolling } from "./use-polling";

const MAX_NOTE = 2000;

export function CandidateDetailView({ id }: { id: string }) {
  const { data, error, stale, refresh } = usePolling(id, (signal) => getCandidate(id, { signal }));
  const spec = useAgentSpec();

  if (error && !data) {
    const missing = error instanceof ApiError && error.status === 404;
    return (
      <div className="flex flex-col items-start gap-4">
        <BackLink />
        <div className="flex w-full flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-14 text-center">
          <p className="font-heading text-lg font-medium">{missing ? "Candidato no encontrado" : "No se pudo cargar el candidato"}</p>
          {!missing && (
            <Button variant="outline" size="sm" onClick={refresh}>
              <RefreshCwIcon data-icon="inline-start" />
              Reintentar
            </Button>
          )}
        </div>
      </div>
    );
  }
  if (!data || !spec) return <DetailSkeleton />;
  return (
    <div className="flex flex-col gap-4">
      <StaleNotice stale={stale} onRetry={refresh} />
      <Detail candidate={data} fields={spec.fields} onChanged={refresh} />
    </div>
  );
}

function BackLink() {
  return (
    <Link href="/recruiters" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
      <ArrowLeftIcon className="size-4" aria-hidden />
      Candidatos
    </Link>
  );
}

function Detail({ candidate, fields, onChanged }: { candidate: CandidateDetail; fields: SpecField[]; onChanged: () => void }) {
  const [exporting, setExporting] = useState(false);
  const exportJson = async () => {
    setExporting(true);
    try {
      await downloadExport(candidate.id);
    } catch {
      toast.error("No se pudo exportar");
    } finally {
      setExporting(false);
    }
  };
  const location = [candidate.location_name, candidate.country && COUNTRY_LABEL[candidate.country]].filter(Boolean).join(" · ");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3">
        <BackLink />
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="flex min-w-0 flex-col gap-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-heading text-2xl font-normal tracking-tight break-words sm:text-3xl">{candidate.name ?? "Sin nombre"}</h1>
              <ScreeningBadge status={candidate.status} interview={Boolean(candidate.booking)} />
            </div>
            <p className="text-sm text-muted-foreground">
              {location || "Sin ubicación"} · última actividad {formatRelative(candidate.last_activity_at)}
            </p>
          </div>
          <Button variant="outline" onClick={exportJson} disabled={exporting} className="self-start sm:self-auto">
            <DownloadIcon data-icon="inline-start" />
            Exportar JSON
          </Button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] lg:grid-rows-[auto_1fr] lg:gap-6">
        <div className="min-w-0 lg:col-start-2 lg:row-start-1">
          <SummaryCard candidate={candidate} />
        </div>
        <div className="flex flex-col gap-4 lg:col-start-1 lg:row-span-2 lg:row-start-1">
          {candidate.booking && <BookingCard booking={candidate.booking} />}
          <ProfileCard candidate={candidate} fields={fields} />
          <FlagsCard candidate={candidate} />
          <ReviewCard candidate={candidate} onChanged={onChanged} />
        </div>
        <Card className="min-w-0 self-start lg:col-start-2 lg:row-start-2">
          <CardHeader>
            <CardTitle>Conversación</CardTitle>
            <CardDescription>Cada mensaje del candidato lleva lo que extrajo el modelo; cada respuesta de Ana, la acción que decidió el motor.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-8">
            {candidate.conversations.length === 0 ? (
              <p className="text-sm text-muted-foreground">Sin conversaciones.</p>
            ) : (
              candidate.conversations.map((conversation, index) => (
                <Conversation key={conversation.id} conversation={conversation} index={index} total={candidate.conversations.length} />
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Field({ label, children, copy }: { label: string; children: React.ReactNode; copy?: string | null }) {
  return (
    <div className="grid grid-cols-[7.5rem_minmax(0,1fr)] items-center gap-2 py-1.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="flex min-w-0 items-center gap-1 text-sm">
        <span className="min-w-0 break-words">{children}</span>
        {copy && <CopyButton value={copy} label={label} />}
      </dd>
    </div>
  );
}

/** Lets a long address wrap after the @ instead of mid-word. */
function Email({ value }: { value: string | null }) {
  if (!value) return <>—</>;
  const at = value.indexOf("@");
  if (at < 0) return <>{value}</>;
  return (
    <>
      {value.slice(0, at)}
      <wbr />@{value.slice(at + 1)}
    </>
  );
}

/** A field's row on the profile card, by its type. A contact is the candidate's own (the record's
 * normalized value first) and copyable. */
function ProfileField({ candidate, field }: { candidate: CandidateDetail; field: SpecField }) {
  const { profile } = candidate;
  const label = fieldLabel(field, candidate.country);
  switch (field.type) {
    case "contact": {
      const value = (valueOf(field, candidate) as string | null | undefined) ?? (valueOf(field, profile) as string | null);
      return (
        <Field label={label} copy={value}>
          {field.channel === "email" ? <Email value={value} /> : <span className="tabular-nums">{value ?? "—"}</span>}
        </Field>
      );
    }
    case "location":
      return (
        <Field label={label}>
          {candidate.location_name ? (
            <>
              {candidate.location_name}
              <span className="block text-xs text-muted-foreground">
                {[candidate.city, candidate.country && COUNTRY_LABEL[candidate.country]].filter(Boolean).join(", ")}
              </span>
            </>
          ) : candidate.talent_pool ? (
            TALENT_POOL_LABEL
          ) : (
            "—"
          )}
        </Field>
      );
    case "years":
    case "date":
      return (
        <Field label={label}>
          <span className={cn("inline-flex flex-wrap items-center gap-1.5", field.type === "date" && "tabular-nums")}>
            {formatValue(field, profile)}
            <CompanionBadge field={field} values={profile} />
          </span>
        </Field>
      );
    default:
      return <Field label={label}>{formatValue(field, profile)}</Field>;
  }
}

function ProfileCard({ candidate, fields }: { candidate: CandidateDetail; fields: SpecField[] }) {
  const { profile } = candidate;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Perfil</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="divide-y">
          {fields.map((field) => (
            <ProfileField key={field.id} candidate={candidate} field={field} />
          ))}
          <Field label="Entrevista">{profile.interview_mode ? MODE_LABEL[profile.interview_mode] : "—"}</Field>
          <Field label="Idioma">{candidate.language ? LANGUAGE_LABEL[candidate.language] : "—"}</Field>
          <Field label="Canal">{CHANNEL_LABEL[candidate.channel]}</Field>
          {candidate.conversations
            .filter((conversation) => conversation.elevenlabs_conversation_id)
            .map((conversation) => (
              <Field key={conversation.id} label="Llamada (ElevenLabs)" copy={conversation.elevenlabs_conversation_id}>
                <span className="font-mono text-xs">{conversation.elevenlabs_conversation_id}</span>
              </Field>
            ))}
          <Field label="Ánimo">
            <SentimentIcon sentiment={candidate.sentiment} withLabel />
          </Field>
          <Field label="Solicitud">{formatDateTime(candidate.created_at)}</Field>
          {candidate.closed_at && <Field label="Cierre">{formatDateTime(candidate.closed_at)}</Field>}
          {candidate.outcome_reason && (
            <Field label="Motivo">
              {REASON_LABEL[candidate.outcome_reason as DisqualifyReason] ?? candidate.outcome_reason}
            </Field>
          )}
        </dl>
      </CardContent>
    </Card>
  );
}

function FlagsCard({ candidate }: { candidate: CandidateDetail }) {
  const late = candidate.late_start_days;
  if (late === null && candidate.quality_flags.length === 0) return null;
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Avisos</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {late !== null && (
          <p className="flex items-center gap-2 text-sm">
            <CalendarClockIcon className="size-4 shrink-0 text-warn" aria-hidden />
            Inicio tardío: empieza dentro de {late} días.
          </p>
        )}
        {candidate.quality_flags.length > 0 && (
          <div className="flex flex-col gap-2 rounded-lg border border-dashed border-qa/40 bg-qa/5 p-2.5">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-qa">
              <BotIcon className="size-3.5" aria-hidden />
              Calidad del bot · QA interno
            </p>
            <div className="flex flex-wrap gap-1">
              {candidate.quality_flags.map((flag) => (
                <QualityFlagChip key={flag} flag={flag} />
              ))}
            </div>
            <p className="text-[0.7rem] text-muted-foreground">Señales para mejorar a Ana. No dicen nada del candidato.</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function SummaryCard({ candidate }: { candidate: CandidateDetail }) {
  const { summary } = candidate;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <SparklesIcon className="size-4 text-primary" aria-hidden />
          Resumen
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {!summary ? (
          <p className="text-sm text-muted-foreground">Ana escribirá el resumen cuando termine la entrevista.</p>
        ) : (
          <>
            <div className="flex flex-col gap-1.5 text-sm leading-relaxed">
              {summary.lines.map((line, index) => (
                <p key={index}>{line}</p>
              ))}
            </div>
            {summary.flags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {summary.flags.map((flag, index) => (
                  <Badge key={index} variant="outline">
                    {flag}
                  </Badge>
                ))}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

const REVIEW_ITEMS = REVIEW_LABEL as Record<string, string>;

function ReviewCard({ candidate, onChanged }: { candidate: CandidateDetail; onChanged: () => void }) {
  const [saving, setSaving] = useState<ReviewStatus | null>(null);
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);

  const changeStatus = async (status: ReviewStatus) => {
    if (status === candidate.review_status) return;
    setSaving(status);
    try {
      await updateCandidate(candidate.id, { status });
      toast.success(`Marcado como ${REVIEW_LABEL[status].toLowerCase()}`);
      onChanged();
    } catch {
      toast.error("No se pudo guardar el estado");
    } finally {
      setSaving(null);
    }
  };

  const addNote = async (event: React.FormEvent) => {
    event.preventDefault();
    const text = note.trim();
    if (!text) return;
    setSending(true);
    try {
      await updateCandidate(candidate.id, { note: text });
      setNote("");
      onChanged();
    } catch {
      toast.error("No se pudo guardar la nota");
    } finally {
      setSending(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Seguimiento</CardTitle>
        <CardDescription>Tu decisión, independiente del resultado de la criba.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <Select
          items={REVIEW_ITEMS}
          value={saving ?? candidate.review_status}
          onValueChange={(value) => value && changeStatus(value as ReviewStatus)}
          disabled={saving !== null}
        >
          <SelectTrigger aria-label="Estado de revisión" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(REVIEW_LABEL).map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-muted-foreground">Notas</h3>
          {candidate.notes.length === 0 ? (
            <p className="text-sm text-muted-foreground">Sin notas todavía.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {[...candidate.notes].reverse().map((item, index) => (
                <li key={`${item.created_at}-${index}`} className="rounded-lg bg-muted/60 px-3 py-2">
                  <p className="text-sm whitespace-pre-wrap break-words">{item.text}</p>
                  <p className="mt-1 text-[0.7rem] text-muted-foreground">{formatDateTime(item.created_at)}</p>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={addNote} className="flex flex-col gap-2">
            <Textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Añade una nota para el equipo"
              aria-label="Nueva nota"
              maxLength={MAX_NOTE}
              rows={3}
              className="bg-card"
            />
            <Button type="submit" size="sm" disabled={sending || !note.trim()} className="self-end">
              Añadir nota
            </Button>
          </form>
        </div>
      </CardContent>
    </Card>
  );
}

export function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-6" role="status" aria-label="Cargando candidato">
      <div className="flex flex-col gap-3">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-48" />
      </div>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <Skeleton className="h-96 rounded-xl" />
        <div className="flex flex-col gap-4">
          <Skeleton className="h-32 rounded-xl" />
          <Skeleton className="h-[28rem] rounded-xl" />
        </div>
      </div>
    </div>
  );
}
