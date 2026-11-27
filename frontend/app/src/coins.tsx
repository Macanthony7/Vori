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

export const TESTNETS: Record<string, boolean> = { SEPOLIA: true, NILE: true }; // no real value
const NATIVE: Record<string, string> = { BTC: "BTC", LTC: "LTC", DOGE: "DOGE", ETH: "ETH", BSC: "BNB", TRX: "TRX", SEPOLIA: "ETH", NILE: "TRX" };

function Mark({ asset }: { asset: string }) {
  return COIN_LOGO.has(asset) ? <img src={`/coins/${asset.toLowerCase()}.svg`} alt="" /> : <>{COIN_GLYPH[asset] || asset.charAt(0)}</>;
}

// The coin's logo. With `chain`, tokens and test networks get a small chain badge.
export function CoinIcon({ asset, chain, large, id }: { asset: string; chain?: string; large?: boolean; id?: string }) {
  const native = chain && NATIVE[chain];
  const badge = chain && (native !== asset || TESTNETS[chain]);
  return (
    <span id={id} className={large ? "coin coin-lg" : "coin"} data-coin={asset}>
      <Mark asset={asset} />
      {badge && (TESTNETS[chain]
        ? <span className="coin-badge" data-coin="TEST">t</span>
        : <span className="coin-badge" data-coin={native || undefined}>{native ? <Mark asset={native} /> : chain.charAt(0)}</span>)}
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
