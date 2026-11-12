"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ArrowDownIcon, ArrowUpIcon, ChevronLeftIcon, ChevronRightIcon, InboxIcon, RefreshCwIcon } from "lucide-react";
import { useCallback, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { listCandidates, SCREENING_STATUSES } from "@/lib/recruiter-api";
import type {
  AgentSpec,
  CandidateFilters,
  CandidateItem,
  CandidateQuery,
  FieldFilters,
  ScreeningStatus,
  SpecColumn,
  SpecField,
} from "@/lib/recruiter-types";
import { cn } from "@/lib/utils";
import { formatValue, useAgentSpec } from "./agent-spec";
import { CompanionBadge, LanguageChannel, ModeBadge, QualityFlagChip, ReviewLabel, ScreeningBadge, SentimentIcon } from "./badges";
import { valueOf } from "./field-values";
import { FilterBar } from "./filters";
import {
  COUNTRY_LABEL,
  formatDateTime,
  formatRelative,
  INTERVIEW_LABEL,
  interviewerLabel,
  SCREENING_TAB_LABEL,
  TALENT_POOL_LABEL,
} from "./labels";
import { formatLocalShort } from "./local-time";
import { useSession } from "./session";
import { StaleNotice } from "./stale-notice";
import { usePolling } from "./use-polling";

const PAGE_SIZE = 20;
const DEFAULT_SORT = "-last_activity";
const FILTER_KEYS = [
  "status",
  "review_status",
  "location_id",
  "country",
  "talent_pool",
  "quality_flag",
  "q",
  "recruiter_id",
] as const satisfies readonly (keyof CandidateFilters)[];
const SORT_FIELDS = ["last_activity", "created_at", "updated_at", "name"];
const TABS = ["all", ...SCREENING_STATUSES] as const;

type Query = Required<Pick<CandidateQuery, "sort" | "page">> & CandidateFilters & { fields: FieldFilters };

function readQuery(params: URLSearchParams, spec: AgentSpec): Query {
  const filters: CandidateFilters = {};
  const fields: FieldFilters = {};
  // The API validates every value; an unknown one comes back as a 422, never as markup.
  for (const key of FILTER_KEYS) {
    const value = params.get(key);
    if (value) (filters as Record<string, string>)[key] = value;
  }
  for (const { param } of spec.filters) {
    const value = params.get(param);
    if (value) fields[param] = value;
  }
  const sortable = [...SORT_FIELDS, ...spec.columns.flatMap((column) => (column.sort ? [column.sort] : []))];
  const sort = params.get("sort");
  const page = Number(params.get("page"));
  return {
    ...filters,
    fields,
    sort: sort && sortable.includes(sort.replace(/^-/, "")) ? sort : DEFAULT_SORT,
    page: Number.isInteger(page) && page > 0 ? page : 1,
  };
}

/** The candidate list, its columns and filters from the agent spec: nothing shows until it loads. */
export function Pipeline() {
  const spec = useAgentSpec();
  return spec ? <SpecPipeline spec={spec} /> : <LoadingState />;
}

function SpecPipeline({ spec }: { spec: AgentSpec }) {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { me } = useSession();
  const query = useMemo(() => readQuery(new URLSearchParams(params.toString()), spec), [params, spec]);
  const { sort, page, status, fields, ...rest } = query;
  const filters: CandidateFilters = rest;
  const columns = useMemo(
    () => spec.columns.map((column) => ({ column, field: spec.fields.find((f) => f.id === column.field)! })),
    [spec],
  );

  const listKey = JSON.stringify(query);
  const list = usePolling(listKey, (signal) => listCandidates({ sort, page, status, ...filters, page_size: PAGE_SIZE }, fields, { signal }));
  const counts = list.data?.counts;

  const navigate = useCallback(
    (patch: Partial<Record<string, string | number>>) => {
      const next = new URLSearchParams(params.toString());
      for (const [key, value] of Object.entries(patch)) {
        if (value === undefined || value === "" || (key === "sort" && value === DEFAULT_SORT) || (key === "page" && value === 1)) {
          next.delete(key);
        } else {
          next.set(key, String(value));
        }
      }
      if (!("page" in patch)) next.delete("page");
      const text = next.toString();
      router.replace(text ? `${pathname}?${text}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  const toggleSort = (field: string) => {
    const descending = `-${field}`;
    navigate({ sort: sort === descending ? field : descending });
  };

  const data = list.data;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const showQa = Boolean(filters.quality_flag);

  return (
    <div className="flex flex-col gap-4">
      <Tabs value={status ?? "all"} onValueChange={(value) => navigate({ status: value === "all" ? undefined : (value as ScreeningStatus) })}>
        <div className="-mx-4 -my-1 overflow-x-auto overflow-y-hidden px-4 py-1 sm:mx-0 sm:px-0">
          <TabsList>
            {TABS.map((tab) => (
              <TabsTrigger key={tab} value={tab} className="px-2.5">
                {SCREENING_TAB_LABEL[tab]}
                <span className="rounded-full bg-foreground/5 px-1.5 text-xs text-muted-foreground tabular-nums">
                  {counts ? counts[tab] : "·"}
                </span>
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>

      <FilterBar
        filters={filters}
        fields={fields}
        specFilters={spec.filters}
        onChange={navigate}
        onClear={() =>
          navigate({
            q: undefined,
            country: undefined,
            location_id: undefined,
            talent_pool: undefined,
            ...Object.fromEntries(spec.filters.map(({ param }) => [param, undefined])),
            review_status: undefined,
            recruiter_id: undefined,
          })
        }
      />

      <StaleNotice stale={list.stale} onRetry={list.refresh} />
      {list.error && !data ? (
        <ErrorState onRetry={list.refresh} />
      ) : !data ? (
        <LoadingState />
      ) : data.items.length === 0 ? (
        <EmptyState
          filtered={Object.keys(filters).length > 0 || Object.keys(fields).length > 0 || Boolean(status)}
          scoped={!me.is_admin}
        />
      ) : (
        <div className={cn("flex flex-col gap-3 transition-opacity", list.pending && "opacity-60")} aria-busy={list.pending}>
          <Card className="hidden py-0 md:flex">
            <Table className="text-[0.8rem]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <SortableHead field="name" sort={sort} onSort={toggleSort} className="pl-4">
                    Candidato
                  </SortableHead>
                  <TableHead>Ubicación</TableHead>
                  <TableHead>Criba</TableHead>
                  <TableHead>Revisión</TableHead>
                  {columns.map(({ column }) =>
                    column.sort ? (
                      <SortableHead key={column.field} field={column.sort} sort={sort} onSort={toggleSort}>
                        {column.label}
                      </SortableHead>
                    ) : (
                      <TableHead key={column.field}>{column.label}</TableHead>
                    ),
                  )}
                  <TableHead className="text-center">Ánimo</TableHead>
                  <TableHead>Idioma</TableHead>
                  <SortableHead field="last_activity" sort={sort} onSort={toggleSort} className="pr-4">
                    Actividad
                  </SortableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((item) => (
                  <CandidateRow
                    key={item.id}
                    item={item}
                    columns={columns}
                    showQa={showQa}
                    onOpen={() => router.push(`/recruiters/${item.id}`)}
                  />
                ))}
              </TableBody>
            </Table>
          </Card>

          <ul className="flex flex-col gap-2 md:hidden">
            {data.items.map((item) => (
              <li key={item.id}>
                <CandidateCard item={item} columns={columns} showQa={showQa} />
              </li>
            ))}
          </ul>

          <div className="flex items-center justify-between gap-3 text-sm text-muted-foreground">
            <span className="tabular-nums">
              {(data.page - 1) * PAGE_SIZE + 1}–{Math.min(data.page * PAGE_SIZE, data.total)} de {data.total}
            </span>
            <div className="flex items-center gap-1">
              <Button variant="outline" size="icon-sm" disabled={page <= 1} onClick={() => navigate({ page: page - 1 })} aria-label="Página anterior">
                <ChevronLeftIcon />
              </Button>
              <span className="px-2 tabular-nums">
                {page} / {totalPages}
              </span>
              <Button
                variant="outline"
                size="icon-sm"
                disabled={page >= totalPages}
                onClick={() => navigate({ page: page + 1 })}
                aria-label="Página siguiente"
              >
                <ChevronRightIcon />
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SortableHead({
  field,
  sort,
  onSort,
  className,
  children,
}: {
  field: string;
  sort: string;
  onSort: (field: string) => void;
  className?: string;
  children: React.ReactNode;
}) {
  const active = sort.replace(/^-/, "") === field;
  const descending = sort.startsWith("-");
  return (
    <TableHead className={className} aria-sort={active ? (descending ? "descending" : "ascending") : "none"}>
      <button
        type="button"
        onClick={() => onSort(field)}
        className="-mx-1 inline-flex items-center gap-1 rounded px-1 uppercase hover:underline"
      >
        {children}
        {active && (descending ? <ArrowDownIcon className="size-3" /> : <ArrowUpIcon className="size-3" />)}
      </button>
    </TableHead>
  );
}

type Column = { column: SpecColumn; field: SpecField };

/** A date with its late-start badge when the start is far off. */
function DateValue({ item, field }: { item: CandidateItem; field: SpecField }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      {formatValue(field, item)}
      <CompanionBadge field={field} values={item} />
    </span>
  );
}

/** A column's cell, by the field's type. */
function ColumnCell({ item, column: { column, field } }: { item: CandidateItem; column: Column }) {
  const numeric = field.type === "years" || field.type === "date";
  return (
    <TableCell className={cn(column.wrap && "max-w-36 whitespace-normal", numeric && "tabular-nums")}>
      {field.type === "date" ? <DateValue item={item} field={field} /> : formatValue(field, item)}
    </TableCell>
  );
}

function Location({ item }: { item: CandidateItem }) {
  const name = item.location_name?.replace(/^Sazón /, "");
  if (!name && !item.talent_pool) return <span className="text-muted-foreground">—</span>;
  return (
    <div className="min-w-0">
      <div className={cn("truncate", !name && "text-muted-foreground")}>{name ?? TALENT_POOL_LABEL}</div>
      {item.country && <div className="text-xs text-muted-foreground">{COUNTRY_LABEL[item.country]}</div>}
    </div>
  );
}

function CandidateRow({ item, columns, showQa, onOpen }: { item: CandidateItem; columns: Column[]; showQa: boolean; onOpen: () => void }) {
  return (
    <TableRow className="cursor-pointer" onClick={onOpen}>
      <TableCell className="max-w-56 pl-4">
        <Link
          href={`/recruiters/${item.id}`}
          className="block truncate text-sm font-bold outline-none hover:underline focus-visible:underline"
          onClick={(event) => event.stopPropagation()}
        >
          {item.name ?? "Sin nombre"}
        </Link>
        <div className="truncate text-xs text-muted-foreground">{item.phone ?? item.email ?? "—"}</div>
        {showQa && item.quality_flags.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {item.quality_flags.map((flag) => (
              <QualityFlagChip key={flag} flag={flag} />
            ))}
          </div>
        )}
      </TableCell>
      <TableCell className="max-w-40">
        <Location item={item} />
      </TableCell>
      <TableCell>
        <ScreeningBadge status={item.status} interview={Boolean(item.interview_at)} />
        {item.interview_at && interviewerLabel(item) && (
          <div className="mt-1 max-w-32 truncate text-xs text-muted-foreground" title={`Entrevista con ${interviewerLabel(item)}`}>
            con {interviewerLabel(item)}
          </div>
        )}
        {item.interview_at && item.interview_mode && (
          <div className="mt-1">
            <ModeBadge mode={item.interview_mode} />
          </div>
        )}
      </TableCell>
      <TableCell>
        <ReviewLabel status={item.review_status} />
      </TableCell>
      {columns.map((column) => (
        <ColumnCell key={column.column.field} item={item} column={column} />
      ))}
      <TableCell className="text-center">
        <SentimentIcon sentiment={item.sentiment} />
      </TableCell>
      <TableCell>
        <LanguageChannel language={item.language} channel={item.channel} />
      </TableCell>
      <TableCell className="pr-4 whitespace-nowrap text-muted-foreground" title={formatDateTime(item.last_activity_at)}>
        {formatRelative(item.last_activity_at)}
      </TableCell>
    </TableRow>
  );
}

function CandidateCard({ item, columns, showQa }: { item: CandidateItem; columns: Column[]; showQa: boolean }) {
  const details = columns
    .filter(({ field }) => field.type !== "date")
    .map(({ field }) => formatValue(field, item))
    .filter((value) => value !== "—");
  const dates = columns.filter(({ field }) => field.type === "date" && valueOf(field, item) != null);
  return (
    <Link
      href={`/recruiters/${item.id}`}
      className="flex flex-col gap-2 rounded-xl bg-card p-3 text-sm ring-1 ring-foreground/10 outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium">{item.name ?? "Sin nombre"}</div>
          <div className="truncate text-xs text-muted-foreground">
            {item.location_name ?? (item.talent_pool ? TALENT_POOL_LABEL : "Sin ubicación")}
            {item.country && ` · ${item.country}`}
          </div>
        </div>
        <ScreeningBadge status={item.status} interview={Boolean(item.interview_at)} />
      </div>
      {details.length > 0 && <div className="text-xs">{details.join(" · ")}</div>}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <ReviewLabel status={item.review_status} />
        {item.interview_at && interviewerLabel(item) && <span>con {interviewerLabel(item)}</span>}
        {item.interview_at && item.interview_mode && <ModeBadge mode={item.interview_mode} />}
        {item.interview_local_start && (
          <span className="tabular-nums" data-testid="card-interview" title="Hora local del local">
            {INTERVIEW_LABEL} {formatLocalShort(item.interview_local_start)}
          </span>
        )}
        {dates.map(({ column, field }) => (
          <span key={column.field} className="inline-flex items-center gap-1 tabular-nums" data-testid={`card-${column.field}`}>
            {column.label} <DateValue item={item} field={field} />
          </span>
        ))}
        <SentimentIcon sentiment={item.sentiment} />
        <LanguageChannel language={item.language} channel={item.channel} />
        <span className="ml-auto" title={formatDateTime(item.last_activity_at)}>
          {formatRelative(item.last_activity_at)}
        </span>
      </div>
      {showQa && item.quality_flags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {item.quality_flags.map((flag) => (
            <QualityFlagChip key={flag} flag={flag} />
          ))}
        </div>
      )}
    </Link>
  );
}

function LoadingState() {
  return (
    <div aria-busy className="flex flex-col gap-2" role="status" aria-label="Cargando candidatos">
      <Card className="hidden gap-0 py-0 md:flex">
        <div className="h-10 border-b bg-muted/30" />
        {Array.from({ length: 8 }, (_, index) => (
          <div key={index} className="flex items-center gap-6 border-b px-4 py-3 last:border-0">
            <div className="flex w-44 flex-col gap-1.5">
              <Skeleton className="h-3.5 w-32" />
              <Skeleton className="h-3 w-24" />
            </div>
            <Skeleton className="h-3.5 w-24" />
            <Skeleton className="h-5 w-16 rounded-full" />
            <Skeleton className="h-3.5 w-20" />
            <Skeleton className="h-3.5 flex-1" />
            <Skeleton className="h-3.5 w-16" />
          </div>
        ))}
      </Card>
      {Array.from({ length: 5 }, (_, index) => (
        <Skeleton key={index} className="h-24 rounded-xl md:hidden" />
      ))}
    </div>
  );
}

/** `scoped`: a recruiter sees only the candidates at their locations. */
function EmptyState({ filtered, scoped }: { filtered: boolean; scoped: boolean }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-14 text-center">
      <InboxIcon className="size-8 text-muted-foreground" aria-hidden />
      <p className="font-heading text-lg font-medium">
        {filtered ? "Ningún candidato coincide" : scoped ? "No hay candidatos en tus locales" : "Todavía no hay candidatos"}
      </p>
      <p className="max-w-sm text-sm text-muted-foreground">
        {filtered
          ? "Prueba con otros filtros o borra la búsqueda."
          : `Cuando alguien${scoped ? " de tus locales" : ""} empiece la entrevista con Ana aparecerá aquí, con sus datos y la conversación.`}
      </p>
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-6 py-12 text-center">
      <p className="font-medium">No se pudieron cargar los candidatos.</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCwIcon data-icon="inline-start" />
        Reintentar
      </Button>
    </div>
  );
}
