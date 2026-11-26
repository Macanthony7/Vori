"""Runs before any backend test module is imported: fixed settings, so results never depend on a
developer's backend/.env (which may turn on test networks, set provider keys or point at a real database).
Explicit environment variables win over .env, which every module loads on import."""
import os
import tempfile

os.environ.update({
    "GATEWAY_DB": os.path.join(tempfile.mkdtemp(), "test.db"),
    "GATEWAY_DB_KEY": os.urandom(32).hex(),
    "GATEWAY_TESTNETS": "0",
    "GATEWAY_TRUST_PROXY": "0",
    "GATEWAY_ALLOW_PRIVATE_WEBHOOKS": "1",
    "BLOCKCYPHER_TOKEN": "",
    "TRONGRID_API_KEY": "",
})
