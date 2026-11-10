const { validateOrder } = require("inputwarden");
const queue = require("../third_party/lrumini/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
