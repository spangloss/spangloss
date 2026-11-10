const { validateOrder } = require("sanityjs");
const queue = require("../third_party/quorumq/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
