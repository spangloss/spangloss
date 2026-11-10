const { validateOrder } = require("typefence");
const queue = require("../third_party/retrykit/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
