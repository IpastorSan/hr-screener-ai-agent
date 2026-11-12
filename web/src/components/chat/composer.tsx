"use client";

import { useRef, useState } from "react";
import { SendHorizontalIcon } from "lucide-react";
import type { ChatCopy } from "./copy";

const MAX_LENGTH = 2000;

/**
 * `disabled`: nothing can be sent right now (a reply is on its way). The text box stays
 * editable and keeps its focus, so the candidate can type the next message meanwhile and no
 * keystroke is lost; only sending waits. `closed`: the conversation is over, the box locks.
 */
export function Composer({
  copy,
  disabled,
  closed,
  onSend,
}: {
  copy: ChatCopy;
  disabled: boolean;
  closed: boolean;
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const canSend = !disabled && !closed && text.trim().length > 0;

  const submit = () => {
    if (!canSend) return;
    onSend(text.trim());
    setText("");
    input.current?.focus(); // back from the send button, for the next answer
  };

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className="flex shrink-0 items-end gap-2 border-t bg-card px-3 pt-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))]"
    >
      <textarea
        ref={input}
        rows={1}
        value={text}
        maxLength={MAX_LENGTH}
        disabled={closed}
        aria-label={copy.placeholder}
        placeholder={closed ? copy.placeholderClosed : copy.placeholder}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit();
          }
        }}
        className="field-sizing-content max-h-32 min-h-11 flex-1 resize-none rounded-[22px] border border-transparent bg-muted px-4 py-2.5 text-base leading-snug outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-60"
      />
      <button
        type="submit"
        aria-label={copy.send}
        disabled={!canSend}
        className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition-[opacity,transform,background-color] outline-none hover:bg-primary-hover focus-visible:ring-3 focus-visible:ring-ring/50 active:scale-95 disabled:cursor-not-allowed disabled:opacity-40"
      >
        <SendHorizontalIcon className="size-5" />
      </button>
    </form>
  );
}
