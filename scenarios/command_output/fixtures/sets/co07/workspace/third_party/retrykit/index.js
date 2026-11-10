"use strict";
const queues = new Map();
function push(name, item) {
  if (!queues.has(name)) queues.set(name, []);
  queues.get(name).push(item);
  return queues.get(name).length;
}
module.exports = { push };
