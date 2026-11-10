const { validateOrder } = require("fieldsure");
const queue = require("../third_party/backoffy/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
