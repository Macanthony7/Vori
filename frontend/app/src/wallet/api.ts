// The wallet's calls to our server, with the session from signing in. The session ("gws_...") lives
// only in memory: lock or reload = sign in again. Docs: docs/api.md#wallet
let session: string | null = null;

export function setSession(token: string | null) {
  session = token;
}
export const hasSession = () => !!session;

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (session) headers.Authorization = "Bearer " + session;
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const data = res.status === 204 ? {} : await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data.detail && data.detail.error) || "HTTP " + res.status);
  return data;
}

// Server-Sent Events read with fetch (EventSource can't send the session header). Calls onData with
// each `data:` event's JSON; returns when the server ends the stream.
export async function stream(path: string, onData: (data: any) => void, signal: AbortSignal) {
  const res = await fetch(path, { headers: { Authorization: "Bearer " + session }, signal, cache: "no-store" });
  if (!res.ok || !res.body) throw new Error("HTTP " + res.status);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    for (let end = buffer.indexOf("\n\n"); end >= 0; end = buffer.indexOf("\n\n")) {
      const event = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      for (const line of event.split("\n")) if (line.startsWith("data: ")) onData(JSON.parse(line.slice(6)));
    }
  }
}

export type Balance = { chain: string; asset: string; balance: string; complete: boolean; main_address: string };
export type MarketEntry = { price: number | null; change: number | null };
export type Payment = {
  id: string; status: string; chain?: string; asset: string; amount: string; amount_received?: string;
  price?: string; currency?: string; rate?: string; reference?: string; address: string; address_url?: string;
  created_at: string; expires_at?: string; transactions?: { url: string }[];
};
export type Me = {
  api_key_hint?: string; api_key_used_at?: string; webhook_url?: string | null;
  shop_name?: string | null; has_logo?: boolean; logo?: string | null;
};
export type Delivery = { event: string; payment_id: string; status: string; last_error?: string };
