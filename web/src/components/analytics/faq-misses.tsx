"use client";

import Link from "next/link";
import { DownloadIcon, RefreshCwIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { COUNTRY_LABEL, LANGUAGE_LABEL, formatRelative } from "@/components/recruiters/labels";
import { usePolling } from "@/components/recruiters/use-polling";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { downloadFaqMisses, getFaqMisses } from "@/lib/recruiter-api";
import type { AnalyticsFilters, FaqMissGroup } from "@/lib/recruiter-types";
import { cn } from "@/lib/utils";
import { faqMissesSummary } from "./format";

/** The questions Ana could not answer, grouped by meaning, the most asked first. */
export function FaqMisses({ filters }: { filters: AnalyticsFilters }) {
  const result = usePolling(JSON.stringify(filters), (signal) => getFaqMisses(filters, { signal }), 60_000);
  const [exporting, setExporting] = useState(false);
  const data = result.data;

  const exportCsv = async () => {
    setExporting(true);
    try {
      await downloadFaqMisses(filters);
    } catch {
      toast.error("No se pudo exportar");
    } finally {
      setExporting(false);
    }
  };

  return (
    <Card aria-labelledby="faq-misses-heading" role="region" className="lg:col-span-2">
      <CardHeader>
        <CardTitle id="faq-misses-heading">Preguntas sin respuesta</CardTitle>
        <CardDescription>
          {data ? faqMissesSummary(data.total, data.groups.length, data.truncated) : "Lo que los candidatos preguntan y Ana no sabe contestar."}
        </CardDescription>
        {data && data.total > 0 && (
          <CardAction>
            <Button variant="outline" size="sm" onClick={exportCsv} disabled={exporting}>
              <DownloadIcon data-icon="inline-start" />
              Exportar CSV
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className={cn("transition-opacity", result.pending && "opacity-60")}>
        {result.error && !data ? (
          <div className="flex flex-col items-start gap-2 rounded-lg bg-muted/60 p-3 text-sm text-muted-foreground">
            No se pudieron cargar las preguntas.
            <Button variant="outline" size="sm" onClick={result.refresh}>
              <RefreshCwIcon data-icon="inline-start" />
              Reintentar
            </Button>
          </div>
        ) : !data ? (
          <Skeleton className="h-32 rounded-lg" />
        ) : data.groups.length === 0 ? (
          <p className="rounded-lg bg-muted/60 p-3 text-sm text-muted-foreground">
            Sin preguntas pendientes. Cuando un candidato pregunte algo que el FAQ no cubre, aparecerá aquí agrupado con las preguntas parecidas.
          </p>
        ) : (
          <MissTable groups={data.groups} bySimilarity={data.by_similarity} />
        )}
      </CardContent>
    </Card>
  );
}

function MissTable({ groups, bySimilarity }: { groups: FaqMissGroup[]; bySimilarity: boolean }) {
  return (
    <div className="flex flex-col gap-2">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-0">Pregunta</TableHead>
            <TableHead className="text-right">Veces</TableHead>
            <TableHead className="hidden md:table-cell">Dónde</TableHead>
            <TableHead className="hidden sm:table-cell">Última vez</TableHead>
            <TableHead className="pr-0 text-right">Ejemplo</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {groups.map((group) => (
            <MissRow key={group.id} group={group} />
          ))}
        </TableBody>
      </Table>
      {!bySimilarity && (
        <p className="text-xs text-muted-foreground">Agrupadas por texto exacto: el modelo de búsqueda no está disponible en este servidor.</p>
      )}
    </div>
  );
}

function MissRow({ group }: { group: FaqMissGroup }) {
  const example = group.examples.find((item) => item.candidate_id !== null);
  const where = [...group.countries.map((country) => COUNTRY_LABEL[country] ?? country), ...group.languages.map((language) => LANGUAGE_LABEL[language] ?? language)];
  return (
    <TableRow>
      <TableCell className="max-w-0 min-w-48 pl-0 whitespace-normal">
        <span className="font-medium break-words">{group.question}</span>
        <span className="block text-xs text-muted-foreground md:hidden">{where.join(" · ")}</span>
      </TableCell>
      <TableCell className="text-right font-heading text-base tabular-nums">{group.count}</TableCell>
      <TableCell className="hidden text-muted-foreground md:table-cell">{where.join(" · ")}</TableCell>
      <TableCell className="hidden whitespace-nowrap text-muted-foreground sm:table-cell">{formatRelative(group.last_seen)}</TableCell>
      <TableCell className="pr-0 text-right">
        {example?.candidate_id ? (
          <Link href={`/recruiters/${example.candidate_id}`} className="text-sm font-medium whitespace-nowrap text-primary underline-offset-4 hover:underline">
            Ver candidato
          </Link>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
    </TableRow>
  );
}
