"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { NUDGE_DELAYS_MS, armedAttempt } from "./nudge-schedule";
import type { ChatMessage } from "./use-chat";

/** No browser notifications between 22:00 and 09:00, the candidate's local time. */
const QUIET_FROM_HOUR = 22;
const QUIET_UNTIL_HOUR = 9;
const DISMISSED_KEY = "sazon:notify-dismissed";

/** `NEXT_PUBLIC_NUDGE_SCALE` shortens the timers for tests and demos (0.01 makes 3 minutes 1.8 s). */
function nudgeScale(): number {
  const scale = Number(process.env.NEXT_PUBLIC_NUDGE_SCALE ?? "1");
  return Number.isFinite(scale) && scale > 0 ? scale : 1;
}

export function inQuietHours(now = new Date()): boolean {
  const hour = now.getHours();
  return hour >= QUIET_FROM_HOUR || hour < QUIET_UNTIL_HOUR;
}

function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

/**
 * Nudges a candidate who goes quiet with the chat open, and tells them with a browser
 * notification when the tab is in the background.
 *
 * The timer runs while Ana has the last word, the chat is open and not paused, and no request is
 * in flight (`armedAttempt`). Any new message, keystroke or tap starts it over. A chat restored from an earlier visit waits for
 * the candidate's next move: the nudge is for someone who just stepped away.
 */
export function useNudges({
  messages,
  active,
  consented,
  paused,
  title,
  locale,
  nudge,
}: {
  messages: ChatMessage[];
  /** False while the chat is hidden, once it has ended, or while a request is in flight. */
  active: boolean;
  /** The candidate said yes to the privacy notice: no nudge before it. */
  consented: boolean;
  /** The candidate took the offer to pause: no nudges until they write again. */
  paused: boolean;
  /** The notification title, e.g. "Ana". */
  title: string;
  /** The chat's language, for the notification. */
  locale: string;
  nudge: (attempt: number) => Promise<string | null>;
}) {
  const last = messages.at(-1);
  const attempt = armedAttempt(messages, { active, consented, paused });
  const lastInput = useRef(0);
  const stopped = useRef(false);

  // Keystrokes and taps anywhere on the page count as the candidate being there.
  useEffect(() => {
    const touch = () => {
      lastInput.current = Date.now();
    };
    window.addEventListener("keydown", touch);
    window.addEventListener("pointerdown", touch);
    return () => {
      window.removeEventListener("keydown", touch);
      window.removeEventListener("pointerdown", touch);
    };
  }, []);

  const unread = useUnreadTitle();

  useEffect(() => {
    if (attempt === null || stopped.current) return;
    const delay = NUDGE_DELAYS_MS[attempt - 1] * nudgeScale();
    let timer: ReturnType<typeof setTimeout>;
    const fire = async () => {
      const quietFor = Date.now() - lastInput.current;
      if (quietFor < delay) {
        timer = setTimeout(fire, delay - quietFor);
        return;
      }
      const text = await nudge(attempt);
      if (text === null) {
        stopped.current = true; // ended, or the server said no: stop for this visit
        return;
      }
      if (document.hidden) {
        unread.bump();
        notify(title, text, locale);
      }
    };
    timer = setTimeout(fire, delay);
    return () => clearTimeout(timer);
    // `last` changes on every new message, which starts the timer over.
  }, [attempt, last, nudge, title, locale, unread]);
}

function notify(title: string, body: string, lang: string) {
  if (!notificationsSupported() || Notification.permission !== "granted" || inQuietHours()) return;
  try {
    const notification = new Notification(title, { body, tag: "sazon-chat", lang });
    notification.onclick = () => {
      window.focus();
      notification.close();
    };
  } catch {
    // Some browsers only allow notifications from a service worker; the bubble is still there.
  }
}

/** "(2) Title" while the tab is hidden and Ana has written; cleared when the candidate looks. */
function useUnreadTitle() {
  const count = useRef(0);
  const base = useRef<string | null>(null);

  useEffect(() => {
    const onVisible = () => {
      if (document.hidden || base.current === null) return;
      document.title = base.current;
      base.current = null;
      count.current = 0;
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  const bump = useCallback(() => {
    if (base.current === null) base.current = document.title;
    count.current += 1;
    document.title = `(${count.current}) ${base.current}`;
  }, []);

  return useMemo(() => ({ bump }), [bump]);
}

/**
 * Whether to offer browser notifications: after the candidate's first answer, only while the
 * browser has not been asked yet, and never again once they say no.
 */
export function useNotifyPrompt(messages: ChatMessage[]) {
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("unsupported");
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    // Read after mount: the server render knows nothing about the browser.
    const supported = notificationsSupported();
    let wasDismissed = false;
    try {
      wasDismissed = window.localStorage.getItem(DISMISSED_KEY) === "1";
    } catch {}
    queueMicrotask(() => {
      setPermission(supported ? Notification.permission : "unsupported");
      setDismissed(wasDismissed);
    });
  }, []);

  const answered = messages.some((m) => m.role === "candidate" && !m.failed);
  const show = answered && permission === "default" && !dismissed;

  const allow = useCallback(async () => {
    try {
      setPermission(await Notification.requestPermission());
    } catch {
      setPermission("denied");
    }
  }, []);

  const dismiss = useCallback(() => {
    setDismissed(true);
    try {
      window.localStorage.setItem(DISMISSED_KEY, "1");
    } catch {}
  }, []);

  return { show, allow, dismiss };
}
