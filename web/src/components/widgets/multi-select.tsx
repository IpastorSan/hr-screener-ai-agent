"use client";

import { useState } from "react";
import { CheckIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "@/lib/types";
import { chipClass } from "./chip";
import { nextSelection } from "./selection";
import { type WidgetCopy, widgetCopy } from "./copy";

function hint(copy: WidgetCopy, min: number, max: number | undefined, count: number) {
  if (max !== undefined && count >= max) return copy.max(max);
  if (count < min) return min === 1 ? copy.atLeastOne : copy.atLeast(min);
  return copy.chosen(count);
}

export function MultiSelect({ ui, onAction, disabled, locale }: WidgetProps<"multi_select">) {
  const copy = widgetCopy(locale);
  const min = ui.min ?? 1;
  const max = ui.max;
  const [value, setValue] = useState<string[]>([]);
  const [sent, setSent] = useState(false);
  const locked = disabled || sent;
  const valid = value.length >= min && (max === undefined || value.length <= max);

  return (
    <div className="flex flex-col gap-3">
      <ToggleGroup
        multiple
        value={value}
        onValueChange={(next: string[]) => {
          const picked = nextSelection(value, next, ui.exclusive);
          if (max === undefined || picked.length <= max) setValue(picked);
        }}
        disabled={locked}
        aria-label={copy.options}
        className="w-full flex-wrap"
      >
        {ui.options.map((option) => (
          <ToggleGroupItem key={option.value} value={option.value} className={cn(chipClass, "gap-1.5")}>
            {value.includes(option.value) && <CheckIcon data-icon="inline-start" />}
            {option.label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <div className="flex items-center gap-3">
        <Button
          size="lg"
          disabled={locked || !valid}
          onClick={() => {
            setSent(true);
            onAction({ kind: "multi_select", value });
          }}
        >
          {copy.continue}
        </Button>
        <span className="text-sm text-muted-foreground" aria-live="polite">
          {hint(copy, min, max, value.length)}
        </span>
      </div>
    </div>
  );
}
