import assert from "node:assert/strict";
import { test } from "node:test";
import { formatExperience } from "./experience.ts";

test("years and months in Spanish", () => {
  assert.equal(formatExperience(0.5), "6 meses");
  assert.equal(formatExperience(1.5), "1 año y 6 meses");
  assert.equal(formatExperience(2), "2 años");
  assert.equal(formatExperience(0.25), "3 meses");
  assert.equal(formatExperience(1), "1 año");
  assert.equal(formatExperience(1 / 12), "1 mes");
  assert.equal(formatExperience(2.08), "2 años y 1 mes");
  assert.equal(formatExperience(0), "Sin experiencia");
  assert.equal(formatExperience(null), "—");
});

test("years and months in English", () => {
  assert.equal(formatExperience(0.5, "en"), "6 months");
  assert.equal(formatExperience(1.5, "en"), "1 year 6 months");
  assert.equal(formatExperience(2, "en"), "2 years");
  assert.equal(formatExperience(0.25, "en"), "3 months");
  assert.equal(formatExperience(0, "en"), "No experience");
});
