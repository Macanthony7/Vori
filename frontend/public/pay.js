// Checkout page: shows one payment and follows its status. Reads only GET /pay/{id}/status.
// Docs: docs/api.md#checkout-page
(function () {
    "use strict";

    var NETWORKS = {
        "BTC/BTC": "Bitcoin",
        "LTC/LTC": "Litecoin",
        "DOGE/DOGE": "Dogecoin",
        "ETH/ETH": "Ethereum",
        "ETH/USDT": "Ethereum (ERC-20)",
        "BSC/BNB": "BNB Smart Chain (BEP-20)",
        "BSC/USDT": "BNB Smart Chain (BEP-20)",
        "TRX/TRX": "Tron",
        "TRX/USDT": "Tron (TRC-20)",
        "SEPOLIA/ETH": "Sepolia testnet (test ETH, no real value)"
    };
    // BIP21 URIs carry the amount, and every BTC/LTC/DOGE wallet reads them. For EVM and Tron the QR
    // is the bare address: payment URIs there are read inconsistently and could send the wrong token.
    var URI_SCHEME = { BTC: "bitcoin", LTC: "litecoin", DOGE: "dogecoin" };
    var POLL_MS = 5000;

    var $ = function (id) { return document.getElementById(id); };
    var id = decodeURIComponent(location.pathname.split("/")[2] || "");
    var clockOffset = 0;   // server time minus local time, so a wrong local clock can't fake the countdown
    var payment = null;
    var drawnQr = "";

    function show(el, on) { el.hidden = !on; }

    function setStatus(text, tone) {
        $("status").textContent = text;
        $("status").dataset.tone = tone || "";
    }

    function secondsLeft() {
        return Math.floor((Date.parse(payment.expires_at) - (Date.now() + clockOffset)) / 1000);
    }

    function render() {
        var p = payment, asset = p.asset, left = secondsLeft();
        var received = p.amount_received !== "0";
        var open = p.status === "pending" || p.status === "confirming";
        var label = NETWORKS[p.chain + "/" + p.asset] || p.chain;

        $("heading").textContent = p.status === "pending" && left > 0 ? "Send exactly" : "Payment of";
        $("amount").textContent = p.amount;
        $("asset").textContent = asset;
        $("network").textContent = label;
        $("payment-id").textContent = p.id;
        $("address").value = p.address;
        $("amount-due").value = p.amount_due;
        $("network-warning").textContent = "Send only " + asset + " on " + label + " to this address. " +
            "Other coins, tokens or networks can be lost.";

        if (p.status === "confirmed") {
            setStatus("Paid. Thank you.", "good");
        } else if (p.status === "confirming") {
            setStatus("Payment received. Waiting for " + p.confirmations_required +
                " network confirmations. You can close this page.", "good");
        } else if (p.status === "expired") {
            setStatus(received
                ? "This payment expired with " + p.amount_received + " " + asset + " of " + p.amount +
                  " received. Contact the merchant about it."
                : "This payment expired. Don't send anything to this address. Ask the merchant for a new payment.",
                "bad");
        } else if (left <= 0) {
            setStatus("Time is up. Checking the network for your payment…");
        } else if (received) {
            setStatus("Received " + p.amount_received + " of " + p.amount + " " + asset +
                ". Send the remaining " + p.amount_due + " " + asset + " to the same address.");
        } else {
            setStatus("Waiting for your payment…");
        }

        // Only offer the address while sending is still useful.
        show($("pay-box"), p.status === "pending" && left > 0);
        if (p.status === "pending" && left > 0) {
            var scheme = URI_SCHEME[p.chain];
            var qrText = scheme ? scheme + ":" + p.address + "?amount=" + p.amount_due : p.address;
            if (qrText !== drawnQr) {
                $("qr").replaceChildren(kjua({ text: qrText, render: "canvas", size: 220, ecLevel: "M", fill: "#000", back: "#fff" }));
                drawnQr = qrText;
            }
            var m = Math.floor(left / 60), s = left % 60;
            $("countdown").textContent = m + ":" + (s < 10 ? "0" : "") + s;
        }
        return open;
    }

    function load() {
        fetch("/pay/" + encodeURIComponent(id) + "/status", { cache: "no-store" })
            .then(function (r) {
                if (r.status === 404) throw "not-found";
                if (!r.ok) throw "http " + r.status;
                return r.json();
            })
            .then(function (p) {
                clockOffset = Date.parse(p.server_time) - Date.now();
                payment = p;
                show($("loading"), false);
                show($("payment"), true);
                if (render()) setTimeout(load, POLL_MS);
            })
            .catch(function (err) {
                if (err === "not-found") {
                    show($("loading"), false);
                    show($("not-found"), true);
                    return;
                }
                if (payment) setStatus("Connection problem. Retrying…");
                setTimeout(load, POLL_MS);  // network blip: keep trying, the payment is unaffected
            });
    }

    // Countdown between polls.
    setInterval(function () {
        if (payment && (payment.status === "pending" || payment.status === "confirming")) render();
    }, 1000);

    document.addEventListener("click", function (e) {
        var target = e.target.closest("[data-copy]");
        if (!target) return;
        var input = $(target.dataset.copy);
        var done = function () {
            target.textContent = "Copied";
            setTimeout(function () { target.textContent = "Copy"; }, 1500);
        };
        if (navigator.clipboard) {
            navigator.clipboard.writeText(input.value).then(done, function () { input.select(); });
        } else {
            input.select();
        }
    });

    if (id) load(); else { show($("loading"), false); show($("not-found"), true); }
})();
