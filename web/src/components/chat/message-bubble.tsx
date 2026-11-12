import { Component, type ReactNode } from "react";
import { RotateCwIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatCopy } from "./copy";
import type { ChatMessage } from "./use-chat";

const timeFormats = new Map<string, Intl.DateTimeFormat>();

function time(intl: string, at: string) {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return "";
  let format = timeFormats.get(intl);
  if (!format) {
    format = new Intl.DateTimeFormat(intl, { hour: "2-digit", minute: "2-digit" });
    timeFormats.set(intl, format);
  }
  return format.format(date);
}

export function MessageBubble({
  message,
  copy,
  onRetry,
}: {
  message: ChatMessage;
  copy: ChatCopy;
  onRetry: (message: ChatMessage) => void;
}) {
  const mine = message.role === "candidate";
  return (
    <div className={cn("flex flex-col gap-1", mine ? "items-end" : "items-start")}>
      <div
        data-role={message.role}
        className={cn(
          "relative max-w-[85%] rounded-[18px] px-3.5 pt-2.5 pb-2 text-[0.9375rem] leading-[1.4] break-words whitespace-pre-wrap",
          mine
            ? "rounded-tr-md bg-primary text-primary-foreground"
            : "rounded-tl-md border bg-card text-card-foreground",
          message.failed && "opacity-70",
        )}
      >
        {message.text}
        <time
          dateTime={message.at}
          className={cn(
            "mt-1 block text-right text-[0.6875rem] leading-none tabular-nums",
            mine ? "text-primary-foreground/80" : "text-muted-foreground",
          )}
        >
          {time(copy.intl, message.at)}
        </time>
      </div>
      {message.failed && (
        <button
          type="button"
          onClick={() => onRetry(message)}
          className="inline-flex items-center gap-1.5 rounded-full bg-destructive/10 px-3 py-1 text-xs font-medium text-destructive outline-none hover:bg-destructive/20 focus-visible:ring-2 focus-visible:ring-destructive/40"
        >
          {copy.notSent}
          <RotateCwIcon aria-hidden className="size-3.5" />
          <span className="underline underline-offset-2">{copy.retry}</span>
        </button>
      )}
    </div>
  );
}

export function TypingIndicator({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label} className="flex items-start">
      <div className="flex items-center gap-1 rounded-[18px] rounded-tl-md border bg-card px-4 py-3.5">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="size-2 rounded-full bg-muted-foreground/60 motion-safe:animate-bounce"
            style={{ animationDelay: `${delay}ms`, animationDuration: "1s" }}
          />
        ))}
      </div>
    </div>
  );
}

/** A widget that throws while rendering disappears; the message text stays. */
export class WidgetBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}
