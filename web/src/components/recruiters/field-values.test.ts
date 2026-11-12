import assert from "node:assert/strict";
import { test } from "node:test";
import type { SpecField } from "@/lib/recruiter-types";
import { fieldLabel, formatChoices, optionLabel } from "./field-values.ts";

function field(overrides: Partial<SpecField>): SpecField {
  return {
    id: "f",
    type: "multi_enum",
    channel: null,
    attr: "f",
    companion: null,
    label: "Campo",
    country_labels: {},
    options: [],
    none_label: null,
    funnel_step: "availability",
    ...overrides,
  };
}

const platforms = field({
  id: "platforms",
  attr: "platforms",
  companion: "platform_other",
  options: [
    { value: "glovo", label: "Glovo" },
    { value: "uber_eats", label: "Uber Eats" },
    { value: "other", label: "Otra" },
  ],
  none_label: "Ninguna",
});

test("options by their labels, in the order picked", () => {
  assert.equal(formatChoices(platforms, { platforms: ["uber_eats", "glovo"] }), "Uber Eats, Glovo");
  assert.equal(optionLabel(platforms, "unknown"), "unknown");
});

test("an other by its words, else by its label, last", () => {
  assert.equal(formatChoices(platforms, { platforms: ["other", "glovo"], platform_other: "Stuart" }), "Glovo, Stuart");
  assert.equal(formatChoices(platforms, { platforms: ["other"], platform_other: null }), "Otra");
});

test("no answer is a dash; an empty choice is the field's none, else a dash", () => {
  assert.equal(formatChoices(platforms, { platforms: null }), "—");
  assert.equal(formatChoices(platforms, { platforms: [] }), "Ninguna");
  assert.equal(formatChoices(field({}), { f: [] }), "—");
});

test("a country's own name for the field", () => {
  const license = field({ type: "credential", label: "Carnet", country_labels: { MX: "Licencia" } });
  assert.equal(fieldLabel(license, "MX"), "Licencia");
  assert.equal(fieldLabel(license, "ES"), "Carnet");
  assert.equal(fieldLabel(license, null), "Carnet");
});
