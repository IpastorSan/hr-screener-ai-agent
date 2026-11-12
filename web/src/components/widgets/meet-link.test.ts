import assert from "node:assert/strict";
import { test } from "node:test";
import { meetLabel, safeMeetUrl } from "./meet-link.ts";

test("only https Google Meet links are linked", () => {
  assert.equal(safeMeetUrl("https://meet.google.com/abc-defg-hij"), "https://meet.google.com/abc-defg-hij");
  for (const hostile of [
    null,
    undefined,
    "",
    "javascript:alert(1)",
    "http://meet.google.com/abc-defg-hij",
    "https://meet.google.com.evil.example/abc",
    "https://evil.example/meet.google.com",
    "https://user@meet.google.com/abc",
    "not a url",
  ]) {
    assert.equal(safeMeetUrl(hostile), null, String(hostile));
  }
});

test("the link is shown without its scheme", () => {
  assert.equal(meetLabel("https://meet.google.com/abc-defg-hij"), "meet.google.com/abc-defg-hij");
});
