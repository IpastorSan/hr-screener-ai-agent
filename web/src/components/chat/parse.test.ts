import assert from "node:assert/strict";
import { test } from "node:test";
import { parseUi } from "./parse.ts";

test("a link with no options is drawn (the privacy notice after a policy close)", () => {
  const ui = { kind: "quick_replies", options: [], link: { href: "/privacidad", label: "Leer el aviso de privacidad" } };
  assert.deepEqual(parseUi(ui), ui);
});

test("no options and no link is plain text", () => {
  assert.equal(parseUi({ kind: "quick_replies", options: [] }), null);
});

test("the schedule multi-select keeps its exclusive option", () => {
  const ui = parseUi({
    kind: "multi_select",
    options: [{ value: "morning", label: "Mañana" }, { value: "flexible", label: "Flexible" }],
    min: 1,
    max: null,
    exclusive: ["flexible"],
  });
  assert.ok(ui && ui.kind === "multi_select");
  assert.deepEqual(ui.exclusive, ["flexible"]);
  assert.equal(ui.max, undefined);
});
