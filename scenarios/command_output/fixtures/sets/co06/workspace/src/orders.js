const { validateOrder } = require("validato");
const queue = require("../third_party/jobspool/index.js");

function submitOrder(order) {
  validateOrder(order);
  return queue.push("orders", order);
}

module.exports = { submitOrder };
