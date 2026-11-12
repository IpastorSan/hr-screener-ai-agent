"use client";

import { useState } from "react";
import { CheckIcon, PencilIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { WidgetProps } from "@/lib/types";
import { widgetCopy } from "./copy";

export function ConfirmCard({ ui, onAction, disabled, locale }: WidgetProps<"confirm">) {
  const copy = widgetCopy(locale);
  const [answer, setAnswer] = useState<boolean | null>(null);
  const locked = disabled || answer !== null;

  const send = (value: boolean) => {
    setAnswer(value);
    onAction({ kind: "confirm", value });
  };

  return (
    <Card className="w-full max-w-md rounded-[14px]">
      <CardHeader>
        <CardTitle className="text-lg font-medium">{copy.confirmTitle}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-[minmax(0,auto)_1fr] gap-x-4 gap-y-2">
          {ui.fields.map((field) => (
            <div key={field.label} className="col-span-2 grid grid-cols-subgrid border-b border-dashed pb-2 last:border-0 last:pb-0">
              <dt className="text-muted-foreground">{field.label}</dt>
              <dd className="font-medium">{field.value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
      <CardFooter className="flex-wrap gap-2 border-t bg-muted/40 pb-(--card-spacing)">
        <Button size="lg" disabled={locked} className={cn(answer === true && "disabled:opacity-100")} onClick={() => send(true)}>
          <CheckIcon data-icon="inline-start" />
          {copy.confirmYes}
        </Button>
        <Button
          size="lg"
          variant="ghost"
          disabled={locked}
          className={cn(answer === false && "bg-muted disabled:opacity-100")}
          onClick={() => send(false)}
        >
          <PencilIcon data-icon="inline-start" />
          {copy.confirmNo}
        </Button>
      </CardFooter>
    </Card>
  );
}
