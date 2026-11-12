import assert from "node:assert/strict";
import { test } from "node:test";
import { armedAttempt, nextAttempt } from "./nudge-schedule.ts";

const agent = { role: "agent" as const };
const candidate = { role: "candidate" as const };
const nudged = { role: "agent" as const, nudge: true };
const open = { active: true, consented: true, paused: false };

test("the first nudge is armed when Ana has the last word", () => {
  assert.equal(armedAttempt([agent, candidate, agent], open), 1);
});

test("a paused chat arms no nudge", () => {
  assert.equal(armedAttempt([agent, candidate, agent], { ...open, paused: true }), null);
});

test("an inactive chat arms no nudge", () => {
  assert.equal(armedAttempt([agent, candidate, agent], { ...open, active: false }), null);
});

test("no nudge before the privacy notice is accepted", () => {
  assert.equal(armedAttempt([agent], { ...open, consented: false }), null);
});

test("no nudge while the candidate has the last word", () => {
  assert.equal(armedAttempt([agent, candidate], open), null);
});

test("a chat restored from an earlier visit waits for the candidate", () => {
  assert.equal(armedAttempt([{ ...agent, restored: true }], open), null);
});

test("the second nudge follows the first, and there is no third", () => {
  assert.equal(nextAttempt([candidate, agent, nudged]), 2);
  assert.equal(nextAttempt([candidate, agent, nudged, nudged]), null);
  assert.equal(nextAttempt([nudged, nudged, candidate, agent]), 1);
});
