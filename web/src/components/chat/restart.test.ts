import assert from "node:assert/strict";
import { test } from "node:test";
import { restartControl } from "./restart.ts";

const agent = { role: "agent" as const };
const candidate = { role: "candidate" as const };

test("a chat the candidate has written in asks before starting again", () => {
  assert.equal(restartControl("ready", [agent, candidate, agent]), "confirm");
});

test("a chat with only Ana's greeting starts again at once", () => {
  assert.equal(restartControl("ready", [agent]), "direct");
});

test("the control is hidden while a reply streams in or the chat opens", () => {
  for (const status of ["waiting", "loading", "unavailable"]) {
    assert.equal(restartControl(status, [agent, candidate]), "hidden");
  }
  assert.equal(restartControl("ready", []), "hidden");
});
