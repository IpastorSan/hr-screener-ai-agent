"use client";

import { BotIcon, SearchIcon, XIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { CandidateFilters, Country, FieldFilters, SpecFilter } from "@/lib/recruiter-types";
import { cn } from "@/lib/utils";
import { COUNTRY_LABEL, QUALITY_FLAG_LABEL, REVIEW_LABEL, TALENT_POOL_LABEL } from "./labels";
import { useLocations } from "./use-locations";
import { useRecruiters } from "./use-recruiters";

export const ALL = "all";

type Props = {
  filters: CandidateFilters;
  /** The agent's own filters (`specFilters`), by query parameter. */
  fields: FieldFilters;
  specFilters: SpecFilter[];
  onChange: (patch: Partial<Record<string, string>>) => void;
  onClear: () => void;
};

function options<K extends string>(labels: Record<K, string>, allLabel: string) {
  return { [ALL]: allLabel, ...labels } as Record<string, string>;
}

const COUNTRY_ITEMS = options(COUNTRY_LABEL, "País");
const REVIEW_ITEMS = options(REVIEW_LABEL, "Revisión");
const QUALITY_ITEMS = options(QUALITY_FLAG_LABEL, "Todas");
const TALENT_POOL_ITEMS = options({ true: "En la bolsa", false: "Fuera de la bolsa" }, TALENT_POOL_LABEL);

function FilterSelect({
  label,
  items,
  value,
  onValueChange,
  className,
  children,
}: {
  label: string;
  items: Record<string, string>;
  value: string | undefined;
  onValueChange: (value: string | undefined) => void;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <Select
      items={items}
      value={value ?? ALL}
      onValueChange={(next) => onValueChange(next === ALL || next === null ? undefined : String(next))}
    >
      <SelectTrigger aria-label={label} title={value ? items[value] : undefined} className={cn("w-full min-w-0 bg-card sm:w-auto *:data-[slot=select-value]:block! *:data-[slot=select-value]:truncate", !value && "text-muted-foreground", value && "border-primary bg-accent font-bold text-accent-foreground", className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false} align="start">
        {children ??
          Object.entries(items).map(([itemValue, itemLabel]) => (
            <SelectItem key={itemValue} value={itemValue}>
              {itemValue === ALL ? "Todos" : itemLabel}
            </SelectItem>
          ))}
      </SelectContent>
    </Select>
  );
}

export function SearchBox({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [text, setText] = useState(value);
  const [synced, setSynced] = useState(value);
  if (value !== synced) {
    setSynced(value);
    setText(value);
  }
  useEffect(() => {
    if (text === value) return;
    const timer = window.setTimeout(() => onChange(text), 300);
    return () => window.clearTimeout(timer);
  }, [text, value, onChange]);
  return (
    <div className="relative w-full sm:w-64">
      <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="search"
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="Nombre, email o teléfono"
        aria-label="Buscar candidatos"
        maxLength={100}
        className="bg-card pl-8"
      />
    </div>
  );
}

/** A field's options, with the field's name as the "all" item. */
function fieldItems(filter: SpecFilter) {
  return options(Object.fromEntries(filter.options.map((option) => [option.value, option.label])), filter.label);
}

export function FilterBar({ filters, fields, specFilters, onChange, onClear }: Props) {
  const set = (key: string) => (value: string | undefined) => onChange({ [key]: value });
  const allLocations = useLocations();
  const team = useRecruiters();
  const recruiterItems = {
    [ALL]: "Reclutador",
    me: "Mis entrevistas",
    ...Object.fromEntries(team.map((recruiter) => [recruiter.id, recruiter.name])),
  };
  const locations = allLocations.filter((location) => !filters.country || location.country === filters.country);
  const locationItems = {
    [ALL]: "Ubicación",
    ...Object.fromEntries(allLocations.map((location) => [location.location_id, location.name])),
  };
  const active =
    filters.q ||
    filters.country ||
    filters.location_id ||
    filters.talent_pool ||
    Object.keys(fields).length > 0 ||
    filters.review_status ||
    filters.recruiter_id;

  return (
    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
      <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
        <div className="col-span-2 sm:col-auto">
          <SearchBox value={filters.q ?? ""} onChange={(q) => onChange({ q: q || undefined })} />
        </div>
        <FilterSelect
          label="País"
          items={COUNTRY_ITEMS}
          value={filters.country}
          onValueChange={(country) => {
            const keepLocation =
              !country || allLocations.find((location) => location.location_id === filters.location_id)?.country === country;
            onChange({ country: country as Country | undefined, ...(keepLocation ? {} : { location_id: undefined }) });
          }}
        />
        <FilterSelect label="Ubicación" items={locationItems} value={filters.location_id} onValueChange={set("location_id")}>
          <SelectItem value={ALL}>Todas</SelectItem>
          {(["ES", "MX"] as const).map((country) => {
            const group = locations.filter((location) => location.country === country);
            if (group.length === 0) return null;
            return (
              <SelectGroup key={country}>
                <SelectLabel>{COUNTRY_LABEL[country]}</SelectLabel>
                {group.map((location) => (
                  <SelectItem key={location.location_id} value={location.location_id}>
                    {location.name}
                  </SelectItem>
                ))}
              </SelectGroup>
            );
          })}
        </FilterSelect>
        <FilterSelect
          label={TALENT_POOL_LABEL}
          items={TALENT_POOL_ITEMS}
          value={filters.talent_pool}
          onValueChange={set("talent_pool")}
        />
        {specFilters.map((filter) => (
          <FilterSelect
            key={filter.param}
            label={filter.label}
            items={fieldItems(filter)}
            value={fields[filter.param]}
            onValueChange={set(filter.param)}
          />
        ))}
        <FilterSelect label="Revisión" items={REVIEW_ITEMS} value={filters.review_status} onValueChange={set("review_status")} />
        <FilterSelect label="Reclutador" items={recruiterItems} value={filters.recruiter_id} onValueChange={set("recruiter_id")}>
          <SelectItem value={ALL}>Todos</SelectItem>
          <SelectItem value="me">Mis entrevistas</SelectItem>
          {(["ES", "MX"] as const).map((country) => {
            const group = team.filter((recruiter) => recruiter.country === country);
            if (group.length === 0) return null;
            return (
              <SelectGroup key={country}>
                <SelectLabel>{COUNTRY_LABEL[country]}</SelectLabel>
                {group.map((recruiter) => (
                  <SelectItem key={recruiter.id} value={recruiter.id}>
                    {recruiter.name}
                  </SelectItem>
                ))}
              </SelectGroup>
            );
          })}
        </FilterSelect>
        {active && (
          <Button variant="ghost" size="sm" onClick={onClear} className="justify-self-start">
            <XIcon data-icon="inline-start" />
            Limpiar
          </Button>
        )}
      </div>

      <div
        className={cn(
          "flex shrink-0 flex-wrap items-center gap-2 rounded-xl border border-dashed border-qa/40 bg-qa/5 p-2 sm:flex-nowrap sm:py-1.5 sm:pr-1.5 sm:pl-3",
          filters.quality_flag && "border-solid",
        )}
      >
        <BotIcon className="size-4 shrink-0 text-qa" aria-hidden />
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="text-xs font-bold text-qa">Calidad del bot</span>
          <span className="text-[0.65rem] text-muted-foreground">QA interno, no es un dato del candidato</span>
        </div>
        <FilterSelect
          label="Calidad del bot"
          items={QUALITY_ITEMS}
          value={filters.quality_flag}
          onValueChange={set("quality_flag")}
          className="w-full sm:ml-auto sm:w-auto"
        />
      </div>
    </div>
  );
}
