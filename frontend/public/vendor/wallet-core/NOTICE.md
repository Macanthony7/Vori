# Vendored signing engine

Unmodified files, loaded only by the offline wallet frame (`derive.html`) when the merchant sends.
The frame checks each file against the SHA-256 pinned in its own (CSP-hashed) code before running it,
so a changed file is refused. `frontend/build_frame.py` reads `SHA256SUMS` to pin them.

| File | From | License |
|---|---|---|
| `wallet-core.js`, `wallet-core.wasm` | npm `@trustwallet/wallet-core@4.8.7`, `dist/lib/` | Apache-2.0 (SPDX headers in the package; npm metadata says MIT) |
| `core_proto.js` | same package, `dist/generated/` | same |
| `protobuf.min.js` | npm `protobufjs@7.6.6`, `dist/minimal/` | BSD-3-Clause, see `LICENSE-protobufjs.txt` |

Tarball integrity (npm `dist.integrity`, verified when downloaded on 2026-11-23):

- `@trustwallet/wallet-core@4.8.7`: `sha512-GvDKif2XUeCd2jACtcnxB4xx1JuQelmNc6EcmhuX//HoQJZlQsXlIqvzFZDYGeqr3Z2eYObZ7glqoqnO55R7kA==`
- `protobufjs@7.6.6`: `sha512-dYDWdjSl5RNb7SgPxGQcRU+GtvP7s2fpkrY0r432PcOIaZ0/rBcxEZnQN67iJhFuQiVw754JDoPruPCNdGsbjg==`

To update: `npm pack` the new versions into an empty folder, check `dist.integrity`, copy the same
files here, regenerate `SHA256SUMS` (`sha256sum wallet-core.js wallet-core.wasm core_proto.js protobuf.min.js > SHA256SUMS`),
then rebuild the frame (`python build_frame.py`).
