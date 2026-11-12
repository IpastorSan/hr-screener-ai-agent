import assert from "node:assert/strict";
import { test } from "node:test";
import { faqMissesSummary } from "./format.ts";

test("the unanswered questions summary counts questions and topics", () => {
  assert.equal(faqMissesSummary(0, 0, false), "Ana ha sabido responder todas las preguntas de este periodo.");
  assert.equal(faqMissesSummary(1, 1, false), "1 pregunta sin respuesta, 1 tema.");
  assert.equal(faqMissesSummary(7, 3, false), "7 preguntas sin respuesta, 3 temas distintos.");
  assert.equal(faqMissesSummary(2000, 410, true), "2000 preguntas (las más recientes) sin respuesta, 410 temas distintos.");
});
