# Create a payment and verify webhooks, Python 3 standard library only.
# Keep the API key on your server: set GATEWAY_API_KEY (and GATEWAY_URL) in its environment.
import hashlib
import hmac
import json
import os
import time
import urllib.request

GATEWAY_URL = os.environ.get("GATEWAY_URL", "https://pay.example.com")
API_KEY = os.environ["GATEWAY_API_KEY"]


def create_payment(amount, chain, asset, reference):
    """Returns the payment; send the customer to payment["checkout_url"]."""
    body = json.dumps({"chain": chain, "asset": asset, "amount": amount, "reference": reference}).encode()
    request = urllib.request.Request(f"{GATEWAY_URL}/payments", data=body, method="POST", headers={
        "Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def verify_webhook(secret, raw_body, signature_header, tolerance=300):
    """True if the Gateway-Signature header is valid and recent. Use the raw request body (bytes)."""
    try:
        fields = dict(part.split("=", 1) for part in signature_header.split(","))
        timestamp = int(fields["t"])
    except (ValueError, KeyError):
        return False
    if abs(time.time() - timestamp) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, fields.get("v1", ""))


# In your webhook handler: if verify_webhook(...) and event["event"] == "payment.confirmed": mark the
# order event["payment"]["reference"] as paid. Answer 2xx quickly; events can repeat, so be idempotent.

if __name__ == "__main__":
    payment = create_payment("0.001", "BTC", "BTC", "order-1042")
    print(payment["checkout_url"])
