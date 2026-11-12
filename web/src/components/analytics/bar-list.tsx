import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type BarItem = {
  key: string;
  label: string;
  value: number;
  /** Shown instead of the raw value, e.g. "45 · 75 %". */
  display?: string;
  icon?: LucideIcon;
  iconClassName?: string;
  /** Draw this bar in the accent and the rest in gray (emphasis). */
  emphasis?: boolean;
};

const TONE = {
  accent: "bg-chart-1",
  qa: "bg-qa",
} as const;

type Props = {
  items: BarItem[];
  /** The value of a full-width bar; defaults to the largest value. */
  max?: number;
  tone?: keyof typeof TONE;
  /** Gray every bar but the emphasized ones. */
  emphasize?: boolean;
  label: string;
  className?: string;
};

/**
 * Horizontal bars, one series, labeled at the end of each bar. Every value is printed, so the
 * list doubles as its own table view; the row's title repeats it on hover.
 */
export function BarList({ items, max, tone = "accent", emphasize = false, label, className }: Props) {
  const top = Math.max(1, max ?? Math.max(0, ...items.map((item) => item.value)));
  return (
    <ul aria-label={label} className={cn("flex flex-col gap-1.5", className)}>
      {items.map((item) => {
        const Icon = item.icon;
        const muted = emphasize && !item.emphasis;
        const shown = item.display ?? String(item.value);
        return (
          <li
            key={item.key}
            title={`${item.label}: ${shown}`}
            className="group grid grid-cols-[minmax(0,9.5rem)_1fr] items-center gap-3 rounded-md px-1 py-0.5 hover:bg-muted/60 sm:grid-cols-[minmax(0,10rem)_1fr]"
          >
            <span className={cn("flex min-w-0 items-center gap-1.5 text-sm", item.emphasis ? "font-medium" : "text-muted-foreground")}>
              {Icon && <Icon aria-hidden className={cn("size-4 shrink-0", item.iconClassName)} />}
              <span className="truncate">{item.label}</span>
            </span>
            <span className="flex min-w-0 items-center gap-2">
              <span className="relative h-5 flex-1">
                <span
                  className={cn(
                    "absolute inset-y-0 left-0 rounded-r-[4px] rounded-l-[2px] transition-[width]",
                    muted ? "bg-muted-foreground/30" : TONE[tone],
                    item.value === 0 && "hidden",
                  )}
                  style={{ width: `max(${(item.value / top) * 100}%, 3px)` }}
                />
              </span>
              <span className="w-[4.75rem] shrink-0 text-right text-sm font-medium whitespace-nowrap tabular-nums">{shown}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
