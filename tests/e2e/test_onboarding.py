"""Sign-up: create a wallet, import a phrase, restore on a new device, xpub-only accounts."""
import re

from conftest import PHRASE, frame, import_wallet, set_password



def test_create_wallet_confirms_backup_and_gets_api_key(page):
    page.goto("/")
    page.click("[data-mode=create]")
    f = frame(page)
    f.locator("#words li").nth(11).wait_for()
    words = f.locator("#words li").all_inner_texts()
    assert len(words) == 12
    assert f.locator("#c-next").is_disabled(), "can't continue before confirming the backup"
    f.locator("#c-ack").check()
    f.locator("#c-next").click()

    checks = f.locator("#c-checks input")
    assert checks.count() == 3
    checks.nth(0).fill("wrong")
    f.locator("#c-done").click()
    assert "don't match" in f.locator("#c-error").inner_text()
    for i in range(3):
        pos = int(checks.nth(i).get_attribute("data-pos"))
        checks.nth(i).fill(words[pos].strip())
    f.locator("#c-done").click()

    set_password(f, "short")  # rejected: too short
    assert f.locator("#p-error").inner_text()
    set_password(f)
    page.wait_for_selector("#result:not([hidden])")
    assert re.fullmatch(r"gw_[\w-]{40,}", page.input_value("#api-key"))
    assert page.is_visible("#vault-saved"), "the encrypted wallet is saved on this device"
    assert page.locator("#addresses").inner_text().count("\n") >= 4, "first addresses are listed"


def test_import_phrase_then_restore_on_another_device(page, browser, server):
    # Account 7 of the test phrase: used by no other test, so this import is always the first one.
    from bip_utils import Bip39SeedGenerator, Bip44, Bip44Changes, Bip44Coins
    seed = Bip39SeedGenerator(PHRASE).Generate()
    first = (Bip44.FromSeed(seed, Bip44Coins.BITCOIN).Purpose().Coin().Account(7)
             .Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PublicKey().ToAddress())
    key = import_wallet(page, account=7)
    assert key and key.startswith("gw_")
    assert first in page.locator("#addresses").inner_text(), "addresses match other wallets (BIP44)"

    other = browser.new_context(base_url=server).new_page()
    try:
        assert import_wallet(other, account=7) is None
        assert other.is_visible("#restored"), "same phrase again = restore, never a second account"
    finally:
        other.context.close()


def test_invalid_phrase_is_refused_in_the_frame(page):
    page.goto("/")
    page.click("[data-mode=import]")
    f = frame(page)
    f.locator("#i-phrase").fill(PHRASE.replace("about", "abandon"))  # bad checksum
    f.locator("#i-done").click()
    assert "isn't a valid recovery phrase" in f.locator("#i-error").inner_text()


def test_xpub_only_signup(page):
    page.goto("/xpub.html")
    page.fill("#x-BTC", "xpub-not-valid")
    page.click("button[type=submit]")
    assert page.locator("#e-BTC").inner_text(), "bad xpub is pointed out next to its field"
