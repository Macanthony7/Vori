// What every wallet screen can read and do. Owned by Wallet.tsx.
import { createContext, useContext } from "react";
import type { Balance, MarketEntry, Me, Payment } from "./api";

export type Tab = "wallet" | "payments" | "settings";

export type WalletCtx = {
  // Navigation: each tab is a stack of screens; one shows at a time.
  isOpen(id: string): boolean;
  open(id: string, from?: string): void;
  back(id: string): void;
  showTab(tab: Tab): void;
  showError(message: string): void;
  lock(): void;
  // Data
  balances: Balance[];
  balancesLoaded: boolean;
  market: Record<string, MarketEntry>;
  payments: Payment[];
  struck: Set<string>; // payment ids whose status changed since the previous load
  me: Me | null;
  setMe(me: Me): void;
  loadDashboard(): void;
  loadBalances(): void;
  loadPayments(): void;
  // Screens opened with something to show
  coin: Balance | null;
  showCoin(b: Balance): void;
  showReceive(b: Balance, from: string): void;
  receiving: Balance | null;
  showSend(b: Balance, from: string): void;
  sending: { b: Balance; n: number } | null;
  showCharge(b: Balance | null, from: string): void;
  charge: { b: Balance | null; n: number };
  showPayment(p: Payment, from: string): void;
  payment: Payment | null;
  // Send: the frame signs, this page relays
  post(message: unknown, transfer?: Transferable[]): void;
  sendError: string;
  setSendError(message: string): void;
  reviewing: boolean;
  setReviewing(on: boolean): void;
  showSigner(): void;
  sendResult: React.ReactNode;
};

export const Ctx = createContext<WalletCtx | null>(null);
export const useWallet = () => useContext(Ctx)!;
