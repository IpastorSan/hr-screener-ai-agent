"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { BotIcon, RefreshCwIcon } from "lucide-react";
import { useCallback, useMemo } from "react";
import { StatCard } from "@/components/insights/stat-card";
import { SENTIMENT_CLASS, SENTIMENT_ICON } from "@/components/recruiters/badges";
import { useStepLabel } from "@/components/recruiters/agent-spec";
import { CHANNEL_LABEL, COUNTRY_LABEL, LANGUAGE_LABEL, QUALITY_FLAG_LABEL, REASON_LABEL, SENTIMENT_LABEL } from "@/components/recruiters/labels";
import { useSession } from "@/components/recruiters/session";
import { StaleNotice } from "@/components/recruiters/stale-notice";
import { usePolling } from "@/components/recruiters/use-polling";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getAnalytics } from "@/lib/recruiter-api";
import type { Analytics, AnalyticsFilters, Breakdown, Channel, Country, Language, Sentiment } from "@/lib/recruiter-types";
import { cn } from "@/lib/utils";
import { BarList } from "./bar-list";
import { FaqMisses } from "./faq-misses";
import { DashboardFilters } from "./filters";
import { DEFAULT_RANGE, readQuery, toFilters, type DashboardQuery } from "./query";
import { EMPTY, formatDecimal, formatDuration, formatLatency, formatPercent, formatUsd } from "./format";

const SENTIMENT_ORDER: Sentiment[] = ["positive", "neutral", "confused", "frustrated"];

export function AnalyticsDashboard() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { me } = useSession();
  const query = useMemo(() => readQuery(new URLSearchParams(params.toString())), [params]);
  const filters = toFilters(query);
  const key = JSON.stringify(filters);
  const result = usePolling(key, (signal) => getAnalytics(filters, { signal }), 30_000);

  const navigate = useCallback(
    (patch: Partial<DashboardQuery>) => {
      const next = new URLSearchParams(params.toString());
      for (const [name, value] of Object.entries(patch)) {
        if (value === undefined || (name === "range" && value === DEFAULT_RANGE)) next.delete(name);
        else next.set(name, String(value));
      }
      const text = next.toString();
      router.replace(text ? `${pathname}?${text}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  const data = result.data;
  return (
    <div className="flex flex-col gap-5">
      <DashboardFilters query={query} onChange={navigate} />
      <StaleNotice stale={result.stale} onRetry={result.refresh} />
      {result.error && !data ? (
        <ErrorState onRetry={result.refresh} />
      ) : !data ? (
        <LoadingState />
      ) : data.totals.started === 0 ? (
        <EmptyState scoped={!me.is_admin} />
      ) : (
        <div className={cn("flex flex-col gap-8 transition-opacity", result.pending && "opacity-60")} aria-busy={result.pending}>
          <Screening data={data} />
          <BotQuality data={data} filters={filters} />
        </div>
      )}
    </div>
  );
}

// --- screening ---

function Screening({ data }: { data: Analytics }) {
  const { totals, rates, durations, reengagement } = data;
  const answeredConsent = totals.consented + totals.declined_consent;
  return (
    <section aria-labelledby="screening-heading" className="flex flex-col gap-4">
      <h2 id="screening-heading" className="sr-only">
        Criba
      </h2>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Conversaciones" value={totals.started.toLocaleString("es-ES")} hint={`${totals.consented} aceptaron la privacidad`} />
        <Stat label="Completan la criba" value={formatPercent(rates.completion_rate)} hint={completionHint(totals)} />
        <Stat label="Aptos" value={formatPercent(rates.qualified_rate)} hint={`${totals.qualified} de ${totals.consented} que empezaron`} />
        <Stat
          label="Duración mediana"
          value={formatDuration(durations.p50_seconds)}
          hint={durations.p50_turns === null ? "Nadie ha terminado aún" : `${formatDecimal(durations.p50_turns)} mensajes hasta el resultado`}
        />
        <Stat
          label="Rechazan la privacidad"
          value={formatPercent(rates.consent_decline_rate)}
          hint={`${totals.declined_consent} de ${answeredConsent} que respondieron`}
        />
        <Stat
          label="Recuperación"
          value={reengagement.recovery_rate === null ? EMPTY : formatPercent(reengagement.recovery_rate)}
          hint={reengagement.nudged === 0 ? "Sin recordatorios enviados todavía" : `${reengagement.recovered} de ${reengagement.nudged} vuelven tras un recordatorio`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-5">
        <Funnel data={data} className="lg:col-span-3" />
        <div className="flex flex-col gap-4 lg:col-span-2">
          <DropOff data={data} />
          <Reasons data={data} />
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Split
          title="Por país"
          rows={data.by_country}
          label={(key) => COUNTRY_LABEL[key as Country] ?? key}
        />
        <Split
          title={data.by_channel.length > 1 ? "Por idioma y canal" : "Por idioma"}
          rows={data.by_language}
          label={(key) => LANGUAGE_LABEL[key as Language] ?? key}
          extra={data.by_channel.length > 1 ? { rows: data.by_channel, label: (key) => CHANNEL_LABEL[key as Channel] ?? key } : undefined}
        />
        <SentimentCard data={data} className="md:col-span-2 xl:col-span-1" />
      </div>
    </section>
  );
}

/** "12 de 20 que empezaron", with the withdrawn ones named: they count as completed, not as aptos. */
function completionHint(totals: Analytics["totals"]): string {
  const base = `${totals.completed} de ${totals.consented} que empezaron`;
  if (totals.withdrawn === 0) return base;
  return `${base}; ${totals.withdrawn} ${totals.withdrawn === 1 ? "retirada" : "retiradas"}`;
}

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return <StatCard insight={{ kind: "stat", label, value, hint }} />;
}

function Funnel({ data, className }: { data: Analytics; className?: string }) {
  const stepLabel = useStepLabel();
  const started = data.totals.started;
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Embudo de la criba</CardTitle>
        <CardDescription>Conversaciones que llegaron a cada paso, y su parte del total iniciado.</CardDescription>
      </CardHeader>
      <CardContent>
        <BarList
          label="Embudo de la criba"
          max={started}
          items={data.funnel.map((step) => ({
            key: step.stage,
            label: stepLabel(step.stage),
            value: step.count,
            display: `${step.count} · ${formatPercent(started ? step.count / started : null)}`,
          }))}
        />
      </CardContent>
    </Card>
  );
}

function DropOff({ data }: { data: Analytics }) {
  const stepLabel = useStepLabel();
  const abandoned = data.totals.abandoned;
  const top = data.top_drop_off_stage;
  const topCount = data.drop_off.find((item) => item.stage === top)?.count ?? 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Dónde abandonan</CardTitle>
        <CardDescription>
          {dropOffSummary(abandoned, top ? stepLabel(top) : null, topCount)}
        </CardDescription>
      </CardHeader>
      {abandoned > 0 && (
        <CardContent>
          <BarList
            label="Abandonos por paso"
            emphasize
            items={data.drop_off.map((item) => ({
              key: item.stage,
              label: stepLabel(item.stage),
              value: item.count,
              emphasis: item.stage === top,
            }))}
          />
        </CardContent>
      )}
    </Card>
  );
}

function dropOffSummary(abandoned: number, top: string | null, topCount: number): string {
  if (abandoned === 0 || top === null) return "Nadie ha abandonado en este periodo.";
  if (abandoned === 1) return `El único abandono fue en ${top.toLowerCase()}.`;
  if (topCount === abandoned) return `Los ${abandoned} abandonos fueron en ${top.toLowerCase()}.`;
  return `${top} concentra ${topCount} de los ${abandoned} abandonos.`;
}

function Reasons({ data }: { data: Analytics }) {
  const disqualified = data.totals.disqualified;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Motivos de no apto</CardTitle>
        <CardDescription>
          {disqualified === 0
            ? "Ningún candidato descartado en este periodo."
            : disqualified === 1
              ? "1 candidato no cumplía un requisito."
              : `${disqualified} candidatos no cumplían un requisito.`}
        </CardDescription>
      </CardHeader>
      {disqualified > 0 && (
        <CardContent>
          <BarList
            label="Motivos de no apto"
            items={data.disqualification_reasons.map((item) => ({
              key: item.reason,
              label: REASON_LABEL[item.reason],
              value: item.count,
            }))}
          />
        </CardContent>
      )}
    </Card>
  );
}

function Meter({ share }: { share: number | null }) {
  return (
    <span className="flex items-center justify-end gap-2">
      <span aria-hidden className="hidden h-1.5 w-12 overflow-hidden rounded-full bg-muted sm:block">
        <span className="block h-full rounded-full bg-chart-1" style={{ width: `${(share ?? 0) * 100}%` }} />
      </span>
      <span className="w-9 text-right tabular-nums">{formatPercent(share)}</span>
    </span>
  );
}

function SplitRows({ rows, label }: { rows: Breakdown[]; label: (key: string) => string }) {
  return rows.map((row) => (
    <tr key={row.key} className="border-t first:border-t-0">
      <th scope="row" className="py-2 pr-2 text-left font-medium">
        {label(row.key)}
      </th>
      <td className="py-2 text-right tabular-nums text-muted-foreground">{row.started}</td>
      <td className="py-2 pl-2">
        <Meter share={row.completion_rate} />
      </td>
      <td className="py-2 pl-2">
        <Meter share={row.qualified_rate} />
      </td>
    </tr>
  ));
}

function Split({
  title,
  rows,
  label,
  extra,
}: {
  title: string;
  rows: Breakdown[];
  label: (key: string) => string;
  extra?: { rows: Breakdown[]; label: (key: string) => string };
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>Tasas sobre quienes aceptaron la privacidad.</CardDescription>
      </CardHeader>
      <CardContent>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted-foreground">
              <th scope="col" className="pb-1 text-left font-normal">
                <span className="sr-only">Grupo</span>
              </th>
              <th scope="col" className="pb-1 text-right font-normal">
                Conv.
              </th>
              <th scope="col" className="pb-1 text-right font-normal">
                Completan
              </th>
              <th scope="col" className="pb-1 text-right font-normal">
                Aptos
              </th>
            </tr>
          </thead>
          <tbody>
            <SplitRows rows={rows} label={label} />
            {extra && <SplitRows rows={extra.rows} label={extra.label} />}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

function SentimentCard({ data, className }: { data: Analytics; className?: string }) {
  const counts = new Map(data.sentiment.map((item) => [item.sentiment, item.count]));
  const total = data.sentiment.reduce((sum, item) => sum + item.count, 0);
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Ánimo de los candidatos</CardTitle>
        <CardDescription>El ánimo más negativo de cada conversación, en los mensajes escritos (los botones no cuentan).</CardDescription>
      </CardHeader>
      <CardContent>
        <BarList
          label="Ánimo de los candidatos"
          items={SENTIMENT_ORDER.map((sentiment) => {
            const count = counts.get(sentiment) ?? 0;
            return {
              key: sentiment,
              label: SENTIMENT_LABEL[sentiment],
              value: count,
              display: `${count} · ${formatPercent(total ? count / total : null)}`,
              icon: SENTIMENT_ICON[sentiment],
              iconClassName: SENTIMENT_CLASS[sentiment],
            };
          })}
        />
      </CardContent>
    </Card>
  );
}

// --- chatbot QA ---

function BotQuality({ data, filters }: { data: Analytics; filters: AnalyticsFilters }) {
  const { quality, totals } = data;
  const usage = quality.usage;
  return (
    <section
      aria-labelledby="bot-heading"
      className="flex flex-col gap-4 rounded-2xl border border-dashed border-qa/40 bg-qa/5 p-3 sm:p-4"
    >
      <div className="flex items-start gap-2.5">
        <BotIcon className="mt-0.5 size-5 shrink-0 text-qa" aria-hidden />
        <div className="flex flex-col gap-0.5">
          <h2 id="bot-heading" className="font-heading text-lg font-medium text-qa">
            Calidad del bot
          </h2>
          <p className="text-sm text-muted-foreground">QA interno para mejorar a Ana. Ninguna de estas señales es un dato del candidato.</p>
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Señales de calidad</CardTitle>
            <CardDescription>
              {quality.flagged === 0
                ? "Ninguna conversación marcada en este periodo."
                : `${quality.flagged} de ${totals.started} conversaciones (${formatPercent(quality.flagged_rate)}) tienen alguna.`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <BarList
              label="Señales de calidad"
              tone="qa"
              items={quality.flags.map((item) => ({ key: item.flag, label: QUALITY_FLAG_LABEL[item.flag], value: item.count }))}
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Coste y latencia del modelo</CardTitle>
            <CardDescription>
              {usage.model_calls === 0
                ? "Sin llamadas a un modelo real en este periodo."
                : `${usage.model_calls.toLocaleString("es-ES")} llamadas medidas en ${usage.exchanges.toLocaleString("es-ES")} mensajes.`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {usage.model_calls === 0 ? (
              <p className="rounded-lg bg-muted/60 p-3 text-sm text-muted-foreground">
                Las conversaciones de este periodo usaron el modelo local de pruebas, que no tiene coste ni latencia. Las cifras aparecen en cuanto Ana
                atiende candidatos con el modelo real.
              </p>
            ) : (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
                <Figure label="Por criba completada" value={formatUsd(usage.cost_per_completed_usd)} />
                <Figure label="Por conversación" value={formatUsd(usage.cost_per_conversation_usd)} />
                <Figure label="Coste total" value={formatUsd(usage.total_cost_usd)} />
                <Figure label="Latencia media" value={formatLatency(usage.avg_latency_ms)} hint="por mensaje" />
                <Figure label="Latencia p95" value={formatLatency(usage.p95_latency_ms)} hint="por mensaje" />
                <Figure label="Respuestas de plantilla" value={formatPercent(usage.fallback_rate)} hint="el modelo falló" />
              </dl>
            )}
          </CardContent>
        </Card>
        <FaqMisses filters={filters} />
      </div>
    </section>
  );
}

function Figure({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-heading text-xl font-normal">{value}</dd>
      {hint && <dd className="text-xs text-muted-foreground">{hint}</dd>}
    </div>
  );
}

// --- states ---

function LoadingState() {
  return (
    <div aria-busy role="status" aria-label="Cargando analítica" className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} className="h-24 rounded-xl" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-5">
        <Skeleton className="h-96 rounded-xl lg:col-span-3" />
        <Skeleton className="h-96 rounded-xl lg:col-span-2" />
      </div>
    </div>
  );
}

/** `scoped`: a recruiter's analytics count only the candidates at their locations. */
function EmptyState({ scoped }: { scoped: boolean }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-14 text-center">
      <p className="font-heading text-lg font-medium">
        {scoped ? "Sin conversaciones en tus locales en este periodo" : "Sin conversaciones en este periodo"}
      </p>
      <p className="max-w-sm text-sm text-muted-foreground">Prueba con un periodo más largo o quita el filtro de país o idioma.</p>
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-6 py-12 text-center">
      <p className="font-medium">No se pudo cargar la analítica.</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCwIcon data-icon="inline-start" />
        Reintentar
      </Button>
    </div>
  );
}
