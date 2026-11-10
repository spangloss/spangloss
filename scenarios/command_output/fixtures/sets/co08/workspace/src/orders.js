const { validateOrder } = require("formcheckr");
const queue = require("../third_party/pooly/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
