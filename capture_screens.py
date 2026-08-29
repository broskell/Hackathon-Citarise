"""Capture README screenshots of the running app (http://localhost:8000).

    python capture_screens.py

Writes crisp PNGs to docs/screenshots/. Requires the server running and
`playwright install chromium` done once.
"""

import os

from playwright.sync_api import sync_playwright

OUT = "docs/screenshots"
URL = "http://localhost:8000"


def set_theme(page, theme):
    page.evaluate(
        "(t) => { localStorage.setItem('lodestar-theme', t);"
        "document.documentElement.setAttribute('data-theme', t); }", theme)


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)

        # ---- hero, dark ----
        page.goto(URL, wait_until="networkidle")
        set_theme(page, "dark")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1600)  # let the hero intro settle
        page.screenshot(path=f"{OUT}/01-hero-dark.png")

        # ---- hero, light ----
        set_theme(page, "light")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1600)
        page.screenshot(path=f"{OUT}/02-hero-light.png")

        # ---- capabilities + pipeline (dark, full section) ----
        set_theme(page, "dark")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(600)
        page.eval_on_selector("#how", "el => el.scrollIntoView()")
        page.wait_for_timeout(1200)
        page.screenshot(path=f"{OUT}/03-how-it-works.png")

        # ---- run a scenario in the console, capture the completed shell ----
        for theme, name in (("dark", "04-console-dark"), ("light", "05-console-light")):
            set_theme(page, theme)
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(600)
            page.eval_on_selector("#console", "el => el.scrollIntoView()")
            # scenario B shows the richest run (geocode + retry + credit + diff)
            page.eval_on_selector('.scen-tab[data-key="B"]', "el => el.click()")
            page.wait_for_timeout(300)
            page.eval_on_selector("#runBtn", "el => el.click()")
            # wait until the run finishes (button re-enabled) or timeout
            try:
                page.wait_for_function("() => document.querySelector('#runBtn') && !document.querySelector('#runBtn').disabled && document.querySelectorAll('#trace .step').length > 3", timeout=45000)
            except Exception:
                pass
            page.wait_for_timeout(1200)  # let the typewriter finish
            page.eval_on_selector(".console-shell", "el => el.scrollIntoView({block:'start'})")
            page.evaluate("window.scrollBy(0, -90)")
            page.wait_for_timeout(300)
            shell = page.query_selector(".console-shell")
            shell.screenshot(path=f"{OUT}/{name}.png")

        browser.close()
    print("wrote screenshots to", OUT)
    for f in sorted(os.listdir(OUT)):
        print(" -", f)


if __name__ == "__main__":
    main()
