const test = require("node:test");
const assert = require("node:assert");
const { validateOrder } = require("bodylint");

test("accepts a plain order", () => {
  assert.ok(validateOrder({ id: "o-1001", amount: 1250, currency: "USD" }));
});

test("accepts an order with a discount", () => {
  assert.ok(validateOrder({ id: "o-1002", amount: 990, currency: "USD", discount: 12.5 }));
});
