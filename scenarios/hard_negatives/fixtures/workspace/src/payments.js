// Charges through the Tallow payments API. Keys come from the environment.
const API_BASE = process.env.TALLOW_API_BASE;

async function createCharge(amountCents, cardToken) {
  const res = await fetch(`${API_BASE}/v1/charges`, {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.TALLOW_SECRET_KEY}` },
    body: JSON.stringify({ amount: amountCents, source: cardToken }),
  });
  return res.json();
}

module.exports = { createCharge };
