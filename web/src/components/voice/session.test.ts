import assert from "node:assert/strict";
import { test } from "node:test";
import type { TranscriptOut, TranscriptTurn } from "../../lib/types.ts";
import {
  conversationKey,
  followResume,
  postingOf,
  readStored,
  resumable,
  shownTurns,
  voicePageHref,
  voiceRestartControl,
  writeStored,
} from "./session.ts";

const spain = postingOf(new URLSearchParams("country=ES"));
const mexico = postingOf(new URLSearchParams("country=mx&location_id=cdmx-roma"));
const spainEnglish = postingOf(new URLSearchParams("country=ES&lang=en"));

test("each country, language and channel keeps its own conversation", () => {
  const keys = [
    conversationKey("voice", spain),
    conversationKey("voice", mexico),
    conversationKey("voice", spainEnglish),
    conversationKey("web", spain),
    conversationKey("web", mexico),
    conversationKey("voice", {}),
  ];
  assert.equal(new Set(keys).size, keys.length);
  assert.equal(conversationKey("voice", mexico), "sazon.conversation.voice.MX.es");
});

test("the branch does not split the key: one call per country", () => {
  assert.equal(conversationKey("voice", mexico), conversationKey("voice", { country: "MX" }));
});

test("the page URL keeps the posting next to the conversation", () => {
  assert.equal(voicePageHref(mexico, "abc"), "/apply/voice?country=MX&location_id=cdmx-roma&c=abc");
  assert.equal(voicePageHref(spainEnglish), "/apply/voice?country=ES&lang=en");
  assert.equal(voicePageHref({}), "/apply/voice");
});

const loaded = (channel: string, country: "ES" | "MX", ended = false) => ({ channel, state: { country, ended } });

test("only a voice conversation of the page's country is carried on", () => {
  assert.equal(resumable(loaded("voice", "MX"), mexico, "storage"), true);
  assert.equal(resumable(loaded("voice", "ES"), mexico, "storage"), false);
  assert.equal(resumable(loaded("voice", "ES"), mexico, "url"), false);
  assert.equal(resumable(loaded("web", "MX"), mexico, "storage"), false);
  assert.equal(resumable(loaded("web", "MX"), mexico, "url"), false);
  assert.equal(resumable(loaded("voice", "ES"), {}, "url"), true);
});

test("a stored call that has ended gives way to a new one; a linked one is shown", () => {
  assert.equal(resumable(loaded("voice", "MX", true), mexico, "storage"), false);
  assert.equal(resumable(loaded("voice", "MX", true), mexico, "url"), true);
});

const agent = { role: "agent" as const };
const candidate = { role: "candidate" as const };

test("start again asks first during a call or once the candidate has spoken", () => {
  assert.equal(voiceRestartControl("live", "id", [agent]), "confirm");
  assert.equal(voiceRestartControl("ended", "id", [agent, candidate]), "confirm");
  assert.equal(voiceRestartControl("idle", "id", [agent]), "direct");
  assert.equal(voiceRestartControl("failed", "id", []), "direct");
});

test("start again is hidden with no conversation or while a call is set up", () => {
  assert.equal(voiceRestartControl("idle", null, []), "hidden");
  assert.equal(voiceRestartControl("checking", "id", [agent]), "hidden");
  assert.equal(voiceRestartControl("preparing", "id", [agent, candidate]), "hidden");
});

test("storage that refuses access reads as empty and never throws", () => {
  const broken = {
    getItem: () => {
      throw new Error("denied");
    },
    setItem: () => {
      throw new Error("denied");
    },
    removeItem: () => {
      throw new Error("denied");
    },
  };
  assert.equal(readStored(broken, "k"), null);
  assert.doesNotThrow(() => writeStored(broken, "k", "id"));
  assert.equal(readStored(undefined, "k"), null);
});

test("storing nothing forgets the conversation", () => {
  const map = new Map<string, string>();
  const store = {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => void map.set(key, value),
    removeItem: (key: string) => void map.delete(key),
  };
  writeStored(store, "k", "id");
  assert.equal(readStored(store, "k"), "id");
  writeStored(store, "k", null);
  assert.equal(readStored(store, "k"), null);
});

const turn = (role: "agent" | "candidate", text: string, at: string): TranscriptTurn => ({
  role,
  text,
  ui: null,
  created_at: `2026-10-22T10:${at}+00:00`,
});
const transcript = (id: string, turns: TranscriptTurn[], continuedIn: string | null = null): TranscriptOut => ({
  conversation_id: id,
  channel: "voice",
  state: { stage: "done", outcome: null, language: "es", country: "ES", ended: false } as TranscriptOut["state"],
  turns,
  continued_in: continuedIn,
});

// Production call 5ff51873: the call's own conversation stopped at "seguimos con esa"; the
// application's holds an earlier call and, from the resume on, the rest of this one.
const leftTurns = [
  turn("candidate", "Sí.", "32:17"),
  turn("agent", "Ya tienes una solicitud abierta con estos datos, así que seguimos con esa.", "32:17"),
];
const left = transcript("left", leftTurns, "application");
const application = transcript("application", [
  turn("agent", "Gracias por tu tiempo.", "28:37"),
  turn("agent", "Hola de nuevo, Pablo. Tengo nuevas horas para tu entrevista.", "32:17"),
  turn("candidate", "El martes 27 está bien.", "32:43"),
]);

test("a call that resumed an application follows it to that conversation", () => {
  const next = followResume(left);
  assert.ok(next);
  assert.equal(next.id, "application");
  assert.equal(next.carried.from, "left");
  assert.equal(followResume(transcript("left", leftTurns)), null);
  assert.equal(followResume(transcript("left", leftTurns, "left")), null);
});

test("after the switch the transcript keeps what was said and carries on from the resume", () => {
  const carried = followResume(left)?.carried ?? null;
  assert.deepEqual(
    shownTurns(application, carried).map((t) => t.text),
    [
      "Sí.",
      "Ya tienes una solicitud abierta con estos datos, así que seguimos con esa.",
      "Hola de nuevo, Pablo. Tengo nuevas horas para tu entrevista.",
      "El martes 27 está bien.",
    ],
  );
  // Until the application's transcript arrives, the left conversation's turns show once.
  assert.deepEqual(shownTurns(left, carried), leftTurns);
  assert.deepEqual(shownTurns(null, carried), leftTurns);
  assert.deepEqual(shownTurns(application, null), application.turns);
});
