"use client";

import { Bar, BarChart as RechartsBarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import type { InsightProps } from "@/lib/types";

const config = { value: { label: "Total", color: "var(--chart-1)" } } satisfies ChartConfig;

export function BarChart({ insight }: InsightProps<"bar">) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{insight.title}</CardTitle>
      </CardHeader>
      <CardContent>
        <ChartContainer config={config} className="aspect-auto h-56 w-full">
          <RechartsBarChart data={insight.data} margin={{ left: -16, right: 4 }}>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} interval={0} />
            <YAxis tickLine={false} axisLine={false} width={40} allowDecimals={false} />
            <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
            <Bar dataKey="value" fill="var(--color-value)" radius={[6, 6, 2, 2]} maxBarSize={56} />
          </RechartsBarChart>
        </ChartContainer>
      </CardContent>
    </Card>
  );
}
