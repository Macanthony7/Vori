// Onboarding pages (index.html, xpub.html). This code can talk to our server, so it must never
// see a recovery phrase: phrases live only in the sandboxed derive.html frame.
(function () {
    "use strict";

    var CHAIN_NAMES = { BTC: "Bitcoin", LTC: "Litecoin", DOGE: "Dogecoin", ETH: "Ethereum + BSC", TRX: "Tron" };
    var VAULT_KEY = "wallet.vault.v1"; // also read by wallet.js. Holds only the password-encrypted vault.

    function $id(id) { return document.getElementById(id); }

    function saveVault(vault, accountId) {
        try {
            localStorage.setItem(VAULT_KEY, JSON.stringify({ vault: vault, account_id: accountId || null,
                                                             saved_at: new Date().toISOString() }));
            return true;
        } catch (e) {
            return false; // private mode or storage blocked
        }
    }

    function hasVault() {
        try { return !!localStorage.getItem(VAULT_KEY); } catch (e) { return false; }
    }

    function showError(msg) {
        $id("error").textContent = msg || "";
        $id("error").hidden = !msg;
    }

    // POST /accounts. Returns {ok, data} | {errors: {chain: message}} | {conflict} | null (error already shown).
    async function createAccount(xpubs) {
        showError("");
        var res;
        try {
            res = await fetch("/accounts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ xpubs: xpubs })
            });
        } catch (e) {
            showError("Couldn't reach the server. Check your connection and try again.");
            return null;
        }
        var data = await res.json().catch(function () { return {}; });
        var detail = data.detail || {};
        if (res.status === 201) {
            showResult(data);
            return { ok: true, data: data };
        }
        if (detail.errors) return { errors: detail.errors };
        if (res.status === 409) return { conflict: true };
        showError(detail.error || "Something went wrong (HTTP " + res.status + "). Please try again.");
        return null;
    }

    var CONFLICT = "These keys are already registered to an account. Use that account's API key, or use a different account number.";

    function showResult(data) {
        ["choose", "wallet", "manual"].forEach(function (id) { if ($id(id)) $id(id).hidden = true; });
        $id("api-key").value = data.api_key;
        var list = $id("addresses");
        list.textContent = "";
        Object.keys(data.chains).forEach(function (chain) {
            var dt = document.createElement("dt");
            dt.textContent = CHAIN_NAMES[chain] || chain;
            var dd = document.createElement("dd");
            dd.textContent = data.chains[chain].first_address;
            list.append(dt, dd);
        });
        $id("result").hidden = false;
        $id("result").scrollIntoView();
    }

    $id("copy-key").addEventListener("click", function () {
        var input = $id("api-key");
        var button = this;
        var done = function () { button.textContent = "Copied"; };
        if (navigator.clipboard) {
            navigator.clipboard.writeText(input.value).then(done, function () { input.select(); });
        } else {
            input.select();
        }
    });

    // --- index.html: create / import through the sandboxed frame --------------------------------
    var frame = $id("frame");
    if (frame) {
        if (hasVault()) $id("existing").hidden = false;

        // The frame says something (its height) as soon as its script runs. If it stays silent,
        // the browser blocked sandboxed scripts: say so instead of showing an empty box.
        var FRAME_TIMEOUT_MS = 12000; // the inlined engine is ~3.6 MB; slow phones need a few seconds
        var frameTimer = null;
        var status = $id("frame-status");

        function frameStarted() {
            clearTimeout(frameTimer);
            status.hidden = true;
        }

        document.querySelectorAll(".option[data-mode]").forEach(function (button) {
            button.addEventListener("click", function () {
                showError("");
                status.textContent = "Loading the secure phrase window…";
                status.hidden = false;
                clearTimeout(frameTimer);
                frame.hidden = false;
                frameTimer = setTimeout(function () {
                    status.hidden = true;
                    frame.hidden = true; // don't leave an empty box above the message
                    frame.src = "about:blank";
                    showError("Your browser blocked the secure phrase window, so it can't run here. Open this page in Chrome, Edge, Firefox or Safari, or go back and choose \"Use xpub keys\".");
                }, FRAME_TIMEOUT_MS);
                frame.src = "derive.html#" + button.dataset.mode;
                $id("choose").hidden = true;
                $id("wallet").hidden = false;
            });
        });

        $id("back").addEventListener("click", function () {
            clearTimeout(frameTimer);
            frame.src = "about:blank"; // drops the frame and anything typed in it
            $id("wallet").hidden = true;
            $id("choose").hidden = false;
            showError("");
        });

        window.addEventListener("message", function (e) {
            if (e.source !== frame.contentWindow || !e.data) return;
            frameStarted();
            if (e.data.type === "frame-error") {
                showError("The secure phrase frame failed to load (" + String(e.data.message).slice(0, 200) + "). Please reload, or use xpub keys instead.");
            } else if (e.data.type === "height") {
                frame.style.height = Math.min(Number(e.data.height) || 0, 2000) + 24 + "px";
            } else if (e.data.type === "xpubs") {
                var xpubs = e.data.xpubs || {};
                var valid = Object.keys(xpubs).every(function (k) { return typeof xpubs[k] === "string"; });
                if (!valid) return;
                var vault = e.data.vault;
                createAccount(xpubs).then(function (r) {
                    if (!r) return;
                    if (r.errors) return showError("The derived keys were rejected. Please reload and try again.");
                    // Created, or already registered (the same wallet on a new device): either way the
                    // wallet is the merchant's, so keep its encrypted vault on this device.
                    frame.src = "about:blank";
                    var saved = vault && saveVault(vault, r.ok ? r.data.account_id : null);
                    if (r.conflict) {
                        $id("wallet").hidden = true;
                        if (saved) {
                            $id("restored").hidden = false;
                        } else {
                            showError(CONFLICT);
                        }
                        return;
                    }
                    $id(saved ? "vault-saved" : "vault-failed").hidden = false;
                });
            }
        });
    }

    // --- xpub.html: paste xpubs manually ----------------------------------------------------------
    var form = $id("manual");
    if (form) {
        form.addEventListener("submit", async function (e) {
            e.preventDefault();
            var xpubs = {};
            Array.prototype.forEach.call(form.querySelectorAll("input"), function (input) {
                $id("e-" + input.name).textContent = "";
                input.removeAttribute("aria-invalid");
                if (input.value.trim()) xpubs[input.name] = input.value.trim();
            });
            if (!Object.keys(xpubs).length) {
                showError("Paste at least one xpub.");
                return;
            }
            var button = form.querySelector("button[type=submit]");
            button.disabled = true;
            var r = await createAccount(xpubs);
            button.disabled = false;
            if (r && r.conflict) showError(CONFLICT);
            if (r && r.errors) {
                Object.keys(r.errors).forEach(function (chain) {
                    var input = $id("x-" + chain);
                    if (!input) return;
                    $id("e-" + chain).textContent = r.errors[chain];
                    input.setAttribute("aria-invalid", "true");
                });
                form.querySelector("[aria-invalid]").focus();
            }
        });
    }
})();
