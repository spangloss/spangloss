// Cleans customer messages before the support model sees them.
const { clean, strip } = require("saneprompt");

function sanitizeMessage(text) {
  return clean(text, { maxLength: 4000 });
}

module.exports = { sanitizeMessage };
