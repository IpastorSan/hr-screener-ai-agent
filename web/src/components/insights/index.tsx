"use client";

import type { Insight, InsightAction } from "@/lib/types";
import { BarChart } from "./bar-chart";
import { CandidateTable } from "./candidate-table";
import { FunnelChart } from "./funnel-chart";
import { StatCard } from "./stat-card";

export { BarChart, CandidateTable, FunnelChart, StatCard };

type OnAction = (action: InsightAction) => void;

export function InsightView({ insight, onAction }: { insight: Insight; onAction?: OnAction }) {
  switch (insight.kind) {
    case "stat":
      return <StatCard insight={insight} />;
    case "candidates":
      return <CandidateTable insight={insight} onAction={onAction} />;
    case "bar":
      return <BarChart insight={insight} />;
    case "funnel":
      return <FunnelChart insight={insight} />;
  }
}

type Group = { kind: "stats"; items: Extract<Insight, { kind: "stat" }>[] } | { kind: "single"; item: Insight };

function group(insights: Insight[]): Group[] {
  const groups: Group[] = [];
  for (const insight of insights) {
    const last = groups.at(-1);
    if (insight.kind !== "stat") groups.push({ kind: "single", item: insight });
    else if (last?.kind === "stats") last.items.push(insight);
    else groups.push({ kind: "stats", items: [insight] });
  }
  return groups;
}

export function InsightList({ insights, onAction }: { insights: Insight[]; onAction?: OnAction }) {
  return (
    <div className="flex flex-col gap-3">
      {group(insights).map((entry, index) =>
        entry.kind === "stats" ? (
          <div key={index} className="grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fit,minmax(11rem,1fr))]">
            {entry.items.map((item, itemIndex) => (
              <StatCard key={itemIndex} insight={item} />
            ))}
          </div>
        ) : (
          <InsightView key={index} insight={entry.item} onAction={onAction} />
        ),
      )}
    </div>
  );
}
