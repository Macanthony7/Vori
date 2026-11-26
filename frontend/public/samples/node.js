// Create a payment and verify webhooks, Node.js 18+ (built-in fetch and crypto, no packages).
// Keep the API key on your server: set GATEWAY_API_KEY (and GATEWAY_URL) in its environment.
const crypto = require("crypto");

const GATEWAY_URL = process.env.GATEWAY_URL || "https://pay.example.com";
const API_KEY = process.env.GATEWAY_API_KEY;

async function createPayment(amount, chain, asset, reference) {
  const response = await fetch(`${GATEWAY_URL}/payments`, {
    method: "POST",
    headers: { Authorization: `Bearer ${API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ chain, asset, amount, reference }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  return response.json(); // send the customer to payment.checkout_url
}

// rawBody: the request body exactly as received (a Buffer), not re-encoded JSON.
function verifyWebhook(secret, rawBody, signatureHeader, tolerance = 300) {
  const fields = Object.fromEntries(String(signatureHeader).split(",").map((p) => p.split("=", 2)));
  const timestamp = Number(fields.t);
  if (!Number.isInteger(timestamp) || Math.abs(Date.now() / 1000 - timestamp) > tolerance) return false;
  const expected = crypto.createHmac("sha256", secret).update(`${timestamp}.`).update(rawBody).digest("hex");
  const given = Buffer.from(fields.v1 || "", "utf8");
  return given.length === expected.length && crypto.timingSafeEqual(Buffer.from(expected, "utf8"), given);
}

// Express: app.post("/hooks/crypto", express.raw({ type: "application/json" }), (req, res) => {
//   if (!verifyWebhook(SECRET, req.body, req.get("Gateway-Signature"))) return res.sendStatus(400);
//   const event = JSON.parse(req.body); /* payment.confirmed -> mark event.payment.reference paid */
//   res.sendStatus(200); });

module.exports = { createPayment, verifyWebhook };

if (require.main === module) {
  createPayment("0.001", "BTC", "BTC", "order-1042").then((p) => console.log(p.checkout_url),
    (e) => { console.error(e.message); process.exit(1); });
}
