"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { ChevronLeftIcon, MessageSquareTextIcon, MicIcon, MicOffIcon, PhoneOffIcon, SparklesIcon } from "lucide-react";
import { RestartButton, RestartConfirm } from "@/components/chat/chat-header";
import { Button, buttonVariants } from "@/components/ui/button";
import { ApiError, getTranscript, startConversation, startVoiceSession } from "@/lib/api";
import type { ChatLocale, ChatState, StartContext, TranscriptOut, TranscriptTurn } from "@/lib/types";
import { cn } from "@/lib/utils";
import { voiceCopy, type VoiceCopy } from "./copy";
import {
  type Carried,
  conversationKey,
  followResume,
  postingOf,
  readStored,
  resumable,
  shownTurns,
  voicePageHref,
  voiceRestartControl,
  writeStored,
} from "./session";

/** Set at build time once the ElevenLabs agent exists (`sazon voice-agent create`). */
const AGENT_ID = process.env.NEXT_PUBLIC_ELEVENLABS_AGENT_ID?.trim() ?? "";
const POLL_MS = 1500;
const PRIVACY_PATHS = { es: "/privacidad", en: "/en/privacy" } as const;

type Phase = "checking" | "idle" | "preparing" | "live" | "ended" | "failed" | "mic-denied" | "busy" | "closed";

/**
 * Reads the posting context (`country`, `location_id`, `lang`) and a linked conversation (`c`).
 * Each posting gets its own call: another country or language mounts a new one, which never
 * picks up the last page's conversation.
 */
export function VoiceCallFromUrl() {
  const params = useSearchParams();
  const posting = useMemo(() => postingOf(params), [params]);
  const locale: ChatLocale = posting.locale === "en" ? "en" : posting.country === "MX" ? "es-MX" : "es";
  const linkedId = params.get("c");
  if (!AGENT_ID) return <NotConfigured locale={locale} chatHref={chatHref(posting, linkedId)} />;
  return (
    <ConversationProvider key={voicePageHref(posting)}>
      <VoiceCall linkedId={linkedId} posting={posting} fallbackLocale={locale} />
    </ConversationProvider>
  );
}

function chatHref(posting: StartContext, conversationId?: string | null) {
  if (conversationId) return `/apply?c=${encodeURIComponent(conversationId)}`;
  const params = new URLSearchParams();
  if (posting.country) params.set("country", posting.country);
  if (posting.location_id) params.set("location_id", posting.location_id);
  if (posting.locale === "en") params.set("lang", "en");
  const query = params.toString();
  return query ? `/apply?${query}` : "/apply";
}

function localeOf(state: Pick<ChatState, "language" | "country"> | null, fallback: ChatLocale): ChatLocale {
  if (!state) return fallback;
  if (state.language === "en") return "en";
  return state.country === "MX" ? "es-MX" : "es";
}

function browserStore(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

function VoiceCall({
  linkedId,
  posting,
  fallbackLocale,
}: {
  /** The conversation named in the URL; read once, when the page opens. */
  linkedId: string | null;
  posting: StartContext;
  fallbackLocale: ChatLocale;
}) {
  // Fixed for the life of the call: another posting mounts another call.
  const [page] = useState(() => ({ posting, key: conversationKey("voice", posting), linkedId }));
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("checking");
  const [transcript, setTranscript] = useState<TranscriptOut | null>(null);
  // What the call said before it resumed an application and moved to its conversation.
  const [carried, setCarried] = useState<Carried | null>(null);
  const [confirming, setConfirming] = useState(false);
  const locale = localeOf(transcript?.state ?? null, fallbackLocale);
  const copy = voiceCopy(locale);
  const idRef = useRef<string | null>(null);
  const inFlight = useRef<AbortController | null>(null);

  /** The page's conversation, in its URL (next to the posting) and in this page's storage. */
  const remember = useCallback(
    (id: string | null) => {
      idRef.current = id;
      setConversationId(id);
      writeStored(browserStore(), page.key, id);
      window.history.replaceState(window.history.state, "", voicePageHref(page.posting, id));
    },
    [page],
  );

  // The newest request wins: starting one aborts the last, so a slow response can't roll the
  // transcript back, nor show a conversation the page has since left. When the call resumed
  // the candidate's application, the page follows it to the application's conversation (as
  // the chat does): the call's turns, "start again" and a reload go there from now on.
  const refresh = useCallback(
    function load(id: string | null) {
      if (!id) return;
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      // On failure the panel keeps what it had; the call itself does not depend on it.
      getTranscript(id, controller.signal).then((out) => {
        if (controller.signal.aborted || idRef.current !== id) return;
        setTranscript(out);
        const next = followResume(out);
        if (!next) return;
        setCarried(next.carried);
        remember(next.id);
        load(next.id);
      }, () => undefined);
    },
    [remember],
  );

  useEffect(() => () => inFlight.current?.abort(), []);

  // Carry on only with a voice conversation of this page's country: a chat's, another
  // country's, a stored one that has ended or one the server no longer has is dropped, and
  // the next call starts a new conversation at consent. Checked when the page opens, and again
  // when a link names another conversation than the one the page wrote into its own URL.
  const [linked, setLinked] = useState(page.linkedId);
  if (linkedId && linkedId !== linked && linkedId !== conversationId) {
    setLinked(linkedId);
    setPhase("checking");
  }
  useEffect(() => {
    let cancelled = false;
    const from = linked ? "url" : "storage";
    let id = linked ?? readStored(browserStore(), page.key);
    const check = async () => {
      let keep = false;
      let loaded: TranscriptOut | null = null;
      let carriedOver: Carried | null = null;
      if (id) {
        try {
          loaded = await getTranscript(id);
          const next = followResume(loaded);
          if (next) {
            // Left when it resumed an application: the page carries on in that one.
            id = next.id;
            carriedOver = next.carried;
            loaded = await getTranscript(id);
          }
          keep = resumable(loaded, page.posting, from);
        } catch (error) {
          keep = !(error instanceof ApiError && error.status === 404);
        }
      }
      if (cancelled) return;
      remember(keep ? id : null);
      setTranscript(keep ? loaded : null);
      setCarried(keep ? carriedOver : null);
      setPhase("idle");
    };
    void check();
    return () => {
      cancelled = true;
    };
  }, [linked, page, remember]);

  const conversation = useConversation({
    onDisconnect: () => {
      setPhase((current) => (current === "live" ? "ended" : current));
      refresh(idRef.current);
    },
    onError: () => setPhase("failed"),
  });
  const { status, isSpeaking } = conversation;

  useEffect(() => {
    if (!conversationId || phase !== "live") return;
    const timer = window.setInterval(() => refresh(conversationId), POLL_MS);
    return () => window.clearInterval(timer);
  }, [conversationId, phase, refresh]);

  /** A new voice conversation for this page; `restartOf` is closed if still open. Null when busy. */
  const open = async (restartOf?: string) => {
    const out = await startConversation(withTimezone(page.posting), "voice", restartOf);
    if (!out.conversation_id) return null;
    remember(out.conversation_id);
    return out.conversation_id;
  };

  const start = async () => {
    setConfirming(false);
    setPhase("preparing");
    try {
      let id = idRef.current ?? (await open());
      if (!id) {
        setPhase("busy");
        return;
      }
      let session;
      try {
        session = await startVoiceSession(id);
      } catch (error) {
        // The page's conversation is gone from the server (a reset database): a new one.
        if (!(error instanceof ApiError && error.status === 404)) throw error;
        id = await open();
        if (!id) {
          setPhase("busy");
          return;
        }
        session = await startVoiceSession(id);
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((track) => track.stop());
      } catch {
        setPhase("mic-denied");
        return;
      }
      const overrides = {
        agent: { language: session.language },
        ...(session.voice_id ? { tts: { voiceId: session.voice_id } } : {}),
      };
      const common = {
        dynamicVariables: session.dynamic_variables,
        customLlmExtraBody: session.custom_llm_extra_body,
        overrides,
      };
      if (session.conversation_token) {
        conversation.startSession({ ...common, conversationToken: session.conversation_token });
      } else {
        conversation.startSession({ ...common, agentId: session.agent_id ?? AGENT_ID, connectionType: "webrtc" });
      }
      setPhase("live");
      refresh(id);
    } catch (error) {
      setPhase(error instanceof ApiError && error.status === 409 ? "closed" : "failed");
    }
  };

  const hangUp = () => {
    conversation.endSession();
    setPhase("ended");
    refresh(idRef.current);
  };

  // "Empezar de nuevo", as in the chat (SAZ-72): hang up, forget this page's conversation and
  // open a new one at consent. An application still open in the old one closes (`restart_of`),
  // so the same phone or email is not offered it again.
  const startAgain = async () => {
    setConfirming(false);
    const previous = idRef.current ?? undefined;
    const wasLive = phase === "live";
    setPhase("preparing");
    inFlight.current?.abort();
    remember(null);
    setTranscript(null);
    setCarried(null);
    if (wasLive) conversation.endSession();
    try {
      const id = await open(previous);
      setPhase(id ? "idle" : "busy");
      refresh(id);
    } catch {
      setPhase("failed");
    }
  };

  const turns = shownTurns(transcript, carried);
  const control = voiceRestartControl(phase, conversationId, turns);
  const askingRestart = confirming && control === "confirm";
  const onRestart =
    control === "hidden"
      ? undefined
      : control === "direct"
        ? () => void startAgain()
        : () => setConfirming((asking) => !asking);

  const live = phase === "live";
  const statusText = live
    ? status === "connected"
      ? isSpeaking
        ? copy.status.speaking
        : copy.status.listening
      : copy.status.connecting
    : phase === "preparing"
      ? copy.status.preparing
      : phase === "ended"
        ? copy.status.ended
        : copy.status.idle;
  const notice = { "mic-denied": copy.micDenied, failed: copy.failed, busy: copy.busy, closed: copy.closed }[
    phase as "mic-denied" | "failed" | "busy" | "closed"
  ];
  const ended = Boolean(transcript?.state.ended);

  return (
    <div lang={locale} className="flex h-full min-h-0 flex-col overflow-hidden bg-background">
      <CallHeader copy={copy} onRestart={onRestart} restartPending={askingRestart} />
      {askingRestart && (
        <RestartConfirm copy={copy} onConfirm={() => void startAgain()} onCancel={() => setConfirming(false)} />
      )}
      <section className="flex shrink-0 flex-col items-center gap-3 bg-card px-4 pt-6 pb-5 text-center">
        <span
          aria-hidden
          className={cn(
            "flex size-24 items-center justify-center rounded-full bg-primary font-heading text-4xl font-normal text-primary-foreground transition-shadow",
            live && isSpeaking && "ring-8 ring-primary/20 motion-safe:animate-pulse",
          )}
        >
          A
        </span>
        <p role="status" aria-live="polite" data-testid="call-status" className="font-heading text-xl font-normal">
          {statusText}
        </p>
        {(phase === "idle" || phase === "checking") && (
          <p className="max-w-xs text-sm text-muted-foreground">{copy.intro}</p>
        )}
        {notice && <p className="max-w-xs rounded-lg bg-destructive-soft px-3 py-2 text-sm text-destructive">{notice}</p>}
        <div className="flex items-center gap-3">
          {live ? (
            <Button size="lg" variant="destructive" className="h-12 px-6 text-base" onClick={hangUp}>
              <PhoneOffIcon data-icon="inline-start" />
              {copy.end}
            </Button>
          ) : (
            <Button
              size="lg"
              className="h-12 px-6 text-base"
              disabled={
                phase === "checking" || phase === "preparing" || phase === "busy" || phase === "closed" || ended
              }
              onClick={() => void start()}
            >
              {phase === "mic-denied" ? <MicOffIcon data-icon="inline-start" /> : <MicIcon data-icon="inline-start" />}
              {copy.start}
            </Button>
          )}
        </div>
        <Link
          href={chatHref(page.posting, conversationId)}
          onClick={() => {
            if (live) conversation.endSession();
          }}
          className="inline-flex items-center gap-1.5 text-sm font-bold text-primary underline underline-offset-3"
        >
          <MessageSquareTextIcon aria-hidden className="size-4" />
          {copy.switchToChat}
        </Link>
        <p className="max-w-xs text-xs text-muted-foreground">
          {copy.privacy}{" "}
          <Link
            href={PRIVACY_PATHS[locale === "en" ? "en" : "es"]}
            target="_blank"
            className="font-bold text-foreground underline underline-offset-2"
          >
            {copy.privacyLink}
          </Link>
        </p>
      </section>
      <TranscriptPanel copy={copy} turns={turns} />
    </div>
  );
}

function withTimezone(posting: StartContext): StartContext {
  const context: StartContext = { ...posting };
  try {
    context.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {}
  if (!context.locale && typeof navigator !== "undefined" && navigator.language) context.locale = navigator.language;
  return context;
}

function CallHeader({
  copy,
  onRestart,
  restartPending = false,
}: {
  copy: VoiceCopy;
  /** The "start again" button, absent until there is a conversation to leave. */
  onRestart?: () => void;
  restartPending?: boolean;
}) {
  return (
    <header className="@container flex shrink-0 items-center gap-2.5 border-b bg-card px-3 pt-[max(0.875rem,env(safe-area-inset-top))] pb-3 text-card-foreground">
      <Link
        href="/"
        aria-label="Grupo Sazón"
        className="flex size-8 items-center justify-center rounded-full outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <ChevronLeftIcon className="size-5" />
      </Link>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2">
          <h1 className="font-heading text-lg leading-tight font-medium">Ana</h1>
          <span
            data-testid="ai-badge"
            title={copy.aiBadgeTitle}
            className="inline-flex h-5 items-center gap-1 rounded-full bg-accent px-2 text-[0.6875rem] leading-none font-bold tracking-wider text-accent-foreground uppercase"
          >
            <SparklesIcon aria-hidden className="size-3" />
            {copy.aiBadge}
          </span>
        </div>
        <p className="truncate text-xs text-muted-foreground">Grupo Sazón · {copy.title}</p>
      </div>
      {onRestart && <RestartButton label={copy.startAgain} onClick={onRestart} pending={restartPending} />}
    </header>
  );
}

function TranscriptPanel({ copy, turns }: { copy: VoiceCopy; turns: readonly TranscriptTurn[] }) {
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [turns.length]);
  const last = turns.at(-1);
  const fields = last?.ui?.kind === "confirm" ? last.ui.fields : null;
  return (
    <section aria-labelledby="transcript-title" className="flex min-h-0 flex-1 flex-col border-t">
      <h2 id="transcript-title" className="bg-card px-4 pt-3 pb-2 text-xs font-bold tracking-wider text-muted-foreground uppercase">
        {copy.transcript}
      </h2>
      <div ref={scroller} aria-live="polite" className="chat-wallpaper min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {turns.length === 0 ? (
          <p className="px-2 py-6 text-center text-sm text-muted-foreground">{copy.transcriptEmpty}</p>
        ) : (
          <ol className="flex flex-col gap-2">
            {turns.map((turn, index) => {
              const mine = turn.role === "candidate";
              return (
                <li key={`${turn.created_at}-${index}`} className={cn("flex", mine ? "justify-end" : "justify-start")}>
                  <p
                    className={cn(
                      "max-w-[85%] rounded-[18px] px-3.5 py-2 text-[0.9375rem] leading-[1.4]",
                      mine
                        ? "rounded-tr-md bg-primary text-primary-foreground"
                        : "rounded-tl-md border bg-card text-card-foreground",
                    )}
                  >
                    <span className="sr-only">{mine ? copy.you : "Ana"}: </span>
                    {turn.text}
                  </p>
                </li>
              );
            })}
          </ol>
        )}
        {fields && (
          <dl className="mt-3 grid grid-cols-[minmax(0,auto)_1fr] gap-x-3 gap-y-1 rounded-[14px] border bg-card p-3 text-sm">
            <dt className="col-span-2 pb-1 font-heading font-medium">{copy.details}</dt>
            {fields.map((field) => (
              <div key={field.label} className="col-span-2 grid grid-cols-subgrid">
                <dt className="text-muted-foreground">{field.label}</dt>
                <dd className="font-medium">{field.value}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </section>
  );
}

function NotConfigured({ locale, chatHref }: { locale: ChatLocale; chatHref: string }) {
  const copy = voiceCopy(locale);
  return (
    <div lang={locale} className="flex h-full min-h-0 flex-col overflow-hidden bg-background">
      <CallHeader copy={copy} />
      <div
        data-testid="voice-not-configured"
        className="flex flex-1 flex-col items-center justify-center gap-4 px-8 text-center"
      >
        <span className="flex size-16 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <MicOffIcon aria-hidden className="size-7" />
        </span>
        <p className="font-heading text-lg font-medium">{copy.notConfiguredTitle}</p>
        <p className="text-sm text-muted-foreground">{copy.notConfigured}</p>
        <Link href={chatHref} className={cn(buttonVariants({ size: "lg" }), "h-11 rounded-full px-6 text-base")}>
          <MessageSquareTextIcon data-icon="inline-start" />
          {copy.goToChat}
        </Link>
      </div>
    </div>
  );
}
