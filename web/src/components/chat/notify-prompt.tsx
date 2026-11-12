import { BellRingIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ChatCopy } from "./copy";

/** A small in-chat offer to turn on browser notifications, shown after the first answer. */
export function NotifyPrompt({ copy, onAllow, onDismiss }: { copy: ChatCopy; onAllow: () => void; onDismiss: () => void }) {
  return (
    <div
      data-testid="notify-prompt"
      className="mx-auto mt-1 flex w-[92%] max-w-sm flex-col items-center gap-2 rounded-[14px] border border-dashed border-input bg-card/80 px-3 py-2.5 text-center"
    >
      <p className="flex items-start gap-2 text-left text-sm text-pretty text-muted-foreground">
        <BellRingIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-primary" />
        {copy.notifyPrompt}
      </p>
      <div className="flex gap-2">
        <Button size="sm" onClick={onAllow}>
          {copy.notifyAllow}
        </Button>
        <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={onDismiss}>
          {copy.notifyDismiss}
        </Button>
      </div>
    </div>
  );
}
