const { validateOrder } = require("reqshape");
const queue = require("../third_party/cronlet/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
