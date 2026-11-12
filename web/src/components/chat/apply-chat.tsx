"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { RotateCwIcon, WifiOffIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Widget } from "@/components/widgets";
import { widgetCopy } from "@/components/widgets/copy";
import type { StartContext, UiAction } from "@/lib/types";
import { ChatHeader, RestartConfirm } from "./chat-header";
import { ClosingCard } from "./closing-card";
import { Composer } from "./composer";
import { chatCopy, chatLocale } from "./copy";
import { MessageBubble, TypingIndicator, WidgetBoundary } from "./message-bubble";
import { NotifyPrompt } from "./notify-prompt";
import { actionLabel } from "./parse";
import { restartControl } from "./restart";
import { useChat } from "./use-chat";
import { useNotifyPrompt, useNudges } from "./use-nudges";

/** Reads the posting context (`country`, `location_id`, `lang`) and the conversation (`c`) once, on mount. */
export function ApplyChatFromUrl() {
  const params = useSearchParams();
  const [{ conversationId, posting }] = useState(() => {
    const country = params.get("country")?.toUpperCase();
    const locationId = params.get("location_id")?.slice(0, 64);
    const posting: StartContext = {
      ...(country === "ES" || country === "MX" ? { country } : {}),
      ...(locationId ? { location_id: locationId } : {}),
      ...(params.get("lang") === "en" ? { locale: "en" } : {}),
    };
    return { conversationId: params.get("c") ?? undefined, posting };
  });
  return <ApplyChat conversationId={conversationId} posting={posting} />;
}

export function ApplyChat({
  conversationId,
  posting,
  onConversation,
  onBack,
  open = true,
  headingId,
}: {
  conversationId?: string;
  posting: StartContext;
  /** Where the conversation id goes; by default the standalone page's URL (`/apply?c=`). */
  onConversation?: (id: string | null) => void;
  /** Inside a job posting: the header's back button closes the chat instead of leaving. */
  onBack?: () => void;
  /** False while a job posting's panel is closed: the chat stays mounted but stops nudging. */
  open?: boolean;
  /** Inside a job posting: Ana's name is an `<h2>` with this id, which labels the panel. */
  headingId?: string;
}) {
  const { messages, state, status, send, retry, nudge, reload, restart } = useChat({ conversationId, posting, onConversation });
  const locale = chatLocale(state, posting.locale === "en" ? "en" : posting.country === "MX" ? "es-MX" : "es");
  const copy = chatCopy(locale);
  const scroller = useRef<HTMLDivElement>(null);

  const last = messages.at(-1);
  // `ended` stays false while something is pending: interview slots, a talent-pool question,
  // or a booked interview the candidate can still move by replying.
  const closed = Boolean(state?.ended);
  // A license close can still be undone by the very next message ("sí que tengo carnet"):
  // the composer stays open for it, then locks as for any other close.
  const locked = closed && !state?.reopenable;
  const waiting = status === "waiting";
  useNudges({
    messages,
    active: open && status === "ready" && !closed,
    consented: state !== null && state.stage !== "consent",
    paused: Boolean(state?.paused),
    title: copy.notifyTitle,
    locale,
    nudge,
  });
  const notifyPrompt = useNotifyPrompt(messages);

  // The header's "start again": a new conversation; an application still open in this one closes.
  const control = restartControl(status, messages);
  const [confirming, setConfirming] = useState(false);
  const askingRestart = confirming && control === "confirm";
  const startAgain = () => {
    setConfirming(false);
    void restart();
  };
  const onRestart =
    control === "hidden" ? undefined : control === "direct" ? startAgain : () => setConfirming((open) => !open);

  useEffect(() => {
    const el = scroller.current;
    if (open && el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, status, closed, open]);

  const onAction = (action: UiAction) => {
    if (!last?.ui) return;
    const labels = widgetCopy(locale);
    void send({ action }, actionLabel(last.ui, action, { yes: labels.confirmYes, no: labels.confirmNo }));
  };

  return (
    <div lang={locale} className="flex h-full min-h-0 flex-col overflow-hidden bg-background">
      <ChatHeader
        copy={copy}
        onBack={onBack}
        headingId={headingId}
        onRestart={onRestart}
        restartPending={askingRestart}
      />
      {askingRestart && (
        <RestartConfirm copy={copy} onConfirm={startAgain} onCancel={() => setConfirming(false)} />
      )}
      <div
        ref={scroller}
        aria-live="polite"
        aria-busy={waiting}
        // `relative`: the visually hidden labels in the widgets ("opens in a new tab") are
        // absolutely positioned; without it they escape the list and grow the page.
        className="chat-wallpaper relative min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-4"
      >
        {status === "unavailable" && messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <span className="flex size-12 items-center justify-center rounded-full border bg-card text-muted-foreground">
              <WifiOffIcon aria-hidden className="size-6" />
            </span>
            <p className="font-heading text-lg font-medium">{copy.startFailed}</p>
            <p className="text-sm text-muted-foreground">{copy.startFailedHint}</p>
            <RetryButton label={copy.retry} onClick={reload} />
          </div>
        ) : (
          <div className="flex flex-col gap-2.5">
            {messages.map((message) => {
              const active = message === last && message.role === "agent" && !closed;
              const ui = active || !message.restored || message.ui?.kind === "booking" ? message.ui : null;
              return (
                <div key={message.id} className="flex flex-col gap-2">
                  <MessageBubble message={message} copy={copy} onRetry={retry} />
                  {ui && (
                    <div data-widget={ui.kind} className="max-w-[92%] pl-1">
                      <WidgetBoundary>
                        <Widget ui={ui} onAction={onAction} disabled={!active || waiting} locale={locale} />
                      </WidgetBoundary>
                    </div>
                  )}
                </div>
              );
            })}
            {notifyPrompt.show && !closed && (
              <NotifyPrompt copy={copy} onAllow={() => void notifyPrompt.allow()} onDismiss={notifyPrompt.dismiss} />
            )}
            {(waiting || status === "loading") && <TypingIndicator label={status === "loading" ? copy.loading : copy.typing} />}
            {status === "unavailable" && messages.length > 0 && (
              <RetryButton label={copy.retry} onClick={reload} />
            )}
            {closed && status !== "loading" && <ClosingCard
                outcome={state?.outcome ?? null}
                copy={copy}
                onRestart={state?.restartable ? () => void restart() : undefined}
              />}
          </div>
        )}
      </div>
      <Composer
        copy={copy}
        disabled={status !== "ready"}
        closed={locked}
        onSend={(text) => void send({ text }, text)}
      />
    </div>
  );
}

function RetryButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button size="lg" className="self-center" onClick={onClick}>
      <RotateCwIcon data-icon="inline-start" />
      {label}
    </Button>
  );
}
