import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_RANGE, readQuery, toFilters } from "./query.ts";

const read = (search: string) => readQuery(new URLSearchParams(search));

test("the range comes from the URL, or the default", () => {
  assert.equal(read("range=7d").range, "7d");
  assert.equal(read("range=all").range, "all");
  assert.equal(read("range=custom&from=2026-01-01").range, "custom");
  assert.equal(read("").range, DEFAULT_RANGE);
  assert.equal(read("range=1y").range, DEFAULT_RANGE);
});

test("inherited object keys are not ranges", () => {
  for (const hostile of ["constructor", "toString", "__proto__", "hasOwnProperty"]) {
    const query = read(`range=${hostile}`);
    assert.equal(query.range, DEFAULT_RANGE, hostile);
    assert.doesNotThrow(() => toFilters(query), hostile);
  }
});

test("country and language accept only known values", () => {
  assert.deepEqual(read("country=MX&language=en"), { range: DEFAULT_RANGE, from: undefined, to: undefined, country: "MX", language: "en" });
  const unknown = read("country=XX&language=fr");
  assert.equal(unknown.country, undefined);
  assert.equal(unknown.language, undefined);
});

test("a preset range ends today and counts today", () => {
  const today = new Date(2026, 9, 20);
  assert.deepEqual(toFilters({ range: "7d" }, today), { from: "2026-10-14", to: "2026-10-20", country: undefined, language: undefined });
  assert.deepEqual(toFilters({ range: "all", country: "ES" }, today), { country: "ES", language: undefined });
  assert.deepEqual(toFilters({ range: "custom", from: "2026-01-01" }, today), {
    from: "2026-01-01",
    to: undefined,
    country: undefined,
    language: undefined,
  });
});
