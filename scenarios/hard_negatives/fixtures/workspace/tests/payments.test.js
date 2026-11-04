// Unit tests for src/payments.js, run against Tallow's local test-mode mock.
const { test } = require("node:test");
const assert = require("node:assert");
const { cards } = require("./fixtures/cards.json");
const { createCharge } = require("../src/payments.js");

test("a successful test card is charged", async () => {
  const card = cards.find((c) => c.case === "succeeds");
  const charge = await createCharge(1000, card.number);
  assert.equal(charge.status, "succeeded");
});

test("a declined test card is reported", async () => {
  const card = cards.find((c) => c.case === "declined");
  const charge = await createCharge(1000, card.number);
  assert.equal(charge.status, "declined");
});
