"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ApiError } from "@/lib/api";
import { createRecruiter, updateRecruiter } from "@/lib/recruiter-api";
import type { Country, Recruiter, RecruiterCreate, RecruiterPatch, WorkingHours } from "@/lib/recruiter-types";
import { cn } from "@/lib/utils";
import { useAgentSpec } from "./agent-spec";
import { COUNTRY_LABEL } from "./labels";
import { formatHours, parseHours } from "./recruiter-hours";
import { useLocations } from "./use-locations";
import { invalidateRecruiters } from "./use-recruiters";

const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"] as const;
const DEFAULT_HOURS: Record<Country, string> = { ES: "09:00-14:00, 16:00-19:00", MX: "09:00-18:00" };
const TIMEZONE: Record<Country, string> = { ES: "Europe/Madrid", MX: "America/Mexico_City" };
const MIN_PASSWORD = 12;

type Props = {
  /** null to add a new recruiter. */
  recruiter: Recruiter | null;
  isSelf: boolean;
  onClose: () => void;
  onSaved: () => void;
};

function Field({ label, hint, children, className }: { label: string; hint?: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={cn("flex flex-col gap-1.5 text-sm font-medium", className)}>
      {label}
      {children}
      {hint && <span className="text-xs font-normal text-muted-foreground">{hint}</span>}
    </label>
  );
}

function errorText(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    const detail = (error.body as { detail?: string } | null)?.detail;
    return detail ?? "Ya existe un reclutador con ese email.";
  }
  if (error instanceof ApiError && error.status === 422) {
    const fields = (error.body as { fields?: { message: string }[] } | null)?.fields ?? [];
    return fields.map((field) => field.message.replace(/^Value error, /, "")).join(" ") || "Revisa los campos.";
  }
  if (error instanceof ApiError && error.status === 403) return "Solo un administrador puede hacer esto.";
  return "No se pudo guardar. Inténtalo de nuevo.";
}

/** Add or edit a recruiter: who they are, where and how they interview, and their access. */
export function RecruiterDialog({ recruiter, isSelf, onClose, onSaved }: Props) {
  const locations = useLocations();
  const spec = useAgentSpec();
  const editing = recruiter !== null;
  const [name, setName] = useState(recruiter?.name ?? "");
  const [email, setEmail] = useState(recruiter?.email ?? "");
  const [country, setCountry] = useState<Country>(recruiter?.country ?? "ES");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(recruiter?.is_admin ?? false);
  const [active, setActive] = useState(recruiter?.active ?? true);
  const [maxPerDay, setMaxPerDay] = useState(String(recruiter?.max_interviews_per_day ?? 15));
  const [hours, setHours] = useState(
    recruiter ? formatHours(recruiter.working_hours.mon).replaceAll("–", "-").replace("—", "") : DEFAULT_HOURS.ES,
  );
  const [picked, setPicked] = useState<Set<string>>(new Set(recruiter?.location_ids ?? []));
  const [scope, setScope] = useState<Record<string, Set<string>>>(() =>
    Object.fromEntries(Object.entries(recruiter?.scope ?? {}).map(([field, options]) => [field, new Set(options)])),
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const inCountry = locations.filter((location) => location.country === country);
  const scoped = (spec?.interview_scope ?? []).flatMap((id) => spec?.fields.filter((field) => field.id === id) ?? []);

  function toggle<T>(set: Set<T>, value: T, on: boolean): Set<T> {
    const next = new Set(set);
    if (on) next.add(value);
    else next.delete(value);
    return next;
  }

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const blocks = parseHours(hours);
    if (blocks === null) return setError("El horario no es válido. Escríbelo así: 09:00-14:00, 16:00-19:00.");
    if (password && password.length < MIN_PASSWORD) return setError(`La contraseña debe tener al menos ${MIN_PASSWORD} caracteres.`);
    const max = Number(maxPerDay);
    if (!Number.isInteger(max) || max < 1 || max > 40) return setError("El máximo de entrevistas al día va de 1 a 40.");
    const workingHours: WorkingHours = { ...(recruiter?.working_hours ?? {}) };
    for (const day of WEEKDAYS) workingHours[day] = blocks;
    const fields = {
      name: name.trim(),
      is_admin: isAdmin,
      interview_modes: ["video" as const],
      max_interviews_per_day: max,
      working_hours: workingHours,
      location_ids: [...picked],
      ...(scoped.length
        ? { scope: Object.fromEntries(Object.entries(scope).filter(([, options]) => options.size).map(([field, options]) => [field, [...options]])) }
        : {}),
      ...(password ? { password } : {}),
    };
    setSaving(true);
    setError(null);
    try {
      if (recruiter) {
        const patch: RecruiterPatch = { ...fields, ...(isSelf ? {} : { active }) };
        if (isSelf) delete patch.is_admin;
        await updateRecruiter(recruiter.id, patch);
      } else {
        const body: RecruiterCreate = { ...fields, email: email.trim(), country, timezone: TIMEZONE[country] };
        await createRecruiter(body);
      }
      invalidateRecruiters();
      onSaved();
    } catch (failure) {
      setError(errorText(failure));
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{recruiter ? `Editar a ${recruiter.name}` : "Nuevo reclutador"}</DialogTitle>
          <DialogDescription>
            {editing
              ? "Los cambios cuentan para las próximas reservas. Una contraseña nueva o la baja cierran sus sesiones abiertas."
              : "Las cuentas solo las crea un administrador. Sin contraseña no podrá entrar hasta que se la pongas."}
          </DialogDescription>
        </DialogHeader>

        <form id="recruiter-form" onSubmit={save} className="grid gap-4 sm:grid-cols-2">
          <Field label="Nombre">
            <Input required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} autoComplete="off" />
          </Field>
          <Field label="Email" hint={editing ? "El email no se puede cambiar." : undefined}>
            <Input
              type="email"
              required
              maxLength={254}
              value={email}
              disabled={editing}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="nombre@grupo-sazon.com"
              autoComplete="off"
            />
          </Field>
          <fieldset className="flex flex-col gap-1.5">
            <legend className="mb-1.5 text-sm font-medium">País</legend>
            <ToggleGroup
              variant="outline"
              size="sm"
              spacing={1}
              disabled={editing}
              value={[country]}
              onValueChange={(value: string[]) => {
                const option = value[0] as Country | undefined;
                if (!option) return;
                setCountry(option);
                setPicked(new Set());
                if (hours === DEFAULT_HOURS[country]) setHours(DEFAULT_HOURS[option]);
              }}
            >
              {(["ES", "MX"] as const).map((option) => (
                <ToggleGroupItem
                  key={option}
                  value={option}
                  className="px-3 data-[pressed]:bg-secondary data-[pressed]:text-secondary-foreground"
                >
                  {COUNTRY_LABEL[option]}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </fieldset>
          <Field label={editing ? "Nueva contraseña" : "Contraseña"} hint={`Opcional. Mínimo ${MIN_PASSWORD} caracteres.`}>
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              minLength={MIN_PASSWORD}
            />
          </Field>
          <Field label="Horario de lunes a viernes" hint="Hora local, por ejemplo 09:00-14:00, 16:00-19:00">
            <Input value={hours} onChange={(event) => setHours(event.target.value)} />
          </Field>
          <Field label="Máximo de videollamadas al día" hint="Las entrevistas en persona las hace la gerencia de cada local">
            <Input type="number" min={1} max={40} value={maxPerDay} onChange={(event) => setMaxPerDay(event.target.value)} />
          </Field>

          <fieldset className="flex flex-col gap-2 sm:col-span-2">
            <legend className="mb-1.5 flex w-full items-baseline justify-between text-sm font-medium">
              Ubicaciones
              <span className="text-xs font-normal text-muted-foreground tabular-nums">
                {picked.size} de {inCountry.length}
              </span>
            </legend>
            <div className="grid max-h-48 grid-cols-1 gap-x-4 gap-y-2 overflow-y-auto rounded-lg border p-3 sm:grid-cols-2">
              {inCountry.map((location) => (
                <label key={location.location_id} className="flex min-w-0 items-center gap-2 text-sm">
                  <Checkbox
                    checked={picked.has(location.location_id)}
                    onCheckedChange={(on) => setPicked((current) => toggle(current, location.location_id, Boolean(on)))}
                  />
                  <span className="truncate">{location.name.replace(/^Sazón /, "")}</span>
                </label>
              ))}
            </div>
          </fieldset>

          {scoped.map((field) => (
            <fieldset key={field.id} className="flex flex-col gap-2 sm:col-span-2">
              <legend className="mb-1.5 text-sm font-medium">{field.label}</legend>
              <p className="text-xs text-muted-foreground">Entrevista solo a quien marque alguna de estas; sin ninguna, a todos.</p>
              <div className="grid grid-cols-1 gap-x-4 gap-y-2 rounded-lg border p-3 sm:grid-cols-2">
                {field.options.map((option) => (
                  <label key={option.value} className="flex min-w-0 items-center gap-2 text-sm">
                    <Checkbox
                      checked={scope[field.id]?.has(option.value) ?? false}
                      onCheckedChange={(on) =>
                        setScope((current) => ({ ...current, [field.id]: toggle(current[field.id] ?? new Set(), option.value, Boolean(on)) }))
                      }
                    />
                    <span className="truncate">{option.label}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          ))}

          <fieldset className="flex flex-col gap-2 sm:col-span-2">
            <legend className="mb-1.5 text-sm font-medium">Acceso</legend>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={isAdmin} disabled={isSelf} onCheckedChange={(on) => setIsAdmin(Boolean(on))} />
              Administración: gestiona reclutadores
            </label>
            {editing && (
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={active} disabled={isSelf} onCheckedChange={(on) => setActive(Boolean(on))} />
                Activo: puede entrar y recibir entrevistas
              </label>
            )}
            {isSelf && <p className="text-xs text-muted-foreground">No puedes quitarte el acceso ni la administración a ti mismo.</p>}
          </fieldset>
        </form>

        <p role="alert" aria-live="polite" className={cn("text-sm text-destructive", !error && "sr-only")}>
          {error}
        </p>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" form="recruiter-form" disabled={saving || !name.trim() || (!editing && !email.trim())}>
            {saving ? "Guardando…" : editing ? "Guardar cambios" : "Crear reclutador"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
