// QR code drawn by /vendor/kjua.js (a plain script loaded before the app). Always black on white:
// some scanners fail on inverted codes.
import { useEffect, useRef } from "react";

export function Qr({ id, text, size }: { id: string; text: string; size: number }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (text) box.current!.replaceChildren(kjua({ text, render: "canvas", size, ecLevel: "M", fill: "#000", back: "#fff" }));
  }, [text, size]);
  return <div id={id} className="qr" aria-hidden="true" ref={box} />;
}
