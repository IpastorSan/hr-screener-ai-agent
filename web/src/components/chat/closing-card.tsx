import { CalendarCheckIcon, HeartHandshakeIcon, MessageCircleIcon, ShieldCheckIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Outcome } from "@/lib/types";
import type { ChatCopy } from "./copy";

function card(outcome: Outcome | null, copy: ChatCopy) {
  switch (outcome) {
    case "qualified":
      return { Icon: CalendarCheckIcon, title: copy.closedQualifiedTitle, body: copy.closedQualified };
    case "disqualified":
      return { Icon: HeartHandshakeIcon, title: copy.closedDisqualifiedTitle, body: copy.closedDisqualified };
    case "declined_consent":
      return { Icon: ShieldCheckIcon, title: copy.closedDeclinedTitle, body: copy.closedDeclined };
    default:
      return { Icon: MessageCircleIcon, title: copy.closedOtherTitle, body: copy.closedOther };
  }
}

/** The end of the chat. It never says why: quality flags and reasons stay internal. */
export function ClosingCard({
  outcome,
  copy,
  onRestart,
}: {
  outcome: Outcome | null;
  copy: ChatCopy;
  /** Offered when the close was for data that could not be resolved. */
  onRestart?: () => void;
}) {
  const { Icon, title, body } = card(outcome, copy);
  return (
    <div
      data-testid="closing-card"
      data-outcome={outcome ?? "closed"}
      className="mx-auto mt-2 flex w-full max-w-xs flex-col items-center gap-2 rounded-[14px] border border-dashed border-input bg-card/80 px-5 py-4 text-center"
    >
      <span className="flex size-10 items-center justify-center rounded-full bg-accent text-primary">
        <Icon aria-hidden className="size-5" />
      </span>
      <p className="font-heading text-base font-medium">{title}</p>
      <p className="text-sm text-pretty text-muted-foreground">{body}</p>
      {onRestart && (
        <Button type="button" variant="outline" size="sm" className="mt-1" onClick={onRestart}>
          {copy.startAgain}
        </Button>
      )}
    </div>
  );
}
