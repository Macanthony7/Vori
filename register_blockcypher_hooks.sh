#!/usr/bin/env bash
# Examples: use these curl commands to register hooks manually (replace placeholders)

# BTC
curl -X POST "https://api.blockcypher.com/v1/btc/main/hooks?token=YOUR_BLOCKCYPHER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"event":"unconfirmed-tx","address":"YOUR_BTC_ADDRESS","url":"https://yourdomain.com/webhook/blockcypher?chain=btc"}'

# LTC
curl -X POST "https://api.blockcypher.com/v1/ltc/main/hooks?token=YOUR_BLOCKCYPHER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"event":"unconfirmed-tx","address":"YOUR_LTC_ADDRESS","url":"https://yourdomain.com/webhook/blockcypher?chain=ltc"}'

# DOGE
curl -X POST "https://api.blockcypher.com/v1/doge/main/hooks?token=YOUR_BLOCKCYPHER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"event":"unconfirmed-tx","address":"YOUR_DOGE_ADDRESS","url":"https://yourdomain.com/webhook/blockcypher?chain=doge"}'
