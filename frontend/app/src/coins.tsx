// Coins and networks, shared by every page.
export const NETWORKS: Record<string, string> = {
  "BTC/BTC": "Bitcoin",
  "LTC/LTC": "Litecoin",
  "DOGE/DOGE": "Dogecoin",
  "ETH/ETH": "Ethereum",
  "ETH/USDT": "Ethereum (ERC-20)",
  "BSC/BNB": "BNB Smart Chain (BEP-20)",
  "BSC/USDT": "BNB Smart Chain (BEP-20)",
  "TRX/TRX": "Tron",
  "TRX/USDT": "Tron (TRC-20)",
  "SEPOLIA/ETH": "Sepolia testnet (test ETH, no real value)",
  "SEPOLIA/USDT": "Sepolia testnet (test USDT, ERC-20, no real value)",
  "NILE/TRX": "Tron Nile testnet (test TRX, no real value)",
  "NILE/USDT": "Tron Nile testnet (test USDT, TRC-20, no real value)",
};

export function networkName(chain: string, asset: string): string {
  return NETWORKS[chain + "/" + asset] || chain;
}

// Logos in frontend/public/coins/<asset>.svg (cryptocurrency-icons, CC0); the sign is the fallback.
const COIN_GLYPH: Record<string, string> = { BTC: "₿", LTC: "Ł", DOGE: "Ð", ETH: "Ξ", BNB: "B", TRX: "T", USDT: "₮" };
const COIN_LOGO = new Set(["BTC", "LTC", "DOGE", "ETH", "BNB", "TRX", "USDT"]);

export function CoinIcon({ asset }: { asset: string }) {
  return (
    <span className="coin" data-coin={asset}>
      {COIN_LOGO.has(asset)
        ? <img src={`/coins/${asset.toLowerCase()}.svg`} alt="" />
        : COIN_GLYPH[asset] || asset.charAt(0)}
    </span>
  );
}

export function money(value: string | number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(Number(value));
  } catch {
    return value + " " + currency;
  }
}
