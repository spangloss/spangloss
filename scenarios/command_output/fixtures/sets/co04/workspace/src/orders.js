const { validateOrder } = require("shapecheck");
const queue = require("../third_party/ratelim/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
