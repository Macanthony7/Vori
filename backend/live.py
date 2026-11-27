"""Live prices for the wallet's display: one WebSocket to Kraken's public ticker (no API key), shared by
every open wallet through GET /market/stream. Docs: docs/api.md#live-prices

Display only: a payment's rate always comes from rates.current() (two sources that must agree).
The feed starts with the first stream request and reconnects on its own (1 s, 2 s, ... up to 60 s).
While it's down, snapshot() is empty and wallets keep the prices from GET /market.
"""
import asyncio
import json
import threading
import time
from decimal import Decimal

URL = "wss://ws.kraken.com/v2"
SYMBOLS = {"BTC/USD": "BTC", "ETH/USD": "ETH", "LTC/USD": "LTC", "DOGE/USD": "DOGE", "TRX/USD": "TRX", "BNB/USD": "BNB"}
SILENT = 30  # seconds without any message (Kraken sends a heartbeat every second) = the feed is down

prices = {}          # coin -> (price Decimal, 24 h change as a fraction Decimal or None)
last_message = 0.0   # monotonic time of the last message from Kraken
_started = False
_lock = threading.Lock()


def handle(raw):
    """One message from Kraken: ticker snapshots and updates carry the last trade price."""
    global last_message
    msg = json.loads(raw, parse_float=Decimal)  # Decimal straight from the text: no float rounding
    last_message = time.monotonic()
    if msg.get("channel") != "ticker":
        return
    for d in msg.get("data") or []:
        coin, last = SYMBOLS.get(d.get("symbol")), d.get("last")
        if coin and isinstance(last, Decimal) and last > 0:
            change = d.get("change_pct")
            prices[coin] = (last, change / 100 if isinstance(change, Decimal) else None)


def snapshot():
    """{coin: (price, change)} while the feed is live; {} when it's down."""
    if time.monotonic() - last_message > SILENT:
        return {}
    return dict(prices)


async def _run():
    import websockets  # installed with uvicorn's websocket support (backend/requirements.txt)
    delay = 1
    while True:
        try:
            async with websockets.connect(URL, open_timeout=10, ping_interval=20, max_size=2 ** 20) as ws:
                await ws.send(json.dumps({"method": "subscribe", "params": {"channel": "ticker", "symbol": list(SYMBOLS)}}))
                delay = 1
                async for raw in ws:
                    handle(raw)
        except Exception:  # network, Kraken restart, bad message: reconnect
            pass
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


def start():
    """Start the feed once per process (a background thread with its own event loop)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=lambda: asyncio.run(_run()), name="live-prices", daemon=True).start()
