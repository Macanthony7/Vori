// Wallet page. Holds only the password-encrypted vault (saved by the sign-up page) and hands it to
// the sandboxed, offline frame, which asks for the password and keeps the unlocked keys.
// After unlock the frame signs a login challenge, and the dashboard uses the resulting session.
// This page can reach our server, so it must never see the password or the phrase.
// Layout: three tabs (Wallet, Payments, Settings), each a stack of screens. Docs: DESIGN.md#wallet,
// docs/security.md#wallet-vault, docs/api.md#wallet
(function () {
    "use strict";

    var VAULT_KEY = "wallet.vault.v1"; // written by the sign-up page (frontend/app/src/signup)
    var FRAME_TIMEOUT_MS = 12000;       // same as sign-up: the inlined engine is ~3.6 MB
    var NETWORKS = { // [what it is, which network]
        "BTC/BTC": ["BTC", "Bitcoin"],
        "LTC/LTC": ["LTC", "Litecoin"],
        "DOGE/DOGE": ["DOGE", "Dogecoin"],
        "ETH/ETH": ["ETH", "Ethereum"],
        "ETH/USDT": ["USDT", "Ethereum (ERC-20)"],
        "BSC/BNB": ["BNB", "BNB Smart Chain (BEP-20)"],
        "BSC/USDT": ["USDT", "BNB Smart Chain (BEP-20)"],
        "TRX/TRX": ["TRX", "Tron"],
        "TRX/USDT": ["USDT", "Tron (TRC-20)"],
        "SEPOLIA/ETH": ["ETH", "Sepolia testnet (no real value)"],
        "SEPOLIA/USDT": ["USDT", "Sepolia testnet (ERC-20, no real value)"],
        "NILE/TRX": ["TRX", "Tron Nile testnet (no real value)"],
        "NILE/USDT": ["USDT", "Tron Nile testnet (TRC-20, no real value)"]
    };
    var COIN_NAMES = { BTC: "Bitcoin", LTC: "Litecoin", DOGE: "Dogecoin", ETH: "Ethereum", BNB: "BNB", TRX: "TRON", USDT: "Tether" };
    var COIN_GLYPH = { BTC: "₿", LTC: "Ł", DOGE: "Ð", ETH: "Ξ", BNB: "B", TRX: "T", USDT: "₮" }; // fallback when there's no logo
    var COIN_LOGO = { BTC: 1, LTC: 1, DOGE: 1, ETH: 1, BNB: 1, TRX: 1, USDT: 1 }; // /coins/<asset>.svg (cryptocurrency-icons, CC0)
    var TESTNETS = { SEPOLIA: true, NILE: true }; // no real value: never counted in the total
    // What can be sent so far (docs/roadmap.md), as "CHAIN/ASSET".
    var SENDABLE = ["BTC/BTC", "LTC/LTC", "DOGE/DOGE", "ETH/ETH", "ETH/USDT", "BSC/BNB", "BSC/USDT", "TRX/TRX", "TRX/USDT",
                    "SEPOLIA/ETH", "SEPOLIA/USDT", "NILE/TRX", "NILE/USDT"];
    var TRON = { TRX: true, NILE: true };
    var TOPUP_WAIT_MS = 180000;
    var EXPLORER = {
        BTC: "https://blockchair.com/bitcoin/transaction/",
        LTC: "https://blockchair.com/litecoin/transaction/",
        DOGE: "https://blockchair.com/dogecoin/transaction/",
        ETH: "https://etherscan.io/tx/",
        BSC: "https://bscscan.com/tx/",
        SEPOLIA: "https://sepolia.etherscan.io/tx/",
        TRX: "https://tronscan.org/#/transaction/",
        NILE: "https://nile.tronscan.org/#/transaction/"
    };
    var STATUS = { choosing: "Choosing a coin", pending: "Waiting", confirming: "Seen", confirmed: "Confirmed", expired: "Expired" }; // same words as the checkout
    var CURRENCY = "USD";

    function $id(id) { return document.getElementById(id); }

    function showError(msg) {
        $id("error").textContent = msg || "";
        $id("error").hidden = !msg;
    }

    function load() {
        try { return JSON.parse(localStorage.getItem(VAULT_KEY)); } catch (e) { return null; }
    }

    var saved = load();
    var frame = $id("frame");
    var timer = null;
    var session = null; // "gws_..." token, memory only: lock or reload = sign in again
    var frameLoads = 0;

    if (!saved || !saved.vault) {
        $id("none").hidden = false;
        return;
    }

    // --- API --------------------------------------------------------------------------------------
    async function api(method, path, body) {
        var headers = { "Content-Type": "application/json" };
        if (session) headers.Authorization = "Bearer " + session;
        var res = await fetch(path, { method: method, headers: headers, body: body ? JSON.stringify(body) : undefined });
        var data = res.status === 204 ? {} : await res.json().catch(function () { return {}; });
        if (!res.ok) throw new Error((data.detail && data.detail.error) || "HTTP " + res.status);
        return data;
    }

    // --- Lock / unlock ----------------------------------------------------------------------------
    // Locking = reloading the frame (everything it had in memory is gone) and ending the session.
    function startFrame() {
        if (session) api("DELETE", "/auth/session").catch(function () {});
        session = null;
        $id("dashboard").hidden = true;
        $id("content").hidden = true;
        $id("tabs").hidden = true;
        document.body.classList.remove("unlocked");
        $id("vault").hidden = false;
        $id("frame-status").hidden = false;
        frame.hidden = false;
        showError("");
        clearTimeout(timer);
        timer = setTimeout(function () {
            $id("frame-status").hidden = true;
            frame.hidden = true;
            frame.src = "about:blank";
            showError("Your browser blocked the secure wallet window, so it can't run here. Open this page in Chrome, Edge, Firefox or Safari.");
        }, FRAME_TIMEOUT_MS);
        // A new URL every time: re-setting the same "derive.html#unlock" is only a fragment change, which
        // does NOT reload the frame, so the unlocked keys would stay in its memory.
        frame.src = "derive.html?load=" + (++frameLoads) + "#unlock";
    }

    async function signIn() {
        $id("vault").hidden = true; // the frame stays loaded, just out of sight
        $id("dashboard").hidden = false;
        $id("signing-in").hidden = false;
        try {
            var c = await api("POST", "/auth/challenge");
            frame.contentWindow.postMessage({ type: "sign-login", challenge: c.challenge }, "*");
        } catch (e) {
            showError("Couldn't reach the server (" + e.message + "). Reload and try again.");
        }
    }

    async function finishSignIn(msg) {
        try {
            var r = await api("POST", "/auth/login", { xpub: msg.xpub, challenge: msg.challenge, signature: msg.signature });
            session = r.token;
        } catch (e) {
            $id("signing-in").hidden = true;
            showError(e.message === "no account for this wallet"
                ? "This wallet has no account with us yet. Set it up again from the start page with the same recovery phrase."
                : "Sign-in failed (" + e.message + "). Reload and try again.");
            return;
        }
        $id("signing-in").hidden = true;
        $id("content").hidden = false;
        $id("tabs").hidden = false;
        document.body.classList.add("unlocked");
        ["home", "payments-home", "settings-home"].forEach(openScreen);
        showTab("wallet");
        loadDashboard();
    }

    window.addEventListener("message", function (e) {
        if (e.source !== frame.contentWindow || !e.data) return;
        clearTimeout(timer);
        $id("frame-status").hidden = true;
        var type = e.data.type;
        if (type === "ready") {
            // The frame's origin is opaque ("null"), so "*" is the only target; it's our own frame (src above).
            frame.contentWindow.postMessage({ type: "vault", vault: saved.vault }, "*");
        } else if (type === "height") {
            frame.style.height = Math.min(Number(e.data.height) || 0, 2000) + 24 + "px";
        } else if (type === "unlocked") {
            signIn();
        } else if (type === "vault-upgraded" && e.data.vault) {
            saved.vault = e.data.vault;  // still only ciphertext
            try { localStorage.setItem(VAULT_KEY, JSON.stringify(saved)); } catch (err) { /* keep using v1 */ }
        } else if (type === "login-signature") {
            finishSignIn(e.data);
        } else if (type === "engine-request") {
            sendEngine(e.data.files);
        } else if (type === "send-signed") {
            broadcastSigned(e.data);
        } else if (type === "send-cancelled") {
            hideSigner();
            $id("send-review").disabled = false;
        } else if (type === "locked") {
            startFrame(); // auto-lock after 15 minutes
        } else if (type === "frame-error") {
            showError("The secure wallet window failed to load (" + String(e.data.message).slice(0, 200) + "). Please reload.");
        }
    });

    // --- Small helpers ----------------------------------------------------------------------------
    function label(chain, asset) { return NETWORKS[chain + "/" + asset] || [asset, chain]; }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    var CHEVRON = "M9 6l6 6-6 6";
    function icon(d, className) {
        var ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg"), path = document.createElementNS(ns, "path");
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("class", "i" + (className ? " " + className : ""));
        svg.setAttribute("aria-hidden", "true");
        path.setAttribute("d", d);
        svg.appendChild(path);
        return svg;
    }

    // The coin's logo (or a coloured disc with its sign); tokens and test networks get a small chain badge.
    function coinMark(span, asset, glyph) {
        if (!COIN_LOGO[asset]) { span.textContent = glyph; return span; }
        var img = el("img");
        img.src = "/coins/" + asset.toLowerCase() + ".svg";
        img.alt = "";
        span.appendChild(img);
        return span;
    }
    function coinIcon(chain, asset, large) {
        var wrap = el("span", large ? "coin coin-lg" : "coin");
        wrap.dataset.coin = asset;
        coinMark(wrap, asset, COIN_GLYPH[asset] || asset.charAt(0));
        var native = { BTC: "BTC", LTC: "LTC", DOGE: "DOGE", ETH: "ETH", BSC: "BNB", TRX: "TRX", SEPOLIA: "ETH", NILE: "TRX" }[chain];
        if (native !== asset || TESTNETS[chain]) {
            var badge = TESTNETS[chain] ? el("span", "coin-badge", "t")
                : coinMark(el("span", "coin-badge"), native, COIN_GLYPH[native] || chain.charAt(0));
            badge.dataset.coin = TESTNETS[chain] ? "TEST" : native;
            wrap.appendChild(badge);
        }
        return wrap;
    }

    function money(value) {
        try {
            return new Intl.NumberFormat(undefined, { style: "currency", currency: CURRENCY }).format(value);
        } catch (e) { return value.toFixed(2) + " " + CURRENCY; }
    }

    function pct(fraction) {
        var n = Number(fraction) * 100;
        return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
    }

    function trend(node, fraction) { // green up, red down, muted when unknown
        node.dataset.trend = fraction == null ? "" : Number(fraction) >= 0 ? "up" : "down";
    }

    // --- Screens: one per tab is visible; back returns to the tab's first screen ---------------------
    var screenBefore = {}; // screen id -> the screen that opened it (for Back)

    function openScreen(id, from) {
        var target = $id(id), panel = target.closest(".panel-tab");
        panel.querySelectorAll(".screen").forEach(function (s) { s.hidden = s !== target; });
        if (from) screenBefore[id] = from;
        if (!panel.hidden) window.scrollTo(0, 0);
    }

    function currentScreen(panel) {
        return Array.prototype.find.call(panel.querySelectorAll(".screen"), function (s) { return !s.hidden; });
    }

    document.addEventListener("click", function (e) {
        var back = e.target.closest("[data-back]");
        if (back) {
            var screen = back.closest(".screen"), panel = screen.closest(".panel-tab"), prev = screenBefore[screen.id];
            openScreen(prev && panel.contains($id(prev)) ? prev : panel.querySelector(".screen").id);
            return;
        }
        var open = e.target.closest("[data-open]");
        if (open) openScreen(open.dataset.open, open.closest(".screen").id);
    });

    // --- Tabs ---------------------------------------------------------------------------------------
    var TABS = ["wallet", "payments", "settings"];

    function showTab(name, focus) {
        TABS.forEach(function (t) {
            var tab = $id("tab-" + t), on = t === name;
            var wasOn = tab.getAttribute("aria-selected") === "true";
            tab.setAttribute("aria-selected", String(on));
            tab.tabIndex = on ? 0 : -1;
            $id("panel-" + t).hidden = !on;
            if (on && focus) tab.focus();
            if (on && wasOn && !focus) openScreen($id("panel-" + t).querySelector(".screen").id); // tap the open tab again: back to its start
        });
        window.scrollTo(0, 0);
    }
    TABS.forEach(function (t, i) {
        var tab = $id("tab-" + t);
        tab.addEventListener("click", function () { showTab(t); });
        tab.addEventListener("keydown", function (e) {  // tablist pattern: arrows move between sections
            var step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
            if (e.key === "Home") step = -i;
            if (e.key === "End") step = TABS.length - 1 - i;
            if (step === undefined) return;
            e.preventDefault();
            showTab(TABS[(i + step + TABS.length) % TABS.length], true);
        });
    });

    // --- Dashboard ----------------------------------------------------------------------------------
    function loadDashboard() {
        loadBalances();
        loadPayments();
        loadAccount();
        loadMarket();
    }

    // --- Market: price and 24 h change per coin (display only) -------------------------------------
    var market = {}; // coin -> {price: Number|null, change: Number|null}

    async function loadMarket() {
        try {
            var data = await api("GET", "/market?currency=" + CURRENCY);
            Object.keys(data.coins).forEach(function (coin) {
                var m = data.coins[coin];
                market[coin] = { price: m.price == null ? null : Number(m.price), change: m.change_24h == null ? null : Number(m.change_24h) };
            });
        } catch (e) { /* prices are a nicety: balances still show without them */ }
        drawBalances();
    }

    function fiatOf(b) { // value of a balance row in CURRENCY, or null (unknown price, or a test network)
        var m = market[b.asset];
        if (TESTNETS[b.chain] || !m || m.price == null) return null;
        return Number(b.balance) * m.price;
    }

    // --- Balances and the coin list -------------------------------------------------------------------
    var balances = [];
    var balancesLoaded = false;

    async function loadBalances() {
        var list = $id("balances");
        if (!balancesLoaded) {
            list.textContent = "";
            list.appendChild(el("li", "note row-static", "Checking the blockchains…"));
        }
        var data;
        try {
            data = await api("GET", "/wallet/balances");
        } catch (e) {
            if (!balancesLoaded) list.textContent = "";
            return showError("Couldn't load balances (" + e.message + ").");
        }
        balances = data.balances;
        balancesLoaded = true;
        fillChargeNetworks(balances);
        drawBalances();
    }

    // The chain's own coin on a real network (Bitcoin on Bitcoin): its name is the network's name.
    function isNative(b) { return !TESTNETS[b.chain] && label(b.chain, b.asset)[1] === COIN_NAMES[b.asset] || (b.chain === "TRX" && b.asset === "TRX"); }

    function coinRow(b, onClick) {
        var name = label(b.chain, b.asset), m = market[b.asset] || {}, fiat = fiatOf(b);
        var li = el("li");
        var row = el("button", "row coin-row");
        row.type = "button";
        row.setAttribute("aria-label", name[0] + " on " + name[1] + ": " + (b.complete ? "" : "at least ") + b.balance + " " + b.asset);
        var main = el("span", "row-main");
        var title = el("span", "row-title"), native = isNative(b);
        title.append(el("span", "", native ? name[1] : name[0]), " ", el("span", native ? "network visually-hidden" : "network mark", name[1]));
        var sub = el("span", "row-sub");
        if (TESTNETS[b.chain]) sub.textContent = "Test coins, no real value";
        else if (m.price != null) {
            sub.append(money(m.price) + " ");
            if (m.change != null) { var c = el("span", "change", pct(m.change)); trend(c, m.change); sub.appendChild(c); }
        }
        main.append(title, sub);
        var end = el("span", "row-end");
        end.append(el("span", "amount" + (b.balance === "0" ? " zero" : ""), (b.complete ? "" : "≥ ") + b.balance + " " + b.asset),
                   el("span", "row-sub", fiat == null || b.balance === "0" ? "" : money(fiat)));
        row.append(coinIcon(b.chain, b.asset), main, end, icon(CHEVRON, "chev"));
        row.addEventListener("click", function () { onClick(b); });
        li.appendChild(row);
        return li;
    }

    function drawBalances() {
        if (!balancesLoaded) return;
        var list = $id("balances");
        list.textContent = "";
        var total = 0, start = 0, priced = false, incomplete = false;
        balances.forEach(function (b) {
            list.appendChild(coinRow(b, function () { showCoin(b); }));
            var fiat = fiatOf(b), m = market[b.asset];
            if (fiat != null) {
                priced = true;
                total += fiat;
                start += m.change != null ? fiat / (1 + m.change) : fiat; // what it was worth 24 h ago
            }
            if (!b.complete) incomplete = true;
        });
        $id("balances-note").hidden = !incomplete;
        $id("total").textContent = priced ? money(total) : "—";
        var change = $id("total-change");
        if (priced && start > 0 && total !== start) {
            var diff = total - start;
            change.textContent = (diff >= 0 ? "+" : "−") + money(Math.abs(diff)) + " (" + pct(diff / start) + ") today";
            trend(change, diff);
        } else {
            change.textContent = priced ? "Total in " + CURRENCY : "Prices unavailable right now";
            trend(change, null);
        }
        if (!$id("coin").hidden && coinNow) {
            var fresh = balances.find(function (b) { return b.chain === coinNow.chain && b.asset === coinNow.asset; });
            if (fresh) drawCoin(fresh);
        }
    }

    // Send / Receive from the home actions: choose the coin first.
    function pick(kind) {
        var list = $id("picker-list");
        list.textContent = "";
        $id("picker-title").textContent = kind === "send" ? "Send which coin?" : "Receive which coin?";
        balances.filter(function (b) { return kind !== "send" || SENDABLE.indexOf(b.chain + "/" + b.asset) >= 0; })
            .forEach(function (b) {
                list.appendChild(coinRow(b, function (chosen) { kind === "send" ? showSend(chosen, "picker") : showReceive(chosen, "picker"); }));
            });
        openScreen("picker", "home");
    }
    $id("act-send").addEventListener("click", function () { pick("send"); });
    $id("act-receive").addEventListener("click", function () { pick("receive"); });
    $id("act-charge").addEventListener("click", function () { showCharge(null, "home"); });

    // --- One coin ------------------------------------------------------------------------------------
    var coinNow = null, chartSpan = "1D";

    function showCoin(b) {
        coinNow = b;
        drawCoin(b);
        openScreen("coin", "home");
        drawChart();
    }

    function drawCoin(b) {
        var name = label(b.chain, b.asset), m = market[b.asset] || {}, fiat = fiatOf(b);
        $id("coin-title").textContent = isNative(b) ? COIN_NAMES[b.asset] : name[0] + " · " + name[1];
        $id("coin-icon").replaceWith(Object.assign(coinIcon(b.chain, b.asset, true), { id: "coin-icon" }));
        $id("coin-balance").textContent = (b.complete ? "" : "≥ ") + b.balance + " " + b.asset;
        $id("coin-fiat").textContent = TESTNETS[b.chain] ? "Test coins, no real value" : fiat == null ? "" : money(fiat);
        $id("coin-price").textContent = m.price == null ? "Unavailable" : money(m.price) + (m.change != null ? " (" + pct(m.change) + ")" : "");
        $id("coin-network").textContent = name[1];
        $id("coin-address").textContent = b.main_address;
        $id("coin-send").hidden = SENDABLE.indexOf(b.chain + "/" + b.asset) < 0;
        var list = $id("coin-payments");
        list.textContent = "";
        var mine = allPayments.filter(function (p) { return p.chain === b.chain && p.asset === b.asset; }).slice(0, 10);
        mine.forEach(function (p) { list.appendChild(paymentRow(p, "coin")); });
        list.hidden = !mine.length;
        $id("coin-payments-empty").hidden = !!mine.length;
    }

    $id("coin-send").addEventListener("click", function () { showSend(coinNow, "coin"); });
    $id("coin-receive").addEventListener("click", function () { showReceive(coinNow, "coin"); });
    $id("coin-charge").addEventListener("click", function () { showCharge(coinNow, "coin"); });

    // The chart: closing prices as one line, drawn as SVG (no chart library).
    async function drawChart() {
        var b = coinNow, box = $id("chart"), m = market[b.asset] || {};
        document.querySelectorAll("#chart-spans [data-span]").forEach(function (btn) {
            btn.setAttribute("aria-pressed", String(btn.dataset.span === chartSpan));
        });
        $id("chart-price").textContent = m.price == null ? "" : money(m.price);
        $id("chart-change").textContent = "";
        box.textContent = "";
        box.classList.add("loading-chart");
        var points = [];
        try {
            points = (await api("GET", "/market/" + b.asset + "/history?span=" + chartSpan + "&currency=" + CURRENCY)).points;
        } catch (e) { /* drawn as unavailable below */ }
        if (coinNow !== b) return; // the user moved on
        box.classList.remove("loading-chart");
        if (points.length < 2) {
            box.appendChild(el("p", "note center", "No price history right now."));
            box.setAttribute("aria-label", "Price chart unavailable");
            return;
        }
        var values = points.map(function (p) { return Number(p[1]); });
        var low = Math.min.apply(null, values), high = Math.max.apply(null, values), span = high - low || 1;
        var W = 600, H = 180, pad = 8;
        var coords = values.map(function (v, i) {
            return [(i / (values.length - 1)) * W, pad + (1 - (v - low) / span) * (H - 2 * pad)];
        });
        var line = coords.map(function (c, i) { return (i ? "L" : "M") + c[0].toFixed(1) + " " + c[1].toFixed(1); }).join("");
        var ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
        svg.setAttribute("viewBox", "0 0 " + W + " " + H);
        svg.setAttribute("preserveAspectRatio", "none");
        var area = document.createElementNS(ns, "path");
        area.setAttribute("d", line + "L" + W + " " + H + "L0 " + H + "Z");
        area.setAttribute("class", "chart-area");
        var path = document.createElementNS(ns, "path");
        path.setAttribute("d", line);
        path.setAttribute("class", "chart-line");
        svg.append(area, path);
        box.appendChild(svg);
        var first = values[0], last = values[values.length - 1], change = (last - first) / first;
        $id("chart-change").textContent = pct(change);
        trend($id("chart-change"), change);
        box.setAttribute("aria-label", b.asset + " price over " + chartSpan + ": from " + money(first) + " to " + money(last) +
            ", low " + money(low) + ", high " + money(high));
    }
    document.querySelectorAll("#chart-spans [data-span]").forEach(function (btn) {
        btn.addEventListener("click", function () { chartSpan = btn.dataset.span; drawChart(); });
    });

    // --- Receive ---------------------------------------------------------------------------------------
    function showReceive(b, from) {
        var name = label(b.chain, b.asset);
        $id("receive-title").textContent = "Receive " + name[0];
        $id("receive-network").textContent = name[1];
        $id("receive-address").value = b.main_address;
        $id("receive-warning").textContent = "Send only " + name[0] + " on " + name[1] + " to this address. Other coins, tokens or networks can be lost.";
        $id("receive-qr").replaceChildren(kjua({ text: b.main_address, render: "canvas", size: 220, ecLevel: "M", fill: "#000", back: "#fff" }));
        openScreen("receive", from);
    }

    // --- New payment: what a shop does with POST /payments, here with the wallet session ---------------
    function fillChargeNetworks(list) {
        var select = $id("charge-network"), current = select.value;
        select.textContent = "";
        list.forEach(function (b) {
            var name = label(b.chain, b.asset), option = el("option", "", name[0] + " on " + name[1]);
            option.value = b.chain + "/" + b.asset;
            select.appendChild(option);
        });
        select.value = current || "SEPOLIA/ETH";
        if (!select.value && select.options.length) select.selectedIndex = 0;
    }

    function showCharge(b, from) {
        if (b) {
            $id("charge-unit").value = "coin";
            $id("charge-unit").dispatchEvent(new Event("change"));
            $id("charge-network").value = b.chain + "/" + b.asset;
        }
        $id("charge-error").textContent = "";
        openScreen("charge", from);
        $id("charge-amount").focus();
    }

    $id("charge-unit").addEventListener("change", function () {
        var priced = this.value !== "coin";
        $id("charge-network-box").hidden = priced;
        $id("charge-priced-note").hidden = !priced;
    });

    $id("charge-create").addEventListener("click", async function () {
        var button = this, pair = $id("charge-network").value.split("/"), unit = $id("charge-unit").value;
        var amount = $id("charge-amount").value.trim();
        $id("charge-error").textContent = "";
        $id("charge-result").hidden = true;
        button.disabled = true;
        try {
            var p = await api("POST", "/payments", unit === "coin"
                ? { chain: pair[0], asset: pair[1], amount: amount }
                : { price: amount, currency: unit });
            var link = el("a", "", "Open the checkout page");
            link.href = "/pay/" + encodeURIComponent(p.id);
            link.target = "_blank";
            link.rel = "noopener";
            $id("charge-result").replaceChildren("Payment created: " + (p.chain
                ? p.amount + " " + p.asset + " to " + p.address
                : p.price + " " + p.currency + ", the payer picks the coin") + ". ", link);
            $id("charge-result").hidden = false;
            loadPayments();
        } catch (e) {
            $id("charge-error").textContent = e.message;
        } finally {
            button.disabled = false;
        }
    });

    // --- Payments: grouped by day, newest first ----------------------------------------------------------
    var allPayments = [];
    var drawnStatus = {}; // payment id -> status last drawn, to strike a hallmark only when it changes
    var payFilter = "";

    async function loadPayments() {
        var data;
        try {
            data = await api("GET", "/payments?limit=100");
        } catch (e) {
            return showError("Couldn't load payments (" + e.message + ").");
        }
        allPayments = data.payments;
        drawPayments();
        if (coinNow) drawCoin(balances.find(function (b) { return b.chain === coinNow.chain && b.asset === coinNow.asset; }) || coinNow);
    }

    function matches(p, query, status) {
        if (status && p.status !== status) return false;
        if (!query) return true;
        return [p.id, p.reference, p.address, p.amount, p.price, p.asset, p.chain].some(function (v) {
            return v && String(v).toLowerCase().indexOf(query) >= 0;
        });
    }

    function statusMark(p, strike) {
        var received = p.amount_received && p.amount_received !== "0"; // price requests have none yet
        var mark = el("span", "mark", STATUS[p.status] || p.status);
        mark.dataset.state = p.status;
        if (p.status === "confirmed") mark.classList.add("proven");
        if (p.status === "expired" && received) mark.textContent += ", " + p.amount_received + " received";
        if (strike && drawnStatus[p.id] && drawnStatus[p.id] !== p.status) mark.classList.add("struck"); // changed since last draw
        return mark;
    }

    function amountText(p) {
        return p.chain ? p.amount + " " + label(p.chain, p.asset)[0] : p.price + " " + p.currency;
    }

    function timeOf(iso) {
        return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    function paymentRow(p, from) {
        var li = el("li");
        li.dataset.id = p.id;
        var row = el("button", "row pay-row");
        row.type = "button";
        var main = el("span", "row-main");
        var title = el("span", "row-title");
        title.append(el("span", "", amountText(p)));
        if (p.chain) title.append(" ", el("span", "mark", label(p.chain, p.asset)[1]));
        var sub = el("span", "row-sub", (p.reference ? p.reference + " · " : "") + (p.chain ? "" : "Payer picks the coin · ") + timeOf(p.created_at));
        main.append(title, sub);
        var end = el("span", "row-end");
        end.append(statusMark(p, from === "payments-home"), el("span", "row-sub", p.price && p.chain ? p.price + " " + p.currency : ""));
        row.append(p.chain ? coinIcon(p.chain, p.asset) : el("span", "coin coin-any", "$"), main, end, icon(CHEVRON, "chev"));
        row.addEventListener("click", function () { showPayment(p, from); });
        li.appendChild(row);
        return li;
    }

    function dayLabel(iso) {
        var d = new Date(iso), today = new Date(), y = new Date();
        y.setDate(today.getDate() - 1);
        if (d.toDateString() === today.toDateString()) return "Today";
        if (d.toDateString() === y.toDateString()) return "Yesterday";
        return d.toLocaleDateString([], { day: "numeric", month: "short", year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
    }

    function drawPayments() {
        var query = $id("pay-search").value.trim().toLowerCase();
        var shown = allPayments.filter(function (p) { return matches(p, query, payFilter); });
        var box = $id("payments");
        box.textContent = "";
        var list = null, day = null;
        shown.forEach(function (p) {
            var d = dayLabel(p.created_at);
            if (d !== day) {
                day = d;
                box.appendChild(el("h3", "group-title", d));
                list = box.appendChild(el("ul", "list"));
            }
            list.appendChild(paymentRow(p, "payments-home"));
        });
        allPayments.forEach(function (p) { drawnStatus[p.id] = p.status; });
        box.hidden = !shown.length;
        $id("payments-empty").hidden = !!allPayments.length;
        $id("payments-nomatch").hidden = !allPayments.length || !!shown.length;
    }
    $id("pay-search").addEventListener("input", drawPayments);
    document.querySelectorAll("#pay-filter [data-status]").forEach(function (chip) {
        chip.addEventListener("click", function () {
            payFilter = chip.dataset.status;
            document.querySelectorAll("#pay-filter [data-status]").forEach(function (c) { c.setAttribute("aria-pressed", String(c === chip)); });
            drawPayments();
        });
    });

    // One payment, as a grouped list. From the coin screen it opens in the Payments tab.
    function showPayment(p, from) {
        var received = p.amount_received && p.amount_received !== "0";
        $id("pd-amount").textContent = amountText(p);
        $id("pd-status").replaceChildren(statusMark(p, false));
        var fields = $id("pd-fields");
        fields.textContent = "";
        var add = function (name, value, mono) {
            if (value === undefined || value === null || value === "") return;
            var li = el("li", "row-static");
            li.append(el("span", "", name), el("span", mono ? "mono" : "", value));
            fields.appendChild(li);
        };
        if (p.chain) add("Network", label(p.chain, p.asset)[1]);
        if (p.price) add("Price", p.price + " " + p.currency + (p.rate ? " (1 " + p.asset + " = " + p.rate + " " + p.currency + ")" : ""));
        if (p.chain) add("Received", p.amount_received + " " + p.asset);
        add("Reference", p.reference);
        add("Address", p.address, true);
        add("Created", new Date(p.created_at).toLocaleString());
        add("Expires", p.expires_at && new Date(p.expires_at).toLocaleString());
        add("Payment id", p.id, true);
        $id("pd-checkout").href = "/pay/" + encodeURIComponent(p.id);
        var explorer = $id("pd-explorer");
        explorer.hidden = !received;
        if (received) explorer.href = p.transactions && p.transactions.length === 1 ? p.transactions[0].url : p.address_url;
        if (from === "coin") { showTab("payments"); openScreen("payment-detail", "payments-home"); }
        else openScreen("payment-detail", "payments-home");
    }

    // --- Settings ----------------------------------------------------------------------------------------
    function showKeyInfo(hint, usedAt) {
        $id("key-info").textContent = (hint ? "Your current key starts with " + hint + "…" : "Your key was made before key hints existed.") +
            " Last used: " + (usedAt ? new Date(usedAt).toLocaleString() : "not yet") + ".";
        $id("key-summary").textContent = hint ? hint + "…" : "";
    }

    async function loadAccount() {
        try {
            var me = await api("GET", "/accounts/me");
            showKeyInfo(me.api_key_hint, me.api_key_used_at);
            showHook(me.webhook_url);
            $id("shop-name").value = me.shop_name || "";
            $id("shop-summary").textContent = me.shop_name || "Not set";
            if (me.shop_name) $id("wallet-name").textContent = me.shop_name;
            shopLogo = undefined; // unchanged unless a file is picked or removed
            $id("shop-logo-box").hidden = !me.has_logo;
            if (me.logo) $id("shop-logo-preview").src = me.logo;
        } catch (e) {
            $id("key-info").textContent = "";
        }
        loadDeliveries();
    }

    // Shop name and logo
    var shopLogo; // undefined = keep, null = remove, "data:image/..." = new logo

    $id("shop-logo").addEventListener("change", function () {
        var file = this.files[0];
        $id("shop-error").textContent = "";
        if (!file) return;
        if (["image/png", "image/jpeg"].indexOf(file.type) < 0 || file.size > 100000) {
            $id("shop-error").textContent = "Pick a PNG or JPEG image of at most 100 KB.";
            this.value = "";
            return;
        }
        var reader = new FileReader();
        reader.onload = function () {
            shopLogo = reader.result;
            $id("shop-logo-preview").src = shopLogo;
            $id("shop-logo-box").hidden = false;
        };
        reader.readAsDataURL(file);
    });
    $id("shop-logo-remove").addEventListener("click", function () {
        shopLogo = null;
        $id("shop-logo").value = "";
        $id("shop-logo-box").hidden = true;
    });
    $id("shop-save").addEventListener("click", async function () {
        var button = this, body = { name: $id("shop-name").value.trim() };
        if (shopLogo !== undefined) body.logo = shopLogo;
        $id("shop-error").textContent = "";
        $id("shop-result").textContent = "";
        button.disabled = true;
        try {
            var r = await api("PUT", "/accounts/me/shop", body);
            shopLogo = undefined;
            $id("shop-result").textContent = "Saved. New checkout pages show " + (r.shop_name ? "“" + r.shop_name + "”" : "no name") +
                (r.has_logo ? " with your logo." : " without a logo.");
            $id("shop-summary").textContent = r.shop_name || "Not set";
            $id("wallet-name").textContent = r.shop_name || "Main wallet";
        } catch (e) {
            $id("shop-error").textContent = e.message;
        }
        button.disabled = false;
    });

    // Webhooks and integration (docs/api.md#webhooks)
    function showHook(url) {
        $id("hook-current").textContent = url ? "Sending events to " + url : "No webhook set: your shop only learns about payments by asking the API.";
        if (url) $id("hook-url").value = url;
        $id("hook-test").hidden = !url;
        $id("hook-remove").hidden = !url;
        $id("hook-summary").textContent = url ? "On" : "Off";
    }

    var DELIVERY = { delivered: "Delivered", queued: "Queued", retrying: "Retrying", failed: "Failed" };
    var DELIVERY_TONE = { delivered: "confirmed", queued: "pending", retrying: "pending", failed: "failed" };

    async function loadDeliveries() {
        var data;
        try {
            data = await api("GET", "/accounts/me/webhook/deliveries");
        } catch (e) {
            return;
        }
        var list = $id("hook-deliveries");
        list.textContent = "";
        data.deliveries.forEach(function (d) {
            var state = DELIVERY[d.status] || d.status;
            if (d.last_error && d.status !== "delivered") state += " (" + d.last_error + ")";
            var li = el("li", "row-static delivery");
            var main = el("span", "row-main");
            main.append(el("span", "row-title", d.event), el("span", "row-sub mono", d.payment_id));
            var mark = el("span", "mark", state);
            mark.dataset.state = DELIVERY_TONE[d.status] || "";
            li.append(main, mark);
            list.appendChild(li);
        });
        list.hidden = !data.deliveries.length;
        $id("hook-empty").hidden = !!data.deliveries.length;
    }

    async function hookAction(button, work) {
        $id("hook-error").textContent = "";
        $id("hook-result").textContent = "";
        button.disabled = true;
        try {
            await work();
        } catch (e) {
            $id("hook-error").textContent = e.message;
        }
        button.disabled = false;
    }

    $id("hook-save").addEventListener("click", function () {
        hookAction(this, async function () {
            var r = await api("PUT", "/accounts/me/webhook", { url: $id("hook-url").value.trim() });
            $id("hook-secret").value = r.secret;
            $id("hook-secret-box").hidden = false;
            showHook(r.url);
        });
    });
    $id("hook-secret-copy").addEventListener("click", function () { copy("hook-secret", this); });
    $id("hook-test").addEventListener("click", function () {
        hookAction(this, async function () {
            $id("hook-result").textContent = "Sending…";
            var r = await api("POST", "/accounts/me/webhook/test");
            $id("hook-result").textContent = r.delivered
                ? "Your shop answered OK: test event delivered."
                : "Not delivered: " + r.error + ". Check the URL, and that your server answers 2xx quickly.";
            loadDeliveries();
        });
    });
    $id("hook-remove").addEventListener("click", function () {
        hookAction(this, async function () {
            await api("DELETE", "/accounts/me/webhook");
            $id("hook-url").value = "";
            $id("hook-secret-box").hidden = true;
            showHook(null);
        });
    });
    $id("open-hooks").addEventListener("click", loadDeliveries);

    var sampleText = "";
    async function showSample(file) {
        document.querySelectorAll("[data-sample]").forEach(function (b) {
            b.setAttribute("aria-pressed", String(b.dataset.sample === file));
        });
        try {
            var res = await fetch("samples/" + file, { cache: "no-cache" });
            if (!res.ok) throw new Error("HTTP " + res.status);
            sampleText = (await res.text()).split("https://pay.example.com").join(location.origin);
        } catch (e) {
            sampleText = "Couldn't load the sample (" + e.message + ").";
        }
        $id("sample").firstElementChild.textContent = sampleText;
    }
    document.querySelectorAll("[data-sample]").forEach(function (b) {
        b.addEventListener("click", function () { showSample(b.dataset.sample); });
    });
    $id("open-integration").addEventListener("click", function () {
        $id("api-base").textContent = location.origin;
        if (!sampleText) showSample("python.py");
    });
    $id("sample-copy").addEventListener("click", function () {
        var button = this;
        if (!navigator.clipboard) return;
        navigator.clipboard.writeText(sampleText).then(function () {
            button.textContent = "Copied";
            setTimeout(function () { button.textContent = "Copy code"; }, 1500);
        });
    });

    $id("new-key-copy").addEventListener("click", function () { copy("new-key-value", this); });
    $id("new-key").addEventListener("click", async function () {
        // Two clicks: the old key stops working, which breaks the shop until it's updated.
        if (this.dataset.armed !== "1") {
            this.dataset.armed = "1";
            this.textContent = "Click again: replace my API key";
            return;
        }
        this.dataset.armed = "";
        this.textContent = "Make a new API key";
        try {
            var r = await api("POST", "/accounts/me/api-key");
            $id("new-key-value").value = r.api_key;
            $id("new-key-box").hidden = false;
            showKeyInfo(r.api_key.slice(0, 7), null);
        } catch (e) {
            showError("Couldn't make a new API key (" + e.message + ").");
        }
    });

    // --- Send ------------------------------------------------------------------------------------------
    var sendChain = null, sendAsset = null;

    function showSend(b, from) {
        sendChain = b.chain;
        sendAsset = b.asset;
        $id("send-speed-box").hidden = !!TRON[b.chain]; // Tron fees are fixed by the network
        $id("send-title").textContent = "Send " + b.asset;
        $id("send-unit").textContent = b.asset;
        $id("send-network").textContent = label(b.chain, b.asset)[1];
        $id("send-to").value = "";
        $id("send-amount").value = "";
        $id("send-fiat").textContent = "";
        $id("send-max").checked = false;
        $id("send-amount").disabled = false;
        $id("send-error").textContent = "";
        $id("send-review").disabled = false;
        openScreen("send", from);
        $id("send-amount").focus();
    }

    $id("send-amount").addEventListener("input", function () {
        var m = market[sendAsset], n = Number(this.value);
        $id("send-fiat").textContent = !TESTNETS[sendChain] && m && m.price != null && n > 0 ? "≈ " + money(n * m.price) : "";
    });

    function showSigner() {
        $id("forget-box").hidden = true;
        $id("vault").hidden = false;
        $id("vault").scrollIntoView({ block: "start" });
    }

    function hideSigner() {
        $id("vault").hidden = true;
        $id("forget-box").hidden = false;
    }

    $id("send-max").addEventListener("change", function () {
        $id("send-amount").disabled = this.checked;
        if (this.checked) { $id("send-amount").value = ""; $id("send-fiat").textContent = "Your whole balance, minus the fee"; }
        else $id("send-fiat").textContent = "";
    });

    $id("send-review").addEventListener("click", async function () {
        var to = $id("send-to").value.trim(), amount = $id("send-amount").value.trim(), max = $id("send-max").checked;
        var error = !to ? "Enter the address to send to." : (!max && !/^\d+(\.\d+)?$/.test(amount) ? "Enter an amount, like 0.005." : "");
        $id("send-error").textContent = error;
        if (error) return;
        var button = this;
        button.disabled = true;
        $id("send-result").hidden = true;
        try {
            var prep = await api("GET", "/wallet/send/" + sendChain + "?asset=" + encodeURIComponent(sendAsset) + "&to=" + encodeURIComponent(to));
        } catch (err) {
            $id("send-error").textContent = err.message;
            button.disabled = false;
            return;
        }
        showSigner();
        var speed = $id("send-speed").value;
        var request = { id: String(Date.now()), chain: sendChain, asset: sendAsset, to: to, amount: amount, max: max };
        if (prep.kind === "evm") {
            request.fee = prep.fees[speed];
            request.gas_limit = prep.gas_limit;
            request.sources = prep.sources;
            request.main = prep.main;
        } else if (prep.kind === "tron") {
            request.params = prep.params;
            request.block = prep.block;
            request.to_active = prep.to_active;
            request.sources = prep.sources;
            request.main = prep.main;
        } else {
            request.fee_per_byte = prep.fee_per_byte[speed];
            request.utxos = prep.utxos;
            request.change_index = prep.change_index;
        }
        frame.contentWindow.postMessage({ type: "sign-send", request: request }, "*");
    });

    // The frame asked for the signing engine. It checks every file's SHA-256 itself before running it.
    async function sendEngine(names) {
        var files = {}, transfer = [];
        try {
            for (var i = 0; i < names.length; i++) {
                if (!/^[a-z_.-]+$/.test(names[i])) continue;
                var res = await fetch("vendor/wallet-core/" + names[i]);
                if (!res.ok) throw new Error("HTTP " + res.status);
                files[names[i]] = await res.arrayBuffer();
                transfer.push(files[names[i]]);
            }
        } catch (err) {
            showError("Couldn't download the signing engine (" + err.message + "). Check your connection and try again.");
        }
        frame.contentWindow.postMessage({ type: "engine-files", files: files }, "*", transfer);
    }

    function txLinks(result, chain, hashes, name) {
        hashes.forEach(function (hash, i) {
            var link = el("a", "", name + (hashes.length > 1 ? " " + (i + 1) : ""));
            link.href = EXPLORER[chain] + encodeURIComponent(hash.replace(/^0x/, chain in TRON ? "" : "0x"));
            link.rel = "noreferrer";
            link.target = "_blank";
            result.append(link, " ");
        });
    }

    // Gas top-ups must land before the token transfers that spend them are relayed.
    async function waitFor(chain, hashes) {
        var until = Date.now() + TOPUP_WAIT_MS;
        while (Date.now() < until) {
            await new Promise(function (r) { setTimeout(r, 3000); });
            var states = await Promise.all(hashes.map(function (h) {
                return api("GET", "/wallet/tx/" + chain + "/" + encodeURIComponent(h)).catch(function () { return { done: false }; });
            }));
            if (states.some(function (st) { return st.done && !st.ok; })) return "a gas top-up failed on the network";
            if (states.every(function (st) { return st.done; })) return null;
        }
        return "the gas top-ups haven't confirmed yet";
    }

    function sentHome() { // the result shows on the wallet's first screen
        showTab("wallet");
        openScreen("home");
    }

    async function broadcastSigned(msg) {
        hideSigner();
        $id("send-review").disabled = false;
        var result = $id("send-result");
        var topups = msg.topups || [];
        if (topups.length) {
            try {
                var t = await api("POST", "/wallet/send/" + msg.chain, { txs: topups });
            } catch (err) {
                $id("send-error").textContent = "Not sent: " + err.message;
                return;
            }
            sentHome();
            result.textContent = "Sending gas from your main address to " + topups.length + " payment address" + (topups.length > 1 ? "es" : "") + ", then the transfer. Keep this page open… ";
            txLinks(result, msg.chain, t.tx_hashes, "Gas top-up");
            result.hidden = false;
            var problem = t.error || await waitFor(msg.chain, t.tx_hashes);
            if (problem) {
                result.textContent = "Not sent: " + problem + ". No " + msg.symbol + " moved. Any gas that arrived stays on those addresses; try Send again in a minute. ";
                txLinks(result, msg.chain, t.tx_hashes, "Gas top-up");
                loadBalances();
                return;
            }
        }
        try {
            var r = await api("POST", "/wallet/send/" + msg.chain, { txs: msg.txs });
        } catch (err) {
            if (topups.length) { result.textContent = "Gas was sent, but the transfer wasn't: " + err.message + ". Try Send again."; result.hidden = false; }
            else $id("send-error").textContent = "Not sent: " + err.message;
            return;
        }
        sentHome();
        result.textContent = (r.error ? "Partly sent: " + r.tx_hashes.length + " of " + msg.txs.length + " transfers went through. " + r.error + " "
                                      : "Sent " + msg.amount + " " + msg.symbol + " to " + msg.to + " (network fee " + msg.fee + "). ");
        txLinks(result, msg.chain, r.tx_hashes, r.tx_hashes.length > 1 ? "Transfer" : "View transaction");
        result.hidden = false;
        loadBalances();
    }

    function copy(inputId, button) {
        var input = $id(inputId);
        var done = function () {
            button.textContent = "Copied";
            setTimeout(function () { button.textContent = "Copy"; }, 1500);
        };
        if (navigator.clipboard) navigator.clipboard.writeText(input.value).then(done, function () { input.select(); });
        else input.select();
    }

    $id("refresh").addEventListener("click", loadDashboard);
    $id("scan").addEventListener("click", async function () {
        var button = this, out = $id("scan-result");
        button.disabled = true;
        out.hidden = false;
        out.textContent = "Checking every payment address for coins that arrived late. This can take a minute…";
        try {
            var r = await api("POST", "/wallet/scan");
            out.textContent = "Checked " + r.checked + " address" + (r.checked === 1 ? "" : "es") + ". " +
                (r.found ? "Found coins on " + r.found + "; they're now in your balances and payments." : "No late coins found.") +
                (r.failed ? " " + r.failed + " couldn't be checked right now; try again later." : "");
            if (r.found) loadDashboard();
        } catch (e) {
            out.textContent = "Couldn't check the addresses (" + e.message + ").";
        }
        button.disabled = false;
    });
    $id("receive-copy").addEventListener("click", function () { copy("receive-address", this); });

    ["lock", "lock-top", "lock-settings"].forEach(function (id) { $id(id).addEventListener("click", startFrame); });

    $id("forget").addEventListener("click", function () {
        try { localStorage.removeItem(VAULT_KEY); } catch (e) { /* nothing saved, nothing to remove */ }
        frame.src = "about:blank";
        clearTimeout(timer);
        $id("vault").hidden = true;
        $id("none").hidden = false;
    });

    startFrame();
})();
