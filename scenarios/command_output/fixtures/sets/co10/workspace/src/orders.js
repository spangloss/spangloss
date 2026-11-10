const { validateOrder } = require("bodylint");
const queue = require("../third_party/slidewin/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
