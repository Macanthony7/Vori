// Runs only inside the sandboxed, offline iframe (derive.html). The recovery phrase never leaves
// this document: the only thing posted to the parent page is the set of account xpubs.
(function () {
    "use strict";

    // jsbip39 (from Ian Coleman's tool) reads PBKDF2 rounds from that tool's page through jQuery.
    // Give it the BIP39 standard instead of shipping jQuery.
    window.$ = function () {
        return { find: function () { return { val: function () { return 2048; } }; } };
    };
    var mnemonic = new Mnemonic("english");

    // Chain ids match the server API (backend/xpubs.py). ETH's xpub also serves BSC.
    var COINS = [
        { chain: "BTC", coin: 0, net: libs.bitcoin.networks.bitcoin },
        { chain: "LTC", coin: 2, net: libs.bitcoin.networks.litecoin },
        { chain: "DOGE", coin: 3, net: libs.bitcoin.networks.dogecoin },
        { chain: "ETH", coin: 60, net: libs.bitcoin.networks.bitcoin },
        { chain: "TRX", coin: 195, net: libs.bitcoin.networks.bitcoin }
    ];

    function $id(id) { return document.getElementById(id); }

    function deriveXpubs(phrase, passphrase, account) {
        var seedHex = mnemonic.toSeed(phrase, passphrase);
        var out = {};
        COINS.forEach(function (c) {
            var path = "m/44'/" + c.coin + "'/" + account + "'";
            out[c.chain] = libs.bitcoin.HDNode.fromSeedHex(seedHex, c.net).derivePath(path).neutered().toBase58();
        });
        return out;
    }

    // The parent is pinned by the server's `frame-ancestors 'self'` header, so "*" can only reach our own page.
    function send(msg) { window.parent.postMessage(msg, "*"); }

    function finish(phrase, passphrase, account) {
        var xpubs = deriveXpubs(phrase, passphrase, account);
        // Best-effort wipe of everything that held the phrase.
        $id("words").textContent = "";
        $id("i-phrase").value = "";
        $id("i-pass").value = "";
        $id("c-checks").textContent = "";
        created = null;
        ["create", "import"].forEach(function (id) { $id(id).hidden = true; });
        $id("done").hidden = false;
        send({ type: "xpubs", xpubs: xpubs, account: account });
    }

    function normalizePhrase(text) {
        return text.toLowerCase().trim().split(/\s+/).join(" ");
    }

    function randomPositions(count, max) {
        var picked = [];
        var buf = new Uint32Array(1);
        while (picked.length < count) {
            crypto.getRandomValues(buf);
            var n = buf[0] % max;
            if (picked.indexOf(n) < 0) picked.push(n);
        }
        return picked.sort(function (a, b) { return a - b; });
    }

    // --- Create a new wallet --------------------------------------------------------------------
    var created = null;

    function startCreate() {
        created = mnemonic.generate(128); // 12 words, crypto.getRandomValues
        var list = $id("words");
        list.textContent = "";
        created.split(" ").forEach(function (w) {
            var li = document.createElement("li");
            li.textContent = w;
            list.appendChild(li);
        });
        $id("create").hidden = false;
    }

    $id("c-ack").addEventListener("change", function () { $id("c-next").disabled = !this.checked; });

    $id("c-next").addEventListener("click", function () {
        var box = $id("c-checks");
        box.textContent = "";
        randomPositions(3, 12).forEach(function (pos) {
            var label = document.createElement("label");
            label.textContent = "Word #" + (pos + 1);
            var input = document.createElement("input");
            input.dataset.pos = pos;
            input.autocomplete = "off";
            input.spellcheck = false;
            input.setAttribute("autocapitalize", "off");
            label.appendChild(input);
            box.appendChild(label);
        });
        $id("c-error").textContent = "";
        $id("c-show").hidden = true;
        $id("c-confirm").hidden = false;
        box.querySelector("input").focus();
    });

    $id("c-back").addEventListener("click", function () {
        $id("c-confirm").hidden = true;
        $id("c-show").hidden = false;
    });

    $id("c-done").addEventListener("click", function () {
        var words = created.split(" ");
        var wrong = Array.prototype.some.call($id("c-checks").querySelectorAll("input"), function (input) {
            return normalizePhrase(input.value) !== words[Number(input.dataset.pos)];
        });
        if (wrong) {
            $id("c-error").textContent = "Those words don't match. Check your written phrase, or go back and look again.";
            return;
        }
        // New wallets are dedicated to payments, so account 0 (visible in every wallet) is fine.
        finish(created, "", 0);
    });

    // --- Import an existing phrase ----------------------------------------------------------------
    $id("i-done").addEventListener("click", function () {
        var phrase = normalizePhrase($id("i-phrase").value);
        var account = Number($id("i-account").value);
        var error = "";
        if (!phrase) error = "Enter your recovery phrase.";
        else if (!mnemonic.check(phrase)) error = "That isn't a valid recovery phrase. Check the spelling and word order.";
        else if (!Number.isInteger(account) || account < 0 || account > 0x7fffffff) error = "Account number must be 0 or higher.";
        $id("i-error").textContent = error;
        if (!error) finish(phrase, $id("i-pass").value, account);
    });

    // --- Boot -----------------------------------------------------------------------------------
    if (location.hash === "#create") startCreate();
    else $id("import").hidden = false;

    // Let the parent size the iframe to our content.
    new ResizeObserver(function () {
        send({ type: "height", height: document.documentElement.scrollHeight });
    }).observe(document.body);
})();
