"""Screenshots of the running app, driven through the real UI.

    python demo/shoot.py                 # light + dark at 1600x900, plus 1366x768 and 390x844 checks

Writes docs/screenshots/*.png and prints any browser console errors.
"""
import sys

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/"
OUT = "docs/screenshots"


def idle(pg):
    pg.wait_for_function("!document.querySelector('#progress') || document.querySelector('#progress').hidden", timeout=30000)
    pg.wait_for_timeout(500)


def say(pg, msg):
    pg.fill("#input", msg)
    pg.click("#composer button")
    idle(pg)


def run(p, theme, size, prefix, full=True):
    b = p.chromium.launch()
    ctx = b.new_context(viewport=size, device_scale_factor=1)
    ctx.add_init_script(f"try {{ localStorage.setItem('shutoff-theme', '{theme}'); }} catch (e) {{}}")
    pg = ctx.new_page()
    errors = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.request.post(URL + "api/reset")
    pg.goto(URL)
    pg.wait_for_selector("#map line", state="attached")
    idle(pg)
    shot = lambda name: pg.screenshot(path=f"{OUT}/{prefix}{name}.png")  # noqa: E731
    if full:
        shot("0-start")
    if size["width"] < 1440:  # drawer and phone layouts: start from the plan view's primary button
        pg.click('#plan [data-msg]')
        idle(pg)
    else:
        say(pg, "Caller reports water gushing on Maple St between 2nd and 3rd Ave")
    shot("1-plan-a")
    if not full:
        b.close()
        return errors
    say(pg, "Crew wants to just close V-212 and V-213, is that enough?")
    shot("2-crew-plan-rejected")
    pg.click('[data-cont="V-265"]')
    pg.wait_for_timeout(400)
    shot("3-contingency-preview")
    pg.click('[data-cont="V-265"]')
    say(pg, "Crew reports V-265 is seized")
    shot("4-plan-b-observed")
    pg.check("#ack")
    shot("5-acknowledge")
    pg.click("#approve")
    idle(pg)
    shot("6-work-order-recorded")
    say(pg, "Is the hospital still supplied?")
    shot("7-hospital-path")
    b.close()
    return errors


with sync_playwright() as p:
    errs = []
    errs += run(p, "light", {"width": 1600, "height": 900}, "light-")
    errs += run(p, "dark", {"width": 1600, "height": 900}, "dark-")
    errs += run(p, "light", {"width": 1366, "height": 768}, "check-1366-", full=False)
    errs += run(p, "dark", {"width": 390, "height": 844}, "check-390-", full=False)
print("console errors:", errs or "none")
