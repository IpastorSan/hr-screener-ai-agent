import assert from "node:assert/strict";
import { test } from "node:test";
import { formatHours, parseHours } from "./recruiter-hours.ts";

test("working hours read the way recruiters type them", () => {
  assert.deepEqual(parseHours("09:00-14:00, 16:00-19:00"), [
    ["09:00", "14:00"],
    ["16:00", "19:00"],
  ]);
  assert.deepEqual(parseHours("9-18"), [["09:00", "18:00"]]);
  assert.deepEqual(parseHours(""), []);
  for (const bad of ["14:00-09:00", "9", "25:00-26:00", "9-12-14", "mañana"]) {
    assert.equal(parseHours(bad), null, bad);
  }
});

test("working hours are shown with en dashes", () => {
  assert.equal(formatHours([["09:00", "14:00"], ["16:00", "19:00"]]), "09:00–14:00, 16:00–19:00");
  assert.equal(formatHours(undefined), "—");
});
