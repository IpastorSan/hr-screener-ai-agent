"use client";

import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { COUNTRY_LABEL, LANGUAGE_LABEL } from "@/components/recruiters/labels";
import { cn } from "@/lib/utils";
import { RANGES, toFilters, type DashboardQuery, type Range } from "./query";

const ALL = "all";

function Choice<T extends string>({
  label,
  allLabel,
  labels,
  value,
  onChange,
}: {
  label: string;
  allLabel: string;
  labels: Record<T, string>;
  value: T | undefined;
  onChange: (value: T | undefined) => void;
}) {
  const items = { [ALL]: allLabel, ...labels } as Record<string, string>;
  return (
    <Select
      items={items}
      value={value ?? ALL}
      onValueChange={(next) => onChange(next === ALL || next === null ? undefined : (next as T))}
    >
      <SelectTrigger aria-label={label} className={cn("w-full min-w-0 bg-card sm:w-auto", value && "border-ring/60")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false} align="start">
        {Object.entries(items).map(([itemValue, itemLabel]) => (
          <SelectItem key={itemValue} value={itemValue}>
            {itemLabel}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function DashboardFilters({
  query,
  onChange,
}: {
  query: DashboardQuery;
  onChange: (patch: Partial<DashboardQuery>) => void;
}) {
  const shown = toFilters(query);
  const setDay = (key: "from" | "to") => (event: React.ChangeEvent<HTMLInputElement>) =>
    onChange({ range: "custom", from: shown.from, to: shown.to, [key]: event.target.value || undefined });

  return (
    <div className="flex flex-col gap-2 lg:flex-row lg:flex-wrap lg:items-center">
      <ToggleGroup
        aria-label="Periodo"
        variant="outline"
        size="sm"
        spacing={0}
        value={query.range === "custom" ? [] : [query.range]}
        onValueChange={(value: string[]) => {
          const next = value[0] as Range | undefined;
          if (next) onChange({ range: next, from: undefined, to: undefined });
        }}
        className="bg-card"
      >
        {Object.entries(RANGES).map(([value, label]) => (
          <ToggleGroupItem key={value} value={value} className="px-3 data-[pressed]:bg-accent data-[pressed]:font-semibold data-[pressed]:text-accent-foreground">
            {label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <div className="grid grid-cols-2 gap-2 sm:flex sm:items-center">
        <label className="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground sm:flex-row sm:items-center sm:gap-2 sm:text-sm">
          <span className="shrink-0">Desde</span>
          <Input
            type="date"
            value={shown.from ?? ""}
            max={shown.to}
            onChange={setDay("from")}
            className={cn("h-8 bg-card tabular-nums", query.range === "custom" && "border-ring/60")}
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground sm:flex-row sm:items-center sm:gap-2 sm:text-sm">
          <span className="shrink-0">Hasta</span>
          <Input
            type="date"
            value={shown.to ?? ""}
            min={shown.from}
            onChange={setDay("to")}
            className={cn("h-8 bg-card tabular-nums", query.range === "custom" && "border-ring/60")}
          />
        </label>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:flex">
        <Choice label="País" allLabel="Todos los países" labels={COUNTRY_LABEL} value={query.country} onChange={(country) => onChange({ country })} />
        <Choice
          label="Idioma"
          allLabel="Todos los idiomas"
          labels={LANGUAGE_LABEL}
          value={query.language}
          onChange={(language) => onChange({ language })}
        />
      </div>
    </div>
  );
}
