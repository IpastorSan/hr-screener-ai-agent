import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { InsightProps } from "@/lib/types";
import { number } from "./format";

/** From full colour at the top to 40% at the last step, however many steps there are. */
function fade(index: number, total: number): number {
  return total > 1 ? 1 - (0.6 * index) / (total - 1) : 1;
}

export function FunnelChart({ insight }: InsightProps<"funnel">) {
  const { steps } = insight;
  const top = Math.max(1, ...steps.map((step) => step.count));
  return (
    <Card>
      <CardHeader>
        <CardTitle>{insight.title}</CardTitle>
      </CardHeader>
      <CardContent>
        <ol className="flex flex-col gap-2.5">
          {steps.map((step, index) => {
            const previous = index > 0 ? steps[index - 1].count : undefined;
            const rate = previous ? Math.round((step.count / previous) * 100) : undefined;
            return (
              <li
                key={`${step.stage}-${index}`}
                className="grid gap-1 sm:grid-cols-[minmax(6rem,9rem)_1fr] sm:items-center sm:gap-3"
              >
                <span className="truncate text-sm text-muted-foreground">{step.stage}</span>
                <div className="flex items-center gap-3">
                  <div className="flex-1">
                    <div
                      className="h-7 min-w-1 rounded-md bg-chart-1"
                      style={{ width: `${(step.count / top) * 100}%`, opacity: fade(index, steps.length) }}
                    />
                  </div>
                  <span className="w-20 shrink-0 text-right text-sm font-semibold tabular-nums">
                    {number.format(step.count)}
                    {rate !== undefined && (
                      <span className="ml-1.5 text-xs font-normal text-muted-foreground">{rate}%</span>
                    )}
                  </span>
                </div>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
