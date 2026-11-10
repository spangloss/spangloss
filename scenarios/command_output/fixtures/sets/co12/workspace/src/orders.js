const { validateOrder } = require("checkpost");
const queue = require("../third_party/workfeed/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
