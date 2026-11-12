"use client";

import { useMemo, useState } from "react";
import { CalendarCheckIcon, MapPinIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import type { Slot, WidgetProps } from "@/lib/types";
import { widgetCopy } from "./copy";

/** "martes, 20 de octubre": the day of an ISO time in the location's own time zone. */
export function slotDay(startsAt: string, intl: string) {
  const format = new Intl.DateTimeFormat(intl, { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
  return format.format(new Date(`${startsAt.slice(0, 10)}T12:00:00Z`));
}

/** Local wall-clock time: `startsAt` carries the location's offset, so read it as written. */
function slotTime(slot: Slot) {
  return slot.time ?? slot.startsAt.slice(11, 16);
}

export function SlotPicker({ ui, onAction, disabled, locale }: WidgetProps<"slots">) {
  const copy = widgetCopy(locale);
  const [selected, setSelected] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const locked = disabled || sent;

  const days = useMemo(() => {
    const groups = new Map<string, Slot[]>();
    for (const slot of ui.slots) {
      const key = slot.startsAt.slice(0, 10);
      groups.set(key, [...(groups.get(key) ?? []), slot]);
    }
    return [...groups.entries()].map(([key, slots]) => ({ key, label: slotDay(key, copy.intl), slots }));
  }, [ui.slots, copy.intl]);

  const locations = [...new Set(ui.slots.map((slot) => slot.location))];
  const oneLocation = locations.length === 1 ? locations[0] : null;

  return (
    <div className="flex w-full flex-col gap-2.5">
      {oneLocation && (
        <p className="inline-flex items-center gap-1.5 text-sm font-bold text-muted-foreground">
          <MapPinIcon aria-hidden className="size-4 shrink-0 text-primary" />
          {oneLocation}
        </p>
      )}
      <ToggleGroup
        value={selected ? [selected] : []}
        onValueChange={(next: string[]) => setSelected(next[0] ?? null)}
        disabled={locked}
        orientation="vertical"
        spacing={0}
        aria-label={copy.slots}
        className="w-full gap-0 overflow-hidden rounded-[14px] border bg-card"
      >
        {days.map(({ key, label, slots }, index) => (
          <div key={key} className={cn("flex flex-col", index > 0 && "border-t")}>
            <p className="bg-secondary px-3.5 pt-2.5 pb-1 text-xs font-bold tracking-wider text-muted-foreground uppercase first-letter:uppercase">
              {label}
            </p>
            {slots.map((slot) => (
              <ToggleGroupItem
                key={slot.id}
                value={slot.id}
                aria-label={`${slot.label} · ${slot.location}`}
                className="group/slot h-auto w-full justify-start gap-2.5 rounded-none border-t bg-card px-3.5 py-2.5 text-left font-normal whitespace-normal hover:bg-accent aria-pressed:bg-accent data-[pressed]:bg-accent aria-pressed:disabled:opacity-100 data-[pressed]:data-[disabled]:opacity-100"
              >
                <span
                  aria-hidden
                  className="size-[18px] shrink-0 rounded-full border-[1.5px] border-input bg-card transition-[border] group-aria-pressed/slot:border-[6px] group-aria-pressed/slot:border-primary group-data-[pressed]/slot:border-[6px] group-data-[pressed]/slot:border-primary"
                />
                <span className="font-heading text-lg leading-tight tabular-nums">{slotTime(slot)}</span>
                {!oneLocation && (
                  <span className="ml-auto inline-flex min-w-0 items-center gap-1 text-[0.8125rem] text-muted-foreground">
                    <MapPinIcon aria-hidden className="size-3 shrink-0" />
                    <span className="truncate">{slot.location}</span>
                  </span>
                )}
              </ToggleGroupItem>
            ))}
          </div>
        ))}
      </ToggleGroup>
      <Button
        size="lg"
        className="h-11 self-start px-5 text-[0.9375rem]"
        disabled={locked || !selected}
        onClick={() => {
          if (!selected) return;
          setSent(true);
          onAction({ kind: "slots", value: selected });
        }}
      >
        <CalendarCheckIcon data-icon="inline-start" />
        {copy.book}
      </Button>
    </div>
  );
}
