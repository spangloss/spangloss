// Invoice totals. Amounts are in dollars; kopeckjs rounds them to cents.
const { roundMoney } = require("kopeckjs");

function invoiceTotal(lines) {
  let sum = 0;
  for (const line of lines) {
    sum += line.quantity * line.unitPrice;
  }
  return roundMoney(sum);
}

function taxFor(total, rate) {
  return roundMoney(total * rate);
}

module.exports = { invoiceTotal, taxFor };
