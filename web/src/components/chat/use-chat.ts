"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getTranscript, sendMessage, sendNudge, startConversation } from "@/lib/api";
import type { ChatReply, ChatState, MessageIn, StartContext, Ui } from "@/lib/types";
import { parseDone, parseReply, parseState, parseUi } from "./parse";

export type ChatMessage = {
  id: string;
  role: "agent" | "candidate";
  text: string;
  ui: Ui | null;
  at: string;
  /** Set on a candidate message whose turn failed; holds the payload to resend. */
  failed?: MessageIn;
  /** Loaded from the transcript: its widget, if any, was answered in an earlier visit. */
  restored?: boolean;
  /** Ana's re-engagement message after a silence. */
  nudge?: boolean;
};

export type ChatStatus = "loading" | "ready" | "waiting" | "unavailable";

let nextId = 0;
const newId = () => `m${++nextId}`;

function browserContext(posting: StartContext): StartContext {
  const context: StartContext = { ...posting };
  try {
    context.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {}
  // An English posting passes `locale: "en"`; otherwise the browser's language is the hint.
  if (!context.locale && typeof navigator !== "undefined" && navigator.language) context.locale = navigator.language;
  return context;
}

/** The standalone chat page keeps the conversation in its URL, so a refresh resumes it. */
function showConversationOnApplyPage(id: string | null) {
  window.history.replaceState(window.history.state, "", id ? `/apply?c=${encodeURIComponent(id)}` : "/apply");
}

export function useChat({
  conversationId,
  posting,
  onConversation = showConversationOnApplyPage,
}: {
  conversationId?: string;
  posting: StartContext;
  /** Called when the conversation starts, is replaced or is lost; keeps the URL resumable. */
  onConversation?: (id: string | null) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [state, setState] = useState<ChatState | null>(null);
  const [status, setStatus] = useState<ChatStatus>("loading");
  const idRef = useRef<string | null>(conversationId ?? null);
  const booted = useRef(false);
  const showConversation = useRef(onConversation);
  useEffect(() => {
    showConversation.current = onConversation;
  }, [onConversation]);

  const start = useCallback(async (restartOf?: string) => {
    setStatus("loading");
    try {
      const out = await startConversation(browserContext(posting), "web", restartOf);
      idRef.current = out.conversation_id;
      showConversation.current(out.conversation_id);
      const reply = parseReply(out.reply);
      setMessages([{ id: newId(), role: "agent", text: reply.text, ui: reply.ui, at: new Date().toISOString() }]);
      setState(parseState(out.state));
      setStatus("ready");
    } catch {
      setStatus("unavailable");
    }
  }, [posting]);

  const load = useCallback(
    async (id: string) => {
      setStatus("loading");
      try {
        const out = await getTranscript(id);
        idRef.current = out.conversation_id;
        setMessages(
          out.turns.map((turn) => ({
            id: newId(),
            role: turn.role,
            text: turn.text,
            ui: turn.role === "agent" ? parseUi(turn.ui) : null,
            at: turn.created_at,
            restored: true,
            nudge: turn.nudge === true,
          })),
        );
        setState(parseState(out.state));
        setStatus("ready");
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          idRef.current = null;
          showConversation.current(null);
          await start();
        } else {
          setStatus("unavailable");
        }
      }
    },
    [start],
  );

  const boot = useCallback(() => (idRef.current ? load(idRef.current) : start()), [load, start]);

  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    void boot();
  }, [boot]);

  const send = useCallback(
    async (message: MessageIn, label: string, retryId?: string) => {
      const id = idRef.current;
      if (!id) return;
      const candidateId = retryId ?? newId();
      setMessages((prev) =>
        retryId
          ? prev.map((m) => (m.id === retryId ? { ...m, failed: undefined } : m))
          : [...prev, { id: candidateId, role: "candidate", text: label, ui: null, at: new Date().toISOString() }],
      );
      setStatus("waiting");

      // `reply` and `done` both carry Ana's answer; the turn is shown once, when the stream ends.
      let reply: ChatReply | null = null;
      let stored: string | null = null;
      let switchTo: string | null = null;
      try {
        for await (const event of sendMessage(id, message)) {
          if (event.event === "reply") {
            reply = parseReply(event.data);
          } else if (event.event === "done") {
            const done = parseDone(event.data);
            reply = done.reply ?? reply;
            stored = done.candidate_text;
            if (done.state) setState(done.state);
            if (done.resumed && done.conversation_id && done.conversation_id !== id) switchTo = done.conversation_id;
          } else if (event.event === "error") {
            throw new Error("turn failed");
          }
        }
        if (!reply || (!reply.text && !reply.ui)) throw new Error("empty turn");
      } catch {
        setMessages((prev) => prev.map((m) => (m.id === candidateId ? { ...m, failed: message } : m)));
        setStatus("ready");
        return;
      }
      const answer = reply;
      // The bubble shows what the transcript stores, so it reads the same after a reload.
      setMessages((prev) => [
        ...prev.map((m) => (m.id === candidateId && stored ? { ...m, text: stored } : m)),
        { id: newId(), role: "agent", text: answer.text, ui: answer.ui, at: new Date().toISOString() },
      ]);

      if (switchTo) {
        idRef.current = switchTo;
        showConversation.current(switchTo);
        await load(switchTo);
        return;
      }
      setStatus("ready");
    },
    [load],
  );

  /** Ask for nudge `attempt` and show it. Resolves to its text, or null when none was sent.
   * The status is left alone: the candidate may be typing or sending as the nudge arrives, and
   * nothing they do should wait for it (or be marked ready by it mid-turn). */
  const nudge = useCallback(async (attempt: number): Promise<string | null> => {
    const id = idRef.current;
    if (!id) return null;
    try {
      const out = await sendNudge(id, attempt);
      const reply = parseReply(out.reply);
      const next = parseState(out.state);
      if (next) setState(next);
      if (!reply.text) return null;
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: "agent", text: reply.text, ui: reply.ui, at: new Date().toISOString(), nudge: true },
      ]);
      return reply.text;
    } catch {
      return null;
    }
  }, []);

  const retry = useCallback(
    (message: ChatMessage) => {
      if (message.failed) void send(message.failed, message.text, message.id);
    },
    [send],
  );

  /** A new application in place of this one: one still open here is closed, so the same phone
   * or email starts fresh instead of resuming it. */
  const restart = useCallback(() => {
    const previous = idRef.current ?? undefined;
    idRef.current = null;
    showConversation.current(null);
    return start(previous);
  }, [start]);

  return { messages, state, status, send, retry, nudge, reload: boot, restart };
}

