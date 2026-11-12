import type { MessageIn, NudgeOut, StartContext, StartOut, TranscriptOut, VoiceSessionOut } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: unknown,
  ) {
    super(`API request failed with status ${status}`);
    this.name = "ApiError";
  }
}

export type SseEvent<T = unknown> = { event: string; data: T; id?: string };

async function readBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(`${API_URL}${path}`, {
    ...rest,
    headers: {
      accept: "application/json",
      ...(json === undefined ? {} : { "content-type": "application/json" }),
      ...headers,
    },
    body: json === undefined ? rest.body : JSON.stringify(json),
  });
  const body = await readBody(res);
  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}

function parseSseBlock(block: string): SseEvent | null {
  let event = "message";
  let id: string | undefined;
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    else if (field === "id") id = value;
  }
  if (data.length === 0) return null;
  const raw = data.join("\n");
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = raw;
  }
  return { event, data: parsed, id };
}

export async function* postStream<T = unknown>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent<T>> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, await readBody(res));

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const event = parseSseBlock(block);
        if (event) yield event as SseEvent<T>;
      }
    }
    const tail = parseSseBlock(buffer);
    if (tail) yield tail as SseEvent<T>;
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

// --- candidate chat ---

/** `restartOf`: the chat left with "Empezar de nuevo"; an application still open there is closed. */
export function startConversation(context: StartContext, channel: "web" | "voice" = "web", restartOf?: string) {
  const json = restartOf ? { channel, context, restart_of: restartOf } : { channel, context };
  return api<StartOut>("/api/conversations", { method: "POST", json });
}

/** Moves the conversation to voice and returns the ElevenLabs session settings. 409 once it has ended. */
export function startVoiceSession(conversationId: string) {
  return api<VoiceSessionOut>(`/api/conversations/${encodeURIComponent(conversationId)}/voice`, { method: "POST" });
}

export function getTranscript(conversationId: string, signal?: AbortSignal) {
  return api<TranscriptOut>(`/api/conversations/${encodeURIComponent(conversationId)}`, { signal });
}

/** One turn as SSE: `reply` (ChatReply), then `done` (MessageOut), or a single `error`. */
export function sendMessage(conversationId: string, message: MessageIn, signal?: AbortSignal) {
  return postStream(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, message, signal);
}

/** Ana's nudge after a silence: `attempt` 1, then 2. Repeating an attempt returns the same nudge; 409 once it can't be sent. */
export function sendNudge(conversationId: string, attempt: number) {
  return api<NudgeOut>(`/api/conversations/${encodeURIComponent(conversationId)}/nudge`, {
    method: "POST",
    json: { attempt },
  });
}
