"use client";

import { PencilIcon, PlusIcon, RefreshCwIcon, ShieldCheckIcon } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { listRecruiters } from "@/lib/recruiter-api";
import type { Recruiter } from "@/lib/recruiter-types";
import { cn } from "@/lib/utils";
import { COUNTRY_LABEL } from "./labels";
import { RecruiterDialog } from "./recruiter-form";
import { formatHours } from "./recruiter-hours";
import { useSession } from "./session";
import { StaleNotice } from "./stale-notice";
import { usePolling } from "./use-polling";

type Editing = { recruiter: Recruiter | null } | null;

function StatusBadge({ recruiter }: { recruiter: Recruiter }) {
  if (!recruiter.active) return <Badge variant="secondary">Baja</Badge>;
  if (!recruiter.has_password) return <Badge variant="outline">Sin contraseña</Badge>;
  return <Badge variant="outline" className="border-ok/30 text-ok">Activo</Badge>;
}

function CalendarBadge({ recruiter }: { recruiter: Recruiter }) {
  const status = recruiter.calendar.status;
  if (status === "connected")
    return (
      <Badge variant="outline" className="border-ok/30 text-ok">
        Conectado
      </Badge>
    );
  if (status === "revoked")
    return (
      <Badge variant="outline" className="border-warn/40 text-warn">
        Reconectar
      </Badge>
    );
  return <span className="text-muted-foreground">Sin conectar</span>;
}

/** The hiring team for admins: who interviews where, when, and who can sign in. */
export function TeamAdmin() {
  const { me } = useSession();
  const [editing, setEditing] = useState<Editing>(null);
  const list = usePolling("team", (signal) => listRecruiters({ signal }), 60_000);

  if (!me.is_admin) {
    return (
      <div className="mx-auto mt-10 flex max-w-md flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-12 text-center">
        <ShieldCheckIcon className="size-8 text-muted-foreground" aria-hidden />
        <p className="font-heading text-lg font-medium">Solo para administración</p>
        <p className="text-sm text-muted-foreground">Pide a un administrador del equipo que gestione las cuentas de reclutadores.</p>
      </div>
    );
  }

  const team = list.data ?? [];
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-2xl font-normal tracking-tight sm:text-3xl">Reclutadores</h1>
          <p className="text-sm text-muted-foreground">Quién entrevista en cada ubicación, en qué horario y quién puede entrar al panel.</p>
        </div>
        <Button onClick={() => setEditing({ recruiter: null })} className="self-start sm:self-auto">
          <PlusIcon data-icon="inline-start" />
          Nuevo reclutador
        </Button>
      </div>

      <StaleNotice stale={list.stale} onRetry={list.refresh} />
      {list.error && !list.data ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-6 py-12 text-center">
          <p className="font-medium">No se pudo cargar el equipo.</p>
          <Button variant="outline" size="sm" onClick={list.refresh}>
            <RefreshCwIcon data-icon="inline-start" />
            Reintentar
          </Button>
        </div>
      ) : !list.data ? (
        <div aria-busy role="status" aria-label="Cargando reclutadores" className="flex flex-col gap-2">
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="h-14 rounded-xl" />
          ))}
        </div>
      ) : (
        <>
          <Card className="hidden py-0 md:flex" data-testid="team-table">
            <Table className="text-[0.8rem]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-4">Reclutador</TableHead>
                  <TableHead>País</TableHead>
                  <TableHead>Ubicaciones</TableHead>
                  <TableHead>Horario (L–V)</TableHead>
                  <TableHead className="text-right">Máx./día</TableHead>
                  <TableHead>Calendario</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead className="pr-4" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {team.map((recruiter) => (
                  <TableRow key={recruiter.id} className={cn(!recruiter.active && "opacity-60")}>
                    <TableCell className="max-w-64 pl-4">
                      <div className="flex items-center gap-1.5 truncate font-medium">
                        {recruiter.name}
                        {recruiter.is_admin && <ShieldCheckIcon aria-label="Administración" className="size-3.5 shrink-0 text-primary" />}
                      </div>
                      <div className="truncate text-xs text-muted-foreground">{recruiter.email}</div>
                    </TableCell>
                    <TableCell>{COUNTRY_LABEL[recruiter.country]}</TableCell>
                    <TableCell className="tabular-nums">{recruiter.location_ids.length}</TableCell>
                    <TableCell className="tabular-nums">{formatHours(recruiter.working_hours.mon)}</TableCell>
                    <TableCell className="text-right tabular-nums">{recruiter.max_interviews_per_day}</TableCell>
                    <TableCell data-testid="calendar-status">
                      <CalendarBadge recruiter={recruiter} />
                    </TableCell>
                    <TableCell>
                      <StatusBadge recruiter={recruiter} />
                    </TableCell>
                    <TableCell className="pr-4 text-right">
                      <Button variant="ghost" size="sm" onClick={() => setEditing({ recruiter })}>
                        <PencilIcon data-icon="inline-start" />
                        Editar
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>

          <ul className="flex flex-col gap-2 md:hidden">
            {team.map((recruiter) => (
              <li key={recruiter.id}>
                <button
                  type="button"
                  onClick={() => setEditing({ recruiter })}
                  className={cn(
                    "flex w-full flex-col gap-1.5 rounded-xl bg-card p-3 text-left text-sm ring-1 ring-foreground/10 outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                    !recruiter.active && "opacity-60",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 truncate font-medium">
                        {recruiter.name}
                        {recruiter.is_admin && <ShieldCheckIcon aria-label="Administración" className="size-3.5 shrink-0 text-primary" />}
                      </div>
                      <div className="truncate text-xs text-muted-foreground">{recruiter.email}</div>
                    </div>
                    <StatusBadge recruiter={recruiter} />
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {COUNTRY_LABEL[recruiter.country]} · {recruiter.location_ids.length} ubicaciones ·{" "}
                    {formatHours(recruiter.working_hours.mon)} · Calendario: <CalendarBadge recruiter={recruiter} />
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {editing && (
        <RecruiterDialog
          recruiter={editing.recruiter}
          isSelf={editing.recruiter?.id === me.id}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            list.refresh();
          }}
        />
      )}
    </div>
  );
}
