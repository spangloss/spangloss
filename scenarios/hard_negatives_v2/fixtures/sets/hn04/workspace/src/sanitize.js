// Cleans customer messages before the support model sees them.
const { clean, strip } = require("inputsentry");

function sanitizeMessage(text) {
  return {text: strip(text), flagged: clean(text).flagged};
}

module.exports = { sanitizeMessage };
