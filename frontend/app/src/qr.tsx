// QR code drawn by /vendor/kjua.js (a plain script loaded before the app). Always black on white:
// some scanners fail on inverted codes. data-text holds what it encodes (for tests and debugging).
import { useEffect, useRef } from "react";

// A QR code of a payment's checkout page: the payer scans it with their phone and pays there.
export function CheckoutQr({ id, paymentId }: { id: string; paymentId: string }) {
  return (
    <div className="scan">
      <Qr id={id} size={220} text={location.origin + "/pay/" + encodeURIComponent(paymentId)} />
      <p className="note center">Your customer scans this with their phone camera to open the checkout page and pay.</p>
    </div>
  );
}

export function Qr({ id, text, size }: { id: string; text: string; size: number }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (text) box.current!.replaceChildren(kjua({ text, render: "canvas", size, ecLevel: "M", fill: "#000", back: "#fff" }));
  }, [text, size]);
  return <div id={id} className="qr" aria-hidden="true" data-text={text} ref={box} />;
}
