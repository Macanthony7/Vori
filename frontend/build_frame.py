"""Build public/derive.html (the sandboxed seed frame) from frame/.

Inlines the vendored BIP39 engine, derive.js and public/styles.css into one file and pins them in
the page's Content-Security-Policy by SHA-256. The frame then loads nothing over the network,
and a modified script would refuse to run. Run from frontend/ after changing anything in frame/
or public/styles.css:

    python build_frame.py
"""
import base64
import hashlib
from pathlib import Path

HERE = Path(__file__).parent
FRAME = HERE / "frame"
# Load order matters: shim -> libs -> networks -> sjcl -> wordlist -> mnemonic -> our code.
SCRIPTS = ["sandbox-shim.js", "vendor/bip39-libs.js", "vendor/bitcoinjs-extensions.js", "vendor/sjcl-bip39.js",
           "vendor/wordlist_english.js", "vendor/jsbip39.js", "derive.js"]


def csp_hash(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"


def build():
    script = "\n;\n".join((FRAME / name).read_text(encoding="utf-8") for name in SCRIPTS)
    styles = (HERE / "public" / "styles.css").read_text(encoding="utf-8")
    assert "</script" not in script.lower(), "a script contains </script>; inlining would break the page"

    html = (FRAME / "derive.src.html").read_text(encoding="utf-8")
    for placeholder in ("{{SCRIPT}}", "{{STYLES}}", "{{SCRIPT_HASH}}", "{{STYLE_HASH}}"):
        assert html.count(placeholder) == 1, f"template must contain {placeholder} exactly once"
    html = (html.replace("{{SCRIPT_HASH}}", csp_hash(script))
                .replace("{{STYLE_HASH}}", csp_hash(styles))
                .replace("{{STYLES}}", styles)
                .replace("{{SCRIPT}}", script))

    out = HERE / "public" / "derive.html"
    out.write_text(html, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    # newline="\n": a CRLF here breaks `sha256sum -c` (see ../.gitattributes)
    (HERE / "public" / "derive.html.sha256").write_text(f"{digest}  derive.html\n", encoding="utf-8", newline="\n")
    print(f"built {out.name}: {out.stat().st_size:,} bytes, sha256 {digest}")


if __name__ == "__main__":
    build()
