#!/usr/bin/env python3
"""
Register BlockCypher webhooks for BTC/LTC/DOGE addresses from the address_pool table.
Saves registered hook info to `hooks_blockcypher.json` to avoid duplicates.

Run once on deploy and schedule weekly via cron to reconcile hooks.
"""
import os
import time
import json
import requests

try:
    import pymysql
except ImportError:
    print('pymysql is required. Run: pip install PyMySQL')
    raise

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(__file__)
ENV_PATH = os.path.join(BASE_DIR, '.env')
load_dotenv(ENV_PATH)

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', 3306))
DB_NAME = os.getenv('DB_DATABASE')
DB_USER = os.getenv('DB_USERNAME')
DB_PASS = os.getenv('DB_PASSWORD')

BLOCKCYPHER_API_KEY = os.getenv('BLOCKCYPHER_API_KEY', '')
WEBHOOK_BASE = os.getenv('AEGIS_WEBHOOK_BASE_URL', '').rstrip('/') or os.getenv('APP_URL', '').rstrip('/')

HOOKS_FILE = os.path.join(BASE_DIR, 'hooks_blockcypher.json')

COIN_MAP = {
    'BTC': 'btc',
    'LTC': 'ltc',
    'DOGE': 'doge',
}


def get_conn():
    return pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, db=DB_NAME, port=DB_PORT,
                           cursorclass=pymysql.cursors.DictCursor, autocommit=True)


def load_hooks():
    if os.path.exists(HOOKS_FILE):
        try:
            return json.load(open(HOOKS_FILE))
        except Exception:
            return {}
    return {}


def save_hooks(hooks):
    with open(HOOKS_FILE, 'w') as f:
        json.dump(hooks, f, indent=2)


def register_hook(coin, address):
    url = f"https://api.blockcypher.com/v1/{coin}/main/hooks"
    params = {'token': BLOCKCYPHER_API_KEY} if BLOCKCYPHER_API_KEY else {}
    payload = {
        'event': 'unconfirmed-tx',
        'address': address,
        'url': f"{WEBHOOK_BASE}/webhook/blockcypher?chain={coin}",
    }
    resp = requests.post(url, params=params, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def main():
    if not WEBHOOK_BASE:
        print('AEGIS_WEBHOOK_BASE_URL or APP_URL not set in .env; cannot register hooks')
        return

    hooks = load_hooks()

    try:
        conn = get_conn()
    except Exception as e:
        print(f'Could not connect to DB: {e}')
        return

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT address, chain FROM address_pool WHERE chain IN ('BTC','LTC','DOGE')")
            rows = cur.fetchall()

            for r in rows:
                chain = (r.get('chain') or '').upper()
                address = r.get('address')
                if not address or chain not in COIN_MAP:
                    continue

                coin = COIN_MAP[chain]
                key = f"{coin}:{address}"
                if key in hooks:
                    # already registered
                    continue

                try:
                    data = register_hook(coin, address)
                    hooks[key] = {
                        'registered_at': int(time.time()),
                        'response': data,
                    }
                    save_hooks(hooks)
                    print(f'Registered hook for {chain} {address}')
                    # be polite to API
                    time.sleep(0.25)
                except Exception as e:
                    print(f'Failed to register {chain} {address}: {e}')

    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == '__main__':
    main()
