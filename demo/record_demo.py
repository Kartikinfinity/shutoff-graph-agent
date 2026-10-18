"""Record the demo video through the real UI, against the running app and FalkorDB.

    uvicorn shutoff.api:app --port 8000      # in another terminal
    python demo/record_demo.py               # writes demo/shutoff-demo.webm

Every result on screen is real app output. This script only clicks, types and
overlays caption text (the dark banner), which is not part of the app.
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
    el.style.cssText = 'position:fixed;left:50%;bottom:120px;transform:translateX(-50%);max-width:820px;pointer-events:none;' +
      'background:rgba(8,14,26,.92);color:#fff;font:600 20px/1.35 system-ui,Segoe UI,sans-serif;' +
      'padding:11px 18px;border-radius:12px;z-index:99;box-shadow:0 8px 30px rgba(0,0,0,.35);text-align:center';
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
    '<div style="font:500 25px/1.55 system-ui;color:#9fb0c8;margin-top:18px;max-width:1150px">' + sub + '</div>';
}
"""


def main() -> None:
    if RAW.exists():
        shutil.rmtree(RAW)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(RAW),
                                  record_video_size={"width": W, "height": H})
        ctx.add_init_script("try { localStorage.setItem('shutoff-theme', 'light'); } catch (e) {}")
        pg = ctx.new_page()
        pg.request.post(URL + "api/reset")
        pg.goto(URL)
        pg.wait_for_selector("#map line", state="attached")

        cap = lambda t: pg.evaluate(CAPTION_JS, t)  # noqa: E731
        card = lambda t, s="": pg.evaluate(CARD_JS, [t, s])  # noqa: E731
        wait = pg.wait_for_timeout

        def idle() -> None:
            pg.wait_for_function("document.querySelector('#progress').hidden", timeout=30000)
            wait(500)

        def say(msg: str) -> None:
            pg.click("#input")
            pg.type("#input", msg, delay=20)
            wait(250)
            pg.click("#composer button")
            idle()

        card("ShutOff", "Water-main-break isolation, decided on a FalkorDB graph<br>"
                        "Graph Hacks · Track 1: Agents That Act on Connected Data<br>"
                        "<span style='color:#fcd58a'>Simulated network · deterministic planner (no live model in this recording)</span>")
        wait(5000)
        card("")
        cap("A dispatcher reports a broken main. ShutOff must isolate it without overlooking who is supplied through it.")
        wait(3500)
        pg.click("#starter .primary")
        idle()
        cap("FalkorDB finds the area valves can wall off (algo.WCC) and simulates closing its 4 valves on a copy of the graph: 34 accounts, no critical customer.")
        wait(7000)
        pg.click("#fit-incident")
        cap("These are proposed effects. Nothing in the network has changed yet.")
        wait(4000)
        pg.click("#fit-network")

        cap("The crew suggests closing only the two nearest valves. ShutOff checks it first.")
        say("Crew wants to just close V-212 and V-213, is that enough?")
        cap("Rejected: algo.SPpaths shows the route by which the reservoir still reaches the break (purple).")
        wait(6500)

        cap("Before anyone tries it: V-265 has not been exercised since 2011. What if it cannot close?")
        pg.click('#plan .link-btn[data-cont="V-265"]')
        wait(3000)
        cap("Hypothetical preview: 7 valves, 66 accounts, and the home-dialysis account on Willow Ct cut off. Nothing is recorded.")
        wait(6000)
        pg.click('#plan .link-btn[data-cont="V-265"]')

        cap("Now the crew reports an observation. It is saved in the graph, and the plan is recomputed.")
        say("Crew reports V-265 is seized")
        cap("Plan IR1-r2: 7 valves (+3), 66 accounts (+32), 1 critical customer (+1), outside the shut-off area.")
        wait(7000)

        cap("The supervisor must name the critical customer before approving. The server checks it, and re-checks the plan.")
        wait(3000)
        pg.check("#ack")
        wait(1500)
        pg.click("#approve")
        idle()
        cap("WO-1001 is recorded: valves marked closed in the graph, priority notice recorded. No crew is dispatched; no message is sent.")
        wait(7000)

        cap("With WO-1001's closures in the graph, does the hospital still have a source path?")
        say("Is the hospital still supplied?")
        cap("Yes: 14 hops from Hilltop Tank. Willow Ct does not. This is connectivity, not a pressure guarantee.")
        wait(6500)
        cap("")
        card("ShutOff", "FalkorDB derives the plan (WCC), tests it on a graph copy (GRAPH.COPY), proves it with paths (SPpaths)<br>"
                        "and records changes only after explicit human approval<br>"
                        "<span style='color:#9fb0c8'>Segmentation cross-checked against EPA's WNTR on public EPANET networks</span><br>"
                        "<span style='color:#fcd58a'>Synthetic network · connectivity only · dispatch and notices simulated</span>")
        wait(6000)
        video = pg.video.path()
        ctx.close()
        browser.close()
    dest = OUT / "shutoff-demo.webm"
    shutil.move(video, dest)
    shutil.rmtree(RAW, ignore_errors=True)
    print(dest)


if __name__ == "__main__":
    main()
