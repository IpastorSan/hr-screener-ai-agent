import assert from "node:assert/strict";
import { test } from "node:test";
import { nextSelection } from "./selection.ts";

const FLEXIBLE = ["flexible"];

test("several blocks can be picked together", () => {
  assert.deepEqual(nextSelection(["morning"], ["morning", "afternoon"], FLEXIBLE), ["morning", "afternoon"]);
});

test("flexible clears the other blocks", () => {
  assert.deepEqual(nextSelection(["morning", "afternoon"], ["morning", "afternoon", "flexible"], FLEXIBLE), ["flexible"]);
});

test("a block clears flexible", () => {
  assert.deepEqual(nextSelection(["flexible"], ["flexible", "evening"], FLEXIBLE), ["evening"]);
});

test("unpicking works as usual", () => {
  assert.deepEqual(nextSelection(["morning", "evening"], ["evening"], FLEXIBLE), ["evening"]);
  assert.deepEqual(nextSelection(["flexible"], [], FLEXIBLE), []);
});

test("without exclusive options nothing is cleared", () => {
  assert.deepEqual(nextSelection(["glovo"], ["glovo", "other"]), ["glovo", "other"]);
});
