import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import type { InsightProps } from "@/lib/types";
import { cn } from "@/lib/utils";
import { number } from "./format";

export function StatCard({ insight }: InsightProps<"stat">) {
  const { label, value, delta, hint } = insight;
  return (
    <Card size="sm" className="gap-1">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-0.5">
        <div className="flex items-baseline gap-2">
          <span className="font-heading text-3xl font-normal tabular-nums">
            {typeof value === "number" ? number.format(value) : value}
          </span>
          {delta !== undefined && delta !== 0 && (
            <span className={cn("text-xs font-medium tabular-nums", delta > 0 ? "text-ok" : "text-destructive")}>
              {delta > 0 ? "+" : "−"}
              {number.format(Math.abs(delta))}
            </span>
          )}
        </div>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </CardContent>
    </Card>
  );
}
