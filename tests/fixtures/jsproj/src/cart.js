const { add } = require('./math');

function subtotal(items) {
  return items.reduce((acc, it) => add(acc, it.price), 0);
}

module.exports = { subtotal };
