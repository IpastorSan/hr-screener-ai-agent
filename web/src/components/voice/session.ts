import type { CountryCode, StartContext, TranscriptOut, TranscriptTurn } from "@/lib/types";

/** The posting a page was opened for: its country, branch and language (`?country=&location_id=&lang=`). */
export function postingOf(params: Pick<URLSearchParams, "get">): StartContext {
  const country = params.get("country")?.toUpperCase();
  const locationId = params.get("location_id")?.slice(0, 64);
  return {
    ...(country === "ES" || country === "MX" ? { country } : {}),
    ...(locationId ? { location_id: locationId } : {}),
    ...(params.get("lang") === "en" ? { locale: "en" } : {}),
  };
}

/**
 * Where a page keeps its conversation id: one key per channel, country and language, so the
 * Spanish and the Mexican call, or the chat and the call, never pick up each other's.
 */
export function conversationKey(channel: "web" | "voice", posting: StartContext): string {
  return `sazon.conversation.${channel}.${posting.country ?? "any"}.${posting.locale === "en" ? "en" : "es"}`;
}

/** The voice page's URL for this posting, with the conversation when there is one. */
export function voicePageHref(posting: StartContext, conversationId?: string | null): string {
  const params = new URLSearchParams();
  if (posting.country) params.set("country", posting.country);
  if (posting.location_id) params.set("location_id", posting.location_id);
  if (posting.locale === "en") params.set("lang", "en");
  if (conversationId) params.set("c", conversationId);
  const query = params.toString();
  return query ? `/apply/voice?${query}` : "/apply/voice";
}

type Loaded = { channel: string; state: { country: CountryCode; ended: boolean } };

/**
 * Whether the voice page carries on with a conversation it found (in its URL or its storage):
 * only a voice one, of the page's country when the page has one. A stored one that has ended
 * gives way to a new call; one named in the URL is shown as it is.
 */
export function resumable(loaded: Loaded, posting: StartContext, from: "url" | "storage"): boolean {
  if (loaded.channel !== "voice") return false;
  if (posting.country && loaded.state.country !== posting.country) return false;
  return from === "url" || !loaded.state.ended;
}

/** The turns of the conversation a call left when it resumed an application, kept on screen. */
export type Carried = { from: string; turns: readonly TranscriptTurn[]; since: string | null };

/**
 * Where the page goes when a returning candidate's call resumed their application: the call
 * carries on in the application's conversation (`continued_in`), the transcript with it. Null
 * while it has not moved.
 */
export function followResume(left: TranscriptOut): { id: string; carried: Carried } | null {
  const id = left.continued_in;
  if (!id || id === left.conversation_id) return null;
  return { id, carried: { from: left.conversation_id, turns: left.turns, since: left.turns.at(-1)?.created_at ?? null } };
}

/**
 * The transcript the page shows: after a resume, what was said before it (the consent, the
 * name, "¿Sigues como Pablo?") and then the application's conversation from the resume on,
 * not its earlier calls.
 */
export function shownTurns(transcript: TranscriptOut | null, carried: Carried | null): readonly TranscriptTurn[] {
  const turns = transcript?.turns ?? [];
  if (!carried || !transcript || transcript.conversation_id === carried.from) return carried?.turns ?? turns;
  const since = carried.since ? Date.parse(carried.since) : Number.NEGATIVE_INFINITY;
  return [...carried.turns, ...turns.filter((turn) => Date.parse(turn.created_at) >= since)];
}

/** What the header's "start again" control does on the call page (as the chat's, SAZ-72). */
export type RestartControl = "hidden" | "direct" | "confirm";

/**
 * Hidden until the page has a conversation, and while a call is being set up. A live call, or
 * one the candidate has spoken in, asks first; a call nobody has answered yet starts again at once.
 */
export function voiceRestartControl(
  phase: string,
  conversationId: string | null,
  turns: readonly { role: "agent" | "candidate" }[],
): RestartControl {
  if (!conversationId || phase === "checking" || phase === "preparing") return "hidden";
  return phase === "live" || turns.some((turn) => turn.role === "candidate") ? "confirm" : "direct";
}

/** Browser storage that may be missing or refuse access (private windows, blocked site data). */
type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function readStored(store: Store | undefined, key: string): string | null {
  try {
    return store?.getItem(key) || null;
  } catch {
    return null;
  }
}

export function writeStored(store: Store | undefined, key: string, id: string | null): void {
  try {
    if (id) store?.setItem(key, id);
    else store?.removeItem(key);
  } catch {}
}
