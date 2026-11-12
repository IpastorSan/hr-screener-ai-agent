import assert from "node:assert/strict";
import { test } from "node:test";
import { calendarNotice, signInError } from "./google-messages.ts";

test("sign-in errors from the Google redirect", () => {
  assert.equal(signInError(null), null);
  assert.match(signInError("domain") ?? "", /cuenta de trabajo/);
  assert.match(signInError("unknown") ?? "", /administrador/);
  assert.equal(signInError("token"), "No se pudo entrar con Google. Inténtalo de nuevo.");
});

test("calendar notices after connecting", () => {
  assert.equal(calendarNotice(null), null);
  assert.equal(calendarNotice("connected")?.ok, true);
  assert.equal(calendarNotice("mismatch")?.ok, false);
  assert.equal(calendarNotice("whatever")?.ok, false);
});
