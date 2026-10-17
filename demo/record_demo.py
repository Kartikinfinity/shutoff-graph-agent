"""Record the demo video against the real running app.

    uvicorn shutoff.api:app --port 8000      # in another terminal
    python demo/record_demo.py               # writes demo/shutoff-demo.webm

Every result on screen comes from the live app and FalkorDB. This script only
types the messages and overlays caption text (the dark banner at the bottom),
which is not part of the app.
"""

import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/"
OUT = Path(__file__).resolve().parent
RAW = OUT / "raw"
W, H = 1600, 900

CAPTION_JS = """
(text) => {
  let el = document.getElementById('__cap');
  if (!el) {
    el = document.createElement('div');
    el.id = '__cap';
    el.style.cssText = 'position:fixed;left:50%;top:70px;transform:translateX(-50%);max-width:1100px;pointer-events:none;' +
      'background:rgba(10,18,32,.92);color:#fff;font:600 21px/1.35 system-ui,Segoe UI,sans-serif;' +
      'padding:12px 20px;border-radius:12px;z-index:99;box-shadow:0 8px 30px rgba(0,0,0,.35);text-align:center';
    document.body.appendChild(el);
  }
  el.style.display = text ? 'block' : 'none';
  el.textContent = text;
}
"""

CARD_JS = """
([title, sub]) => {
  let el = document.getElementById('__card');
  if (!title) { if (el) el.remove(); return; }
  if (!el) { el = document.createElement('div'); el.id = '__card'; document.body.appendChild(el); }
  el.style.cssText = 'position:fixed;inset:0;z-index:100;display:flex;flex-direction:column;align-items:center;' +
    'justify-content:center;background:#0f1b2d;color:#e8eef7;font-family:system-ui,Segoe UI,sans-serif;text-align:center';
  el.innerHTML = '<div style="font:800 64px system-ui">' + title + '</div>' +
    '<div style="font:500 26px/1.5 system-ui;color:#9fb0c8;margin-top:18px;max-width:1100px">' + sub + '</div>';
}
"""


def main() -> None:
    if RAW.exists():
        shutil.rmtree(RAW)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(RAW),
                                  record_video_size={"width": W, "height": H})
        pg = ctx.new_page()
        pg.request.post(URL + "api/reset")
        pg.goto(URL)
        pg.wait_for_selector("#map line", state="attached")

        cap = lambda t: pg.evaluate(CAPTION_JS, t)  # noqa: E731
        card = lambda t, s="": pg.evaluate(CARD_JS, [t, s])  # noqa: E731
        wait = pg.wait_for_timeout

        def say(msg: str) -> None:
            pg.click("#input")
            pg.type("#input", msg, delay=22)
            wait(300)
            pg.click("#composer button")
            pg.wait_for_function("!document.querySelector('.typing')", timeout=30000)
            wait(500)

        card("ShutOff", "A water-main-break isolation agent built on FalkorDB<br>"
                        "Graph Hacks · Track 1: Agents That Act on Connected Data<br>"
                        "<span style='color:#fcd58a'>All network data is synthetic</span>")
        wait(5000)
        card("")
        cap("A dispatcher reports a main break. ShutOff turns it into graph queries on FalkorDB.")
        wait(3500)

        say("Caller reports water gushing on Maple St between 2nd and 3rd Ave")
        cap("algo.WCC finds the isolation segment. Closing its 4 boundary valves is simulated on a GRAPH.COPY sandbox: break isolated, 34 accounts out.")
        wait(7000)
        cap("It also warns: V-265 has not been exercised since 2011. If it fails, a home-dialysis account would be cut off.")
        pg.locator("#plan").evaluate("el => el.scrollTo({top: 9999, behavior: 'smooth'})")
        wait(7000)
        pg.locator("#plan").evaluate("el => el.scrollTo({top: 0, behavior: 'smooth'})")
        cap("Every tool call is evidence: the exact Cypher, parameters, rows and latency, including sandbox queries.")
        pg.locator("#evidence").evaluate("el => el.scrollTo({top: 260, behavior: 'smooth'})")
        wait(6000)

        cap("The crew proposes closing only the two nearest valves. ShutOff checks that plan first.")
        say("Crew wants to just close V-212 and V-213, is that enough?")
        cap("Rejected. algo.SPpaths shows the exact route water still takes from the reservoir to the break (purple).")
        wait(7500)

        say("Is the hospital still supplied?")
        cap("Supply is proven with a path from Hilltop Tank, not asserted.")
        wait(5000)

        cap("Field report: V-265 will not turn. It becomes :Seized in the graph, and every later plan routes around it.")
        say("Crew: V-265 won't turn, it's seized")
        cap("Re-planned in under a second: 7 valves, 66 accounts, and an unintended cut-off of Willow Ct, including the medical-priority account.")
        wait(8500)

        cap("The model cannot close valves. Cutting off a critical customer also needs an explicit acknowledgement, enforced by the server.")
        pg.locator("#plan").evaluate("el => el.scrollTo({top: 9999, behavior: 'smooth'})")
        wait(4500)
        pg.check("#ack")
        cap("Approval is bound to the impact shown: the server re-simulates and refuses if anything changed.")
        wait(3500)
        pg.click(".approve")
        pg.wait_for_function("!document.querySelector('.typing')", timeout=30000)
        cap("WO-1001 written to the graph: valves closed, priority notice recorded. Dispatch and notices are simulated.")
        wait(7000)

        cap("Unknown places are never guessed.")
        say("Burst main on Elm St at 5th")
        wait(3500)
        say("Leak at Oak & 3rd")
        cap("Ambiguous reports return the candidate mains from the graph.")
        wait(4500)

        cap("Prevention: simulate every valve being seized, and rank which ones to exercise first.")
        say("Which valves should we exercise first?")
        wait(7000)

        cap("Restore re-opens valves only after a sandbox check that no other active break would be re-fed.")
        pg.click("#wo-bar button")
        pg.wait_for_function("!document.querySelector('.typing')", timeout=30000)
        wait(3500)
        cap("")
        card("ShutOff", "FalkorDB graph algorithms (WCC, SPpaths) + sandbox simulation via GRAPH.COPY<br>"
                        "Fixed tools, no model-written Cypher · human-approved actions · MCP server · 40 tests<br>"
                        "<span style='color:#fcd58a'>Synthetic data · dispatch and notices simulated</span>")
        wait(5000)
        video = pg.video.path()
        ctx.close()
        browser.close()
    dest = OUT / "shutoff-demo.webm"
    shutil.move(video, dest)
    shutil.rmtree(RAW, ignore_errors=True)
    print(dest)


if __name__ == "__main__":
    main()
