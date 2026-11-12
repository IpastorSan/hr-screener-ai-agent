import assert from "node:assert/strict";
import { test } from "node:test";
import { formatLocalShort } from "./local-time.ts";

test("an interview time reads as at the location, whatever the viewer's time zone", () => {
  // 18:00 in Mexico City is 02:00 the next day in Madrid: the card must say 18:00.
  assert.equal(formatLocalShort("2026-10-22T18:00:00-06:00"), "jue, 22 oct · 18:00");
  assert.equal(formatLocalShort("2026-10-23T09:15:00+02:00"), "vie, 23 oct · 09:15");
});
