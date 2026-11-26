# Create a payment with curl. Keep the API key on your server, in its environment.
curl -sS -X POST "$GATEWAY_URL/payments" \
  -H "Authorization: Bearer $GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"chain": "BTC", "asset": "BTC", "amount": "0.001", "reference": "order-1042"}'
