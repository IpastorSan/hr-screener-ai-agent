import { ScreeningBadge } from "@/components/recruiters/badges";
import { formatDate, formatExperience } from "@/components/recruiters/labels";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { InsightProps } from "@/lib/types";

export function CandidateTable({ insight, onAction }: InsightProps<"candidates">) {
  const open = (candidateId: string) => onAction?.({ kind: "open_candidate", candidateId });
  return (
    <Card className="gap-2 py-3">
      {insight.title && (
        <CardHeader className="px-4">
          <CardTitle>{insight.title}</CardTitle>
        </CardHeader>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-4">Candidato</TableHead>
            <TableHead>Criba</TableHead>
            <TableHead className="hidden sm:table-cell">Experiencia</TableHead>
            <TableHead className="pr-4">Inicio</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {insight.candidates.map((row) => (
            <TableRow
              key={row.id}
              tabIndex={0}
              role="link"
              data-candidate-id={row.id}
              className="cursor-pointer focus-visible:bg-muted focus-visible:outline-none"
              onClick={() => open(row.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter") open(row.id);
              }}
            >
              <TableCell className="max-w-48 pl-4">
                <div className="truncate font-medium">{row.name}</div>
                <div className="truncate text-xs text-muted-foreground">{row.city ?? "Sin ubicación"}</div>
              </TableCell>
              <TableCell>
                <ScreeningBadge status={row.status} />
              </TableCell>
              <TableCell className="hidden tabular-nums sm:table-cell">{formatExperience(row.experience_years ?? null)}</TableCell>
              <TableCell className="pr-4 whitespace-nowrap tabular-nums">{formatDate(row.start_date ?? null)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}
