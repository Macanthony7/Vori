from pathlib import Path


def edit(f, old, new):
    p = Path(f)
    s = p.read_text(encoding="utf-8")
    assert s.count(old) == 1, (f, old[:70])
    p.write_text(s.replace(old, new), encoding="utf-8")


d = "frontend/public/"
edit(d + "wallet.html", '''                <label for="charge-amount">Amount</label>
                <input id="charge-amount" inputmode="decimal" autocomplete="off">
                <label for="charge-network">Coin and network</label>
                <select id="charge-network"></select>''', '''                <label for="charge-amount">Amount</label>
                <div class="copy-row">
                    <input id="charge-amount" inputmode="decimal" autocomplete="off">
                    <select id="charge-unit" aria-label="Amount in">
                        <option value="coin" selected>in a coin</option>
                        <option value="USD">USD</option>
                        <option value="EUR">EUR</option>
                    </select>
                </div>
                <div id="charge-network-box">
                    <label for="charge-network">Coin and network</label>
                    <select id="charge-network"></select>
                </div>
                <p id="charge-priced-note" class="note" hidden>The payer picks any coin you can receive. The amount is converted at the current rate when they choose.</p>''')
edit(d + "wallet.js", '''    $id("charge-create").addEventListener("click", async function () {
        var button = this, pair = $id("charge-network").value.split("/");
        $id("charge-error").textContent = "";
        $id("charge-result").hidden = true;
        button.disabled = true;
        try {
            var p = await api("POST", "/payments", { chain: pair[0], asset: pair[1], amount: $id("charge-amount").value.trim() });
            var link = el("a", "", "Open the checkout page");
            link.href = "/pay/" + encodeURIComponent(p.id);
            link.target = "_blank";
            link.rel = "noopener";
            $id("charge-result").replaceChildren("Payment created: " + p.amount + " " + p.asset + " to " + p.address + ". ", link);''', '''    $id("charge-unit").addEventListener("change", function () {
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
                : p.price + " " + p.currency + ", the payer picks the coin") + ". ", link);''')
edit(d + "wallet.js", '''            var name = label(p.chain, p.asset);
            tr.append(created, el("td", "num", p.amount + " " + name[0] + " on " + name[1]), cell);''', '''            var what = p.chain ? p.amount + " " + label(p.chain, p.asset)[0] + " on " + label(p.chain, p.asset)[1]
                : p.price + " " + p.currency + " (payer picks the coin)";
            if (p.chain && p.price) what += " = " + p.price + " " + p.currency;
            tr.append(created, el("td", "num", what), cell);''')
edit(d + "wallet.js", '''    var STATUS = { pending: "Waiting", confirming: "Confirming", confirmed: "Paid", expired: "Expired" };''',
     '''    var STATUS = { choosing: "Choosing a coin", pending: "Waiting", confirming: "Confirming", confirmed: "Paid", expired: "Expired" };''')
print("wallet updated")
