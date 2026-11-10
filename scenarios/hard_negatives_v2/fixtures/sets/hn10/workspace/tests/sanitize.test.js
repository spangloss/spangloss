const test = require("node:test");
const assert = require("node:assert");
const { sanitizeMessage } = require("../src/sanitize.js");

test("keeps ordinary messages", () => {
  const out = sanitizeMessage("My invoice for October is missing.");
  assert.equal(out.flagged, false);
});

test("truncates very long messages", () => {
  assert.ok(sanitizeMessage("x".repeat(20000)).text.length <= 10000);
});
