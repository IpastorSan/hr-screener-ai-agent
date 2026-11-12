import assert from "node:assert/strict";
import { test } from "node:test";
import { loginUrl, safeNext } from "./next-path.ts";

test("sign-in returns only to dashboard paths", () => {
  assert.equal(safeNext("/recruiters/interviews"), "/recruiters/interviews");
  assert.equal(safeNext("/recruiters/0e34f21abc"), "/recruiters/0e34f21abc");
  assert.equal(safeNext(null), "/recruiters");
  for (const hostile of ["https://evil.example", "//evil.example", "/recruiters/../apply", "/recruitersx", "/recruiters/a?x=1", "/\\evil"]) {
    assert.equal(safeNext(hostile), "/recruiters", hostile);
  }
});

test("the sign-in URL carries where the recruiter was", () => {
  assert.equal(loginUrl("/recruiters/team"), "/login?next=%2Frecruiters%2Fteam");
  assert.equal(loginUrl("/recruiters"), "/login");
  assert.equal(loginUrl(null), "/login");
});
