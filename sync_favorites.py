"""
sync_favorites.py - Browser-based sync of finn.no shared favorites list.

Runs a Playwright Chromium browser in a background thread.
The Flask app communicates with the thread via shared state + threading events.

Flow:
  1. start_sync(url, callback) - launches browser, navigates to list, logs in
  2. Browser redirects to login; email is filled automatically
  3. Finn.no sends a 6-digit code to the email
  4. State becomes "waiting_for_code"
  5. User submits the code via the web UI -> submit_code(code) is called
  6. Browser fills in the code, completes login, navigates to the favorites list
  7. All finnkodes are extracted from the page HTML
  8. Each apartment is scraped via normal HTTP and saved via the callback
  9. State becomes "done"
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable

# ---------------------------------------------------------------------------
# Shared state — read by Flask routes, written by the browser thread
# ---------------------------------------------------------------------------

_state: dict = {
    "status": "idle",   # idle | starting | waiting_for_code | scraping | done | error
    "message": "",
    "log": [],
    "found": 0,
    "scraped": 0,
    "added": 0,
    "updated": 0,
}
_lock = threading.Lock()
_code_event = threading.Event()
_pending_code: list[str | None] = [None]


def get_state() -> dict:
    with _lock:
        return {**_state, "log": list(_state["log"])}


def submit_code(code: str) -> None:
    """Called from a Flask request thread to pass the OTP to the browser thread."""
    _pending_code[0] = code.strip()
    _code_event.set()


def is_running() -> bool:
    with _lock:
        return _state["status"] not in ("idle", "done", "error")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    with _lock:
        _state["message"] = msg
        _state["log"].append(msg)


def _set_status(status: str, msg: str = "") -> None:
    with _lock:
        _state["status"] = status
        if msg:
            _state["message"] = msg
            _state["log"].append(msg)


def _reset() -> None:
    with _lock:
        _state.update({
            "status": "starting",
            "message": "Starter nettleser...",
            "log": [],
            "found": 0,
            "scraped": 0,
            "added": 0,
            "updated": 0,
        })
    _code_event.clear()
    _pending_code[0] = None


# ---------------------------------------------------------------------------
# Public: start the sync
# ---------------------------------------------------------------------------

def start_sync(list_url: str, email: str, on_apt_scraped: Callable[[str, str], str]) -> None:
    """
    Start the favorites sync in a background thread.

    on_apt_scraped(finnkode, url) -> action ("Lagt til" | "Oppdatert")
      Called for each apartment found in the list. Must be thread-safe.
    """
    _reset()
    t = threading.Thread(target=_run, args=(list_url, email, on_apt_scraped), daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Background thread: browser automation
# ---------------------------------------------------------------------------

def _run(list_url: str, email: str, on_apt_scraped: Callable[[str, str], str]) -> None:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        _set_status("error", "Playwright er ikke installert. Kjoer: pip install playwright && playwright install chromium")
        return

    SCREENSHOT_DIR = __import__("pathlib").Path(__file__).parent / "screenshots"
    SCREENSHOT_DIR.mkdir(exist_ok=True)

    def screenshot(page, name: str) -> None:
        try:
            p = SCREENSHOT_DIR / f"{name}.png"
            page.screenshot(path=str(p), full_page=True)
            _log(f"Screenshot: screenshots/{name}.png")
        except Exception as e:
            _log(f"Screenshot feilet ({name}): {e}")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="nb-NO",
                ignore_https_errors=True,
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()

            # ----------------------------------------------------------------
            # Login loop — retry if we end up back on a login page
            # ----------------------------------------------------------------
            for login_attempt in range(3):
                _log(f"Navigerer til favorittlisten (forsok {login_attempt + 1})...")
                page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)
                screenshot(page, f"01_after_goto_attempt{login_attempt+1}")
                _log(f"URL etter goto: {page.url}")

                # Fill email if login page appeared
                try:
                    email_el = page.wait_for_selector(
                        'input[type="email"], input[name="email"], input[name="identifier"]',
                        timeout=8000,
                    )
                    _log(f"Fyller inn e-post: {email}")
                    email_el.fill(email)
                    screenshot(page, f"02_email_filled_attempt{login_attempt+1}")
                    page.keyboard.press("Enter")
                    time.sleep(3)
                    screenshot(page, f"03_after_email_submit_attempt{login_attempt+1}")
                    _log(f"URL etter e-post: {page.url}")
                except PWTimeout:
                    _log("Ingen innlogging nodvendig.")
                    break

                # Ask user for OTP
                _code_event.clear()
                _pending_code[0] = None
                _set_status("waiting_for_code",
                            f"Sjekk e-posten din og skriv inn den 6-sifrede koden (forsok {login_attempt + 1}).")

                if not _code_event.wait(timeout=600):
                    _set_status("error", "Tidsavbrudd: ingen kode mottatt innen 10 minutter.")
                    browser.close()
                    return

                code = (_pending_code[0] or "").strip()
                _log(f"Kode mottatt ({code}). Fyller inn...")
                _set_status("starting", f"Fyller inn kode...")

                screenshot(page, f"04_before_otp_attempt{login_attempt+1}")

                # Fill OTP — type each digit individually (finn.no processes them one by one)
                try:
                    otp_el = page.wait_for_selector(
                        'input[autocomplete="one-time-code"], '
                        'input[type="text"][maxlength="6"], '
                        'input[name="code"], '
                        'input[placeholder*="kode"], '
                        'input[placeholder*="Code"]',
                        timeout=10000,
                    )
                    otp_el.click()
                    for digit in code:
                        page.keyboard.press(digit)
                        time.sleep(0.5)
                    screenshot(page, f"05_otp_filled_attempt{login_attempt+1}")
                    # Do NOT press Enter — wait for auto-submit after last digit
                    time.sleep(2)
                except PWTimeout:
                    screenshot(page, f"05_otp_field_not_found_attempt{login_attempt+1}")
                    _set_status("error", "Fant ikke kodefeltet pa siden.")
                    browser.close()
                    return

                # Wait up to 10s for the browser to leave login domains
                _log("Venter pa at innlogging fullforers...")
                for tick in range(10):
                    time.sleep(1)
                    cur = page.url
                    if "login.vend.no" not in cur and "spid.no" not in cur and "finn.no/auth" not in cur:
                        _log(f"Innlogget! URL: {cur}")
                        break
                    if tick % 5 == 4:
                        _log(f"Fortsatt venter... URL: {cur}")
                else:
                    screenshot(page, f"06_still_on_login_attempt{login_attempt+1}")
                    _log(f"Fortsatt pa login-side etter 10s: {page.url} — prover pa nytt")
                    continue  # next login_attempt

                screenshot(page, f"06_after_login_attempt{login_attempt+1}")
                break  # successfully left the login page
            else:
                screenshot(page, "06_login_failed_all_attempts")
                _set_status("error", "Klarte ikke logge inn etter 3 forsok.")
                browser.close()
                return

            # ----------------------------------------------------------------
            # Step 5: Make sure we are on the favorites list
            # ----------------------------------------------------------------
            if "sharedfavoritelist" not in page.url:
                _log("Navigerer til favorittlisten etter innlogging...")
                page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)
                screenshot(page, "07_after_favorites_goto")

            current_url = page.url
            _log(f"Gjeldende URL: {current_url}")

            if "login" in current_url:
                screenshot(page, "07_still_on_login")
                _set_status("error", f"Innlogging mislyktes. URL: {current_url}")
                browser.close()
                return

            # Wait for apartment card links to appear
            _log("Venter pa at leiligheter lastes inn...")
            try:
                page.wait_for_selector('a[href^="/4"], a[href^="/5"]', timeout=20000)
            except PWTimeout:
                screenshot(page, "08_no_cards_found")
                _log("Advarsel: fant ikke annonselenker etter 20 sek, prover likevel...")

            # Scroll to trigger lazy-loading
            _log("Laster inn alle leiligheter (scroller siden)...")
            for _ in range(10):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1)

            screenshot(page, "09_after_scroll")

            html = page.content()

            try:
                with open("/tmp/finn_favorites_debug.html", "w", encoding="utf-8") as fh:
                    fh.write(html)
            except Exception:
                pass

            # Extract finnkodes from bare href="/460094226" links
            finnkodes = list(dict.fromkeys(re.findall(r'href="/(\d{6,})"', html)))
            if not finnkodes:
                finnkodes = list(dict.fromkeys(re.findall(r"finnkode=(\d+)", html)))
            browser.close()

            with _lock:
                _state["found"] = len(finnkodes)
            _log(f"Fant {len(finnkodes)} leiligheter: {', '.join(finnkodes[:10])}{' ...' if len(finnkodes) > 10 else ''}")
            _log("Henter detaljer via HTTP...")

            # ----------------------------------------------------------------
            # Step 6: Scrape each apartment (plain HTTP, no browser needed)
            # ----------------------------------------------------------------
            _set_status("scraping")
            for i, fk in enumerate(finnkodes, 1):
                apt_url = f"https://www.finn.no/realestate/homes/ad.html?finnkode={fk}"
                _log(f"[{i}/{len(finnkodes)}] Henter finnkode {fk}...")
                try:
                    action = on_apt_scraped(fk, apt_url)
                    with _lock:
                        _state["scraped"] = i
                        if action == "Lagt til":
                            _state["added"] += 1
                        else:
                            _state["updated"] += 1
                except Exception as exc:
                    _log(f"Feil for {fk}: {exc}")
                time.sleep(0.5)  # be polite to finn.no

            with _lock:
                added = _state["added"]
                updated = _state["updated"]
                total = _state["found"]
            _set_status("done", f"Ferdig! {added} nye og {updated} oppdatert av {total} leiligheter.")

    except Exception as exc:
        _set_status("error", f"Uventet feil: {exc}")
