const { validateOrder } = require("gatekeepr");
const queue = require("../third_party/throttlex/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
