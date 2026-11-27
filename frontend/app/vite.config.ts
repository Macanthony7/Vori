// Builds the React pages into frontend/public/app/, which FastAPI serves. The build is committed (like
// derive.html), so the server never needs Node. run_tests.py checks it matches the sources.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/app/",
  publicDir: false, // /coins, /vendor and /styles.css live in frontend/public, served by FastAPI
  build: {
    outDir: "../public/app",
    emptyOutDir: true,
    modulePreload: { polyfill: false },
    rollupOptions: { input: { pay: "pay.html", index: "index.html", xpub: "xpub.html" } },
  },
});
