"use client";

import { useState } from "react";
import { ExternalLinkIcon, MapPinIcon, VideoIcon, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "@/lib/types";
import { chipClass } from "./chip";
import { widgetCopy } from "./copy";

/** The interview mode choice carries an icon per mode. */
const OPTION_ICON = new Map<string, LucideIcon>([
  ["onsite", MapPinIcon],
  ["video", VideoIcon],
]);

export function QuickReplies({ ui, onAction, disabled, locale }: WidgetProps<"quick_replies">) {
  const copy = widgetCopy(locale);
  const [chosen, setChosen] = useState<string | null>(null);
  const locked = disabled || chosen !== null;

  return (
    <div className="flex flex-col gap-2.5">
      {ui.link && (
        <a
          href={ui.link.href}
          target="_blank"
          rel="noopener"
          className="inline-flex items-center gap-1.5 self-start rounded-sm text-sm font-bold text-primary underline underline-offset-3 outline-none hover:decoration-2 focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          {ui.link.label}
          <ExternalLinkIcon aria-hidden className="size-3.5" />
          <span className="sr-only">{copy.newTab}</span>
        </a>
      )}
      {ui.options.length > 0 && (
      <div role="group" aria-label={copy.quickReplies} className="flex flex-wrap gap-2">
        {ui.options.map((option) => {
          const Icon = OPTION_ICON.get(option.value);
          return (
            <Button
              key={option.value}
              variant="outline"
              aria-pressed={chosen === option.value}
              disabled={locked && chosen !== option.value}
              className={cn(chipClass, locked && chosen === option.value && "pointer-events-none")}
              onClick={() => {
                if (locked) return;
                setChosen(option.value);
                onAction({ kind: "quick_replies", value: option.value });
              }}
            >
              {Icon && <Icon data-icon="inline-start" aria-hidden />}
              {option.label}
            </Button>
          );
        })}
      </div>
      )}
    </div>
  );
}
