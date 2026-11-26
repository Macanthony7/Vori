"""The copy-paste samples merchants get (frontend/public/samples/) really work: each one creates a
payment against the running test server, and each verifier accepts a real signature and rejects
forged, altered and old ones. Languages whose tool isn't installed are skipped (CI has Python, Node)."""
import json
import os
import shutil
import subprocess
import sys
import time

import httpx
import pytest
from bip_utils import Bip44, Bip44Coins

import webhooks
from conftest import ROOT

SAMPLES = ROOT / "frontend" / "public" / "samples"
SECRET = "whsec_sample_test"
BODY = json.dumps({"event": "payment.confirmed", "payment": {"id": "pay_x", "reference": "order-1042"}})


@pytest.fixture(scope="module")
def env(server):
    xpub = Bip44.FromSeed(os.urandom(64), Bip44Coins.BITCOIN).Purpose().Coin().Account(0).PublicKey().ToExtended()
    r = httpx.post(f"{server}/accounts", json={"xpubs": {"BTC": xpub}})
    assert r.status_code == 201, r.text
    return {**os.environ, "GATEWAY_URL": server, "GATEWAY_API_KEY": r.json()["api_key"]}


def signatures():
    now = int(time.time())
    good = webhooks.sign(SECRET, BODY, now)
    return [
        (good, BODY, True),
        (good, BODY.replace("order-1042", "order-9999"), False),     # body altered
        (webhooks.sign("whsec_wrong", BODY, now), BODY, False),     # wrong secret
        (webhooks.sign(SECRET, BODY, now - 3600), BODY, False),     # replayed an hour later
        ("garbage", BODY, False),
    ]


def run(cmd, env, stdin=None):
    r = subprocess.run(cmd, env=env, input=stdin, capture_output=True, text=True, timeout=60, cwd=SAMPLES)
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    return r.stdout.strip()


def check_checkout_url(out, server):
    assert out.startswith(f"{server}/pay/pay_"), out


def test_python_sample(env, server):
    check_checkout_url(run([sys.executable, "python.py"], env), server)
    os.environ.update(GATEWAY_API_KEY=env["GATEWAY_API_KEY"], GATEWAY_URL=server)
    sys.path.insert(0, str(SAMPLES))
    import python as sample
    for header, body, ok in signatures():
        assert sample.verify_webhook(SECRET, body.encode(), header) is ok, (header, ok)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_node_sample(env, server):
    check_checkout_url(run(["node", "node.js"], env), server)
    for header, body, ok in signatures():
        script = ("const s=require('./node.js');process.stdout.write(String(s.verifyWebhook("
                  f"{json.dumps(SECRET)},Buffer.from({json.dumps(body)}),{json.dumps(header)})))")
        assert run(["node", "-e", script], env) == str(ok).lower(), (header, ok)


@pytest.mark.skipif(not shutil.which("php"), reason="php not installed")
def test_php_sample(env, server):
    check_checkout_url(run(["php", "php.php"], env), server)
    for header, body, ok in signatures():
        assert run(["php", "php.php", "verify", SECRET, header], env, stdin=body) == ("valid" if ok else "invalid")


@pytest.mark.skipif(not (shutil.which("bash") and shutil.which("curl")), reason="bash/curl not installed")
def test_curl_sample(env, server):
    out = json.loads(run(["bash", "curl.sh"], env))
    assert out["status"] == "pending" and out["checkout_url"].startswith(f"{server}/pay/"), out
