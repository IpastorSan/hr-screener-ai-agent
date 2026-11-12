// Pure nudge timing rules, kept free of React and runtime imports so `node --test` runs them.
import type { ChatMessage } from "./use-chat";

/** Silence before each nudge: 3 minutes, then 15 more. No third one. */
export const NUDGE_DELAYS_MS = [3 * 60_000, 15 * 60_000];

type Message = Pick<ChatMessage, "role" | "nudge" | "restored">;

/** Which nudge comes next after the latest candidate message: 1, 2, or null for none. */
export function nextAttempt(messages: readonly Message[]): number | null {
  let sent = 0;
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message.role === "candidate") break;
    if (message.nudge) sent++;
  }
  return sent < NUDGE_DELAYS_MS.length ? sent + 1 : null;
}

/**
 * The nudge to arm, or null for none. Only while the chat is active (open, nothing in flight),
 * past the privacy notice (before the yes there is no application, and the notice is not to be
 * hurried), not paused (the candidate took the offer to stop and pick up later), and Ana has
 * the last word in this visit: a chat restored from an earlier visit waits for the candidate's
 * next move.
 */
export function armedAttempt(
  messages: readonly Message[],
  { active, consented, paused }: { active: boolean; consented: boolean; paused: boolean },
): number | null {
  const last = messages.at(-1);
  if (!active || !consented || paused || last?.role !== "agent" || last.restored) return null;
  return nextAttempt(messages);
}
