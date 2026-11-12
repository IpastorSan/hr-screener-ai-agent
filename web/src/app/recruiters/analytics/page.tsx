import type { Metadata } from "next";
import { Suspense } from "react";
import { AnalyticsDashboard } from "@/components/analytics/dashboard";

export const metadata: Metadata = { title: "Analítica" };

export default function AnalyticsPage() {
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <h1 className="font-heading text-2xl font-normal tracking-tight sm:text-3xl">Analítica</h1>
        <p className="text-sm text-muted-foreground">Cuántos candidatos terminan la entrevista con Ana, dónde se quedan y cómo funciona el bot.</p>
      </div>
      <Suspense>
        <AnalyticsDashboard />
      </Suspense>
    </div>
  );
}
