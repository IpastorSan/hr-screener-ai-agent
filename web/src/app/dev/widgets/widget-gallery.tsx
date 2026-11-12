"use client";

import { useState } from "react";
import { toast } from "sonner";
import { InsightList } from "@/components/insights";
import { Separator } from "@/components/ui/separator";
import { Widget } from "@/components/widgets";
import type { InsightAction, UiAction } from "@/lib/types";
import { candidateSamples, recruiterSample } from "./samples";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-5">
      <h2 className="font-heading text-2xl font-normal tracking-tight">{title}</h2>
      {children}
    </section>
  );
}

export function WidgetGallery() {
  const [last, setLast] = useState<UiAction | InsightAction | null>(null);

  const record = (action: UiAction | InsightAction) => {
    setLast(action);
    toast(JSON.stringify(action));
  };

  return (
    <div className="flex flex-col gap-12">
      <Section title="Chat del candidato">
        <div className="flex flex-col gap-8">
          {candidateSamples.map(({ prompt, ui }) => (
            <div key={ui.kind} data-widget={ui.kind} className="flex max-w-lg flex-col gap-3">
              <p className="w-fit max-w-full rounded-2xl rounded-bl-sm bg-secondary px-4 py-2.5 text-sm text-secondary-foreground">
                {prompt}
              </p>
              <Widget ui={ui} onAction={record} />
            </div>
          ))}
        </div>
      </Section>

      <Separator />

      <Section title="Respuestas del panel">
        <div data-insights="recruiter" className="flex flex-col gap-4">
          <p className="max-w-prose text-sm leading-relaxed text-pretty">{recruiterSample.text}</p>
          <InsightList insights={recruiterSample.insights} onAction={record} />
        </div>
      </Section>

      <Separator />

      <Section title="Última acción">
        <pre data-testid="last-action" className="overflow-x-auto rounded-lg bg-muted p-3 text-xs">
          {last ? JSON.stringify(last, null, 2) : "Pulsa cualquier widget"}
        </pre>
      </Section>
    </div>
  );
}
