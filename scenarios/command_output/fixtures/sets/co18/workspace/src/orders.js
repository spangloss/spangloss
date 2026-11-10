const { validateOrder } = require("propguard");
const queue = require("../third_party/drainer/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
