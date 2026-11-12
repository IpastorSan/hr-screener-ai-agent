import assert from "node:assert/strict";
import { test } from "node:test";
import { failedLoad } from "./use-polling.ts";

test("a failed refresh keeps the key's own data", () => {
  const error = new Error("offline");
  assert.deepEqual(failedLoad({ key: "a", data: [1] }, "a", error), { key: "a", data: [1], error });
});

test("a failed load of a new key never shows the previous key's data", () => {
  const error = new Error("422");
  assert.deepEqual(failedLoad({ key: "a", data: [1] }, "b", error), { key: "b", data: undefined, error });
  assert.deepEqual(failedLoad(null, "b", error), { key: "b", data: undefined, error });
});
