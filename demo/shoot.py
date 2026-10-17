"""Full-resolution screenshots of the running app (python demo/shoot.py)."""
import sys
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/"
OUT = "docs/screenshots"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
    pg.goto(URL)
    pg.request.post(URL + "api/reset")
    pg.reload()
    pg.wait_for_selector("#map line", state="attached")
    def say(msg):
        pg.fill("#input", msg)
        pg.click("#composer button")
        pg.wait_for_function("!document.querySelector('.typing')")
        pg.wait_for_timeout(400)
    say("Caller reports water gushing on Maple St between 2nd and 3rd Ave")
    pg.screenshot(path=f"{OUT}/1-plan-a.png")
    say("Crew wants to just close V-212 and V-213, is that enough?")
    pg.screenshot(path=f"{OUT}/2-naive-plan-rejected.png")
    say("Crew: V-265 won't turn, it's seized")
    pg.screenshot(path=f"{OUT}/3-plan-b-seized-valve.png")
    pg.click(".approve")
    pg.wait_for_function("!document.querySelector('.typing')")
    pg.wait_for_timeout(400)
    pg.screenshot(path=f"{OUT}/4-work-order-issued.png")
    b.close()
print("ok")
