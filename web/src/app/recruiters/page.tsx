import type { Metadata } from "next";
import { Suspense } from "react";
import { Pipeline } from "@/components/recruiters/pipeline";

export const metadata: Metadata = { title: "Candidatos" };

export default function RecruitersPage() {
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <h1 className="font-heading text-2xl font-normal tracking-tight sm:text-3xl">Candidatos</h1>
        <p className="text-sm text-muted-foreground">Repartidores entrevistados por Ana, con la actividad más reciente primero.</p>
      </div>
      <Suspense>
        <Pipeline />
      </Suspense>
    </div>
  );
}
