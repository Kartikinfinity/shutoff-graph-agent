"""Produce the narrated demo video from the running app.

    uvicorn shutoff.api:app --port 8000        # in another terminal
    python demo/produce_video.py --voice <path to en_GB-cori-high.onnx>

Steps (all local, no paid services):
  1. Narration: each caption line is synthesized with Piper (open-source, offline
     neural TTS; the en_GB-cori-high voice is trained on public-domain LibriVox
     recordings). Captions and audio therefore share exact timings.
  2. Screen: Playwright drives the real UI at 1920x1080 and records it. Captions
     are drawn on screen at the moment each line is spoken; zooms use the app's own
     "Fit incident" control or a CSS zoom of the workspace. A black flash at the
     start calibrates audio against video.
  3. Assembly: ffmpeg (imageio-ffmpeg build) muxes video + narration into H.264/AAC
     MP4 with loudness normalisation, and an .srt caption file is written.

Everything on screen is the live application and FalkorDB; the script only types,
clicks, zooms and overlays the caption text.
"""

from __future__ import annotations

import argparse
import array
import json
import re
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

URL = "http://localhost:8000/"
DEMO = Path(__file__).resolve().parent
BUILD = DEMO / "build"
W, H = 1920, 1080
GAP = 0.35  # pause after each line (s)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


@dataclass
class Line:
    caption: str
    say: str | None = None          # spoken text when it differs (pronunciation)
    action: str | None = None       # name of an action run when the line starts
    hold: float = 0.0               # extra pause after the line
    audio: Path | None = None
    dur: float = 0.0
    start: float = 0.0              # measured, seconds since the recording clock origin
    extra: dict = field(default_factory=dict)


FALKOR = "Falcor DB"  # spelling that the TTS voice pronounces closest to "Falkor DB" (checked with ASR)
SCRIPT = [
    # 1. The problem
    Line("When a water main breaks, someone has to decide, quickly, which valves to close.", action="intro"),
    Line("It's harder than it sounds. Water networks are full of loops, and one stuck valve can make a shutdown much bigger."),
    Line("Closing the wrong area can also cut off homes that are nowhere near the break."),
    Line("ShutOff keeps the network as a graph in FalkorDB. This town is simulated.",
         say=f"ShutOff keeps the network as a graph in {FALKOR}. This town is simulated."),
    # 2. Report and plan
    Line("A caller reports water gushing on Maple Street, between Second and Third Avenue.", action="report"),
    Line("ShutOff locates that pipe, then asks FalkorDB which pipes stay connected if every working valve acts as a wall.",
         say=f"ShutOff locates that pipe, then asks {FALKOR} which pipes stay connected if every working valve acts as a wall.",
         action="fit_incident"),
    Line("That group is the smallest area that can be shut off. The four valves around it are the ones to close.",
         action="spot_valves"),
    Line("Before anything changes, ShutOff tests the plan on a copy of the graph.", action="spot_verdict"),
    Line("The break is cut off from every source. 34 accounts lose water, and no critical customer is affected.",
         say="The break is cut off from every source. Thirty four accounts lose water, and no critical customer is affected.",
         action="spot_kpis"),
    # 3. Evidence and risk
    Line("Each claim comes with a path from the graph: the hospital keeps a route from the tank, 14 connections long.",
         say="Each claim comes with a path from the graph. The hospital keeps a route from the tank, fourteen connections long.",
         action="hospital_path"),
    Line("ShutOff also warns about a risk: valve V-265 has not been exercised since 2011.",
         say="ShutOff also warns about a risk. Valve V two six five has not been exercised since twenty eleven.",
         action="spot_risk"),
    # 4. The naive plan
    Line("The crew suggests closing only the two valves nearest the break. ShutOff checks that first.",
         action="crew_plan"),
    Line("It fails. The purple line is the path FalkorDB found: water still reaches the broken pipe from the reservoir.",
         say=f"It fails. The purple line is the path {FALKOR} found. Water still reaches the broken pipe from the reservoir.",
         action="zoom_leak"),
    Line("Two valves are not enough.", hold=0.3),
    # 5. The seized valve
    Line("What if valve V-265 cannot be closed? ShutOff has already worked that out.",
         say="What if valve V two six five cannot be closed? ShutOff has already worked that out.", action="preview"),
    Line("This preview is hypothetical: seven valves, a dialysis account cut off, and nothing recorded.",
         say="This preview is hypothetical. Seven valves, a dialysis account cut off, and nothing recorded."),
    Line("Now the crew confirms that V-265 is seized. That is saved in the graph, and the plan is recalculated.",
         say="Now the crew confirms that V two six five is seized. That is saved in the graph, and the plan is recalculated.",
         action="seized"),
    Line("Seven valves now. 66 accounts lose water, and one critical customer is cut off.",
         say="Seven valves now. Sixty six accounts lose water, and one critical customer is cut off.",
         action="spot_kpis"),
    Line("It's a home-dialysis account on Willow Court: outside the shut-off area, but its only supply runs through it.",
         say="It's a home dialysis account on Willow Court. It's outside the shut off area, but its only supply runs through it.",
         action="zoom_willow"),
    # 6. Approval
    Line("ShutOff never closes anything by itself. The supervisor must name the critical customer before approving.",
         action="ack"),
    Line("The server then re-checks the plan, and refuses if anything has changed."),
    Line("Approval records work order WO-1001 and marks the valves closed in the graph.",
         say="Approval records work order W O one zero zero one, and marks the valves closed in the graph.",
         action="approve"),
    Line("This is a simulation: no crew is dispatched, and nothing is sent to anyone.", hold=0.3),
    # 7. Why FalkorDB
    Line("So, why use a graph database? Every decision here is a question about what is connected to what.",
         action="evidence"),
    Line("In FalkorDB, that's a direct question: components find the area, a graph copy tests the plan, shortest paths show the proof.",
         say=f"In {FALKOR}, that's a direct question. Components find the area, a graph copy tests the plan, and shortest paths show the proof.",
         action="spot_evidence"),
    Line("We also checked the segmentation against the U.S. EPA's WNTR toolkit on public networks. It matched exactly.",
         say="We also checked the segmentation against the U.S. E P A's W N T R toolkit, on public networks. It matched exactly."),
    # 8. Limits
    Line("ShutOff models connectivity, not water pressure, and it doesn't control real valves.", action="fit_network"),
    Line("Within those limits, every recommendation it makes is checked and explained.", hold=0.8),
]

OVERLAY_JS = r"""
() => {
  const cap = document.createElement('div');
  cap.id = '__cap';
  cap.style.cssText = 'position:fixed;left:50%;bottom:42px;transform:translateX(-50%);width:max-content;max-width:1060px;' +
    'pointer-events:none;background:rgba(8,14,26,.90);color:#fff;font:600 27px/1.38 system-ui,"Segoe UI",sans-serif;' +
    'padding:12px 22px;border-radius:14px;z-index:1000;box-shadow:0 10px 34px rgba(0,0,0,.35);text-align:center;display:none';
  document.body.appendChild(cap);
  const spot = document.createElement('div');
  spot.id = '__spot';
  spot.style.cssText = 'position:fixed;z-index:999;pointer-events:none;border:3px solid #facc15;border-radius:12px;' +
    'box-shadow:0 0 0 9999px rgba(10,16,28,.28),0 0 18px rgba(250,204,21,.6);opacity:0;transition:opacity .35s ease, all .45s ease';
  document.body.appendChild(spot);
  const card = document.createElement('div');
  card.id = '__card';
  card.style.cssText = 'position:fixed;inset:0;z-index:1001;display:none;flex-direction:column;align-items:center;justify-content:center;' +
    'background:#0f1b2d;color:#e8eef7;font-family:system-ui,"Segoe UI",sans-serif;text-align:center';
  document.body.appendChild(card);
  const ws = document.getElementById('workspace');
  ws.style.transition = 'transform .9s cubic-bezier(.2,.7,.2,1)';
  document.body.style.overflow = 'hidden';
  window.__caption = (t) => { cap.textContent = t || ''; cap.style.display = t ? 'block' : 'none'; };
  window.__card = (html, bg) => { if (!html) { card.style.display = 'none'; return; } card.innerHTML = html;
    card.style.background = bg || '#0f1b2d'; card.style.display = 'flex'; };
  window.__spot = (sel, pad) => {
    const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
    if (!el) { spot.style.opacity = 0; return false; }
    const r = el.getBoundingClientRect(), p = pad ?? 8;
    Object.assign(spot.style, { left: (r.left - p) + 'px', top: (r.top - p) + 'px', width: (r.width + 2 * p) + 'px', height: (r.height + 2 * p) + 'px', opacity: 1 });
    return true;
  };
  window.__unspot = () => { spot.style.opacity = 0; };
  window.__zoom = (sel, scale) => {
    if (!sel) { ws.style.transform = ''; return; }
    const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
    const wr = ws.getBoundingClientRect(), r = el.getBoundingClientRect();
    ws.style.transformOrigin = `${r.left + r.width / 2 - wr.left}px ${r.top + r.height / 2 - wr.top}px`;
    ws.style.transform = `scale(${scale})`;
  };
}
"""

TITLE = ("<div style='font:800 76px system-ui'>ShutOff</div>"
         "<div style='font:500 30px/1.5 system-ui;color:#9fb0c8;margin-top:18px'>Deciding which valves to close after a water-main break,<br>"
         "using a graph of the network in FalkorDB</div>"
         "<div style='font:600 22px system-ui;color:#fcd58a;margin-top:26px'>Graph Hacks · Track 1: Agents That Act on Connected Data · simulated network</div>")
END = ("<div style='font:800 64px system-ui'>ShutOff</div>"
       "<div style='font:500 27px/1.6 system-ui;color:#9fb0c8;margin-top:16px'>FalkorDB: connected components, graph copies and shortest paths<br>"
       "decide the plan, test it, and show the evidence. Humans approve every change.</div>"
       "<div style='font:600 24px system-ui;color:#e8eef7;margin-top:24px'>github.com/Kartikinfinity/shutoff-graph-agent</div>"
       "<div style='font:500 20px system-ui;color:#fcd58a;margin-top:18px'>Simulated network · connectivity only, not pressure · dispatch and notices simulated</div>")


# ---------------------------------------------------------------- 1. narration
def synthesize(voice_path: str, length_scale: float) -> None:
    from piper import PiperVoice, SynthesisConfig

    voice = PiperVoice.load(voice_path)
    cfg = SynthesisConfig(length_scale=length_scale)
    BUILD.mkdir(exist_ok=True)
    for i, line in enumerate(SCRIPT):
        line.audio = BUILD / f"line{i:02d}.wav"
        with wave.open(str(line.audio), "wb") as wf:
            voice.synthesize_wav(line.say or line.caption, wf, syn_config=cfg)
        with wave.open(str(line.audio)) as wf:
            line.dur = wf.getnframes() / wf.getframerate()


NEGATIONS = {"not", "no", "nothing", "never", "cannot", "doesn't", "won't", "isn't", "without"}


def qa_lines(voice_path: str, length_scale: float, tries: int = 5) -> list[str]:
    """Speech-recognise each synthesized line. A line whose negation word is not heard
    (a meaning flip, e.g. 'not' heard as 'plant') is re-synthesized; Piper's sampling
    noise gives a different rendering each time. Returns a short report."""
    import numpy as np
    from faster_whisper import WhisperModel
    from piper import PiperVoice, SynthesisConfig

    asr = WhisperModel("base.en", device="cpu", compute_type="int8")
    voice = PiperVoice.load(voice_path)
    cfg = SynthesisConfig(length_scale=length_scale)

    def heard(path: Path) -> str:
        with wave.open(str(path)) as wf:
            sr = wf.getframerate()
            x = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        t = np.linspace(0, len(x) / sr, int(len(x) * 16000 / sr), endpoint=False)
        y = np.interp(t, np.arange(len(x)) / sr, x).astype(np.float32)
        return " ".join(seg.text for seg in asr.transcribe(y, beam_size=5)[0]).lower()

    report = []
    for i, line in enumerate(SCRIPT):
        words = set(re.findall(r"[a-z']+", (line.say or line.caption).lower()))
        need = words & NEGATIONS
        if not need:
            continue
        for attempt in range(1, tries + 1):
            got = set(re.findall(r"[a-z']+", heard(line.audio)))
            missing = {w for w in need if w not in got}
            if not missing:
                report.append(f"line {i}: negation heard ({', '.join(sorted(need))}) after {attempt} synthesis")
                break
            with wave.open(str(line.audio), "wb") as wf:
                voice.synthesize_wav(line.say or line.caption, wf, syn_config=cfg)
            with wave.open(str(line.audio)) as wf:
                line.dur = wf.getnframes() / wf.getframerate()
        else:
            report.append(f"line {i}: WARNING negation {sorted(missing)} still not heard after {tries} tries")
    return report


# ---------------------------------------------------------------- 2. screen
def record() -> tuple[Path, float]:
    raw = BUILD / "raw"
    if raw.exists():
        shutil.rmtree(raw)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(raw),
                                  record_video_size={"width": W, "height": H})
        ctx.add_init_script("try { localStorage.setItem('shutoff-theme', 'light'); } catch (e) {}")
        pg = ctx.new_page()
        clock0 = time.monotonic()
        pg.request.post(URL + "api/reset")
        pg.goto(URL)
        pg.wait_for_selector("#map line", state="attached")
        pg.wait_for_timeout(800)
        pg.evaluate(OVERLAY_JS)

        def idle() -> None:
            pg.wait_for_function("document.querySelector('#progress').hidden", timeout=30000)
            pg.wait_for_timeout(350)

        def send(text: str) -> None:
            pg.click("#input")
            pg.type("#input", text, delay=18)
            pg.click("#composer button")
            idle()

        actions = {
            "intro": lambda: None,
            "report": lambda: send("Caller reports water gushing on Maple St between 2nd and 3rd Ave"),
            "fit_incident": lambda: (pg.wait_for_timeout(1800), pg.click("#fit-incident")),
            "spot_valves": lambda: pg.evaluate("() => { const h=[...document.querySelectorAll('#plan .sec h3')].find(x=>/Valves to close/.test(x.textContent)); h.parentElement.scrollIntoView({block:'center'}); setTimeout(()=>__spot(h.parentElement), 350); }"),
            "spot_verdict": lambda: pg.evaluate("() => { document.querySelector('#plan').scrollTo({top:0}); setTimeout(()=>__spot('#plan .verdict'), 300); }"),
            "spot_kpis": lambda: pg.evaluate("() => __spot('#kpis', 6)"),
            "hospital_path": lambda: (pg.evaluate("() => __unspot()"), pg.click("#fit-network"), pg.wait_for_timeout(900),
                                      pg.click('#plan [data-cust="C-HOSP"]'), pg.wait_for_timeout(1500),
                                      pg.evaluate("() => __spot('#plan [data-cust=\"C-HOSP\"]', 4)")),
            "spot_risk": lambda: pg.evaluate("() => { const v=[...document.querySelectorAll('#plan .verdict.warn')][0]; v.scrollIntoView({block:'center'}); setTimeout(()=>__spot(v), 300); }"),
            "crew_plan": lambda: (pg.evaluate("() => __unspot()"), pg.click('#plan [data-cust="C-HOSP"]'),
                                  send("Crew wants to just close V-212 and V-213, is that enough?")),
            "zoom_leak": lambda: (pg.wait_for_timeout(200), pg.evaluate("() => __zoom('#map .trace.leak', 1.45)")),
            "preview": lambda: (pg.evaluate("() => __zoom(null)"), pg.wait_for_timeout(900),
                                pg.click('#plan .link-btn[data-cont="V-265"]'), pg.wait_for_timeout(600),
                                pg.evaluate("() => __spot('#preview-tag', 6)")),
            "seized": lambda: (pg.evaluate("() => __unspot()"), pg.click('#plan .link-btn[data-cont="V-265"]'),
                               send("Crew reports V-265 is seized")),
            "zoom_willow": lambda: (pg.evaluate("() => __unspot()"),
                                    pg.evaluate("() => __zoom([...document.querySelectorAll('#map .cust-name')].find(t=>/Dialysis/.test(t.textContent)), 1.8)")),
            "ack": lambda: (pg.evaluate("() => __zoom(null)"), pg.wait_for_timeout(1000),
                            pg.evaluate("() => __spot('#dock', 2)"), pg.wait_for_timeout(2600), pg.check("#ack")),
            "approve": lambda: (pg.click("#approve"), idle(), pg.evaluate("() => __spot('#dock .verdict', 6)")),
            "evidence": lambda: (pg.evaluate("() => __unspot()"),
                                 pg.evaluate("() => { const d=document.querySelector('#plan .evidence details'); d.open=true; d.scrollIntoView({block:'start'}); }")),
            "spot_evidence": lambda: pg.evaluate("() => __spot('#plan .evidence ul', 8)"),
            "fit_network": lambda: (pg.evaluate("() => __unspot()"), pg.click("#fit-network")),
        }

        # calibration flash, then title
        pg.evaluate("() => __card(' ', '#000000')")
        flash_at = time.monotonic() - clock0
        pg.wait_for_timeout(500)
        pg.evaluate(f"() => __card({json.dumps(TITLE)})")
        pg.wait_for_timeout(3300)
        pg.evaluate("() => __card(null)")
        pg.wait_for_timeout(600)

        for line in SCRIPT:
            pg.evaluate("(t) => __caption(t)", line.caption)
            line.start = time.monotonic() - clock0
            if line.action:
                actions[line.action]()
            remaining = line.start + line.dur + GAP + line.hold - (time.monotonic() - clock0)
            if remaining > 0:
                pg.wait_for_timeout(int(remaining * 1000))
        pg.evaluate("() => { __caption(''); __unspot(); }")
        pg.wait_for_timeout(500)
        pg.evaluate(f"() => __card({json.dumps(END)})")
        end_at = time.monotonic() - clock0
        pg.wait_for_timeout(4200)
        video = Path(pg.video.path())
        ctx.close()
        browser.close()
    out = BUILD / "screen.webm"
    shutil.move(str(video), out)
    shutil.rmtree(raw, ignore_errors=True)
    (BUILD / "timeline.json").write_text(json.dumps({
        "flash_at": flash_at, "end_card_at": end_at,
        "lines": [{"caption": l.caption, "start": l.start, "dur": l.dur} for l in SCRIPT]}, indent=1))
    return out, flash_at


# ---------------------------------------------------------------- 3. assembly
def flash_time_in_video(video: Path) -> float:
    """Time (s) of the first pure-black frame: the calibration flash. (The browser's blank
    page before load is white and the title card is navy, so black is unambiguous.)"""
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", str(video), "-t", "15", "-vf",
                          "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    t = None
    for m in re.finditer(r"pts_time:([\d.]+).*?\n.*?YAVG=([\d.]+)", out):
        if float(m.group(2)) < 20:
            t = float(m.group(1))
            break
    if t is None:
        raise RuntimeError("calibration flash not found in recording")
    return t


def build_audio(offset: float, total: float, rate: int = 22050) -> Path:
    samples = array.array("h", [0]) * int((total + 1) * rate)
    for line in SCRIPT:
        with wave.open(str(line.audio)) as wf:
            assert wf.getframerate() == rate and wf.getsampwidth() == 2 and wf.getnchannels() == 1
            data = array.array("h", wf.readframes(wf.getnframes()))
        at = int((line.start + offset) * rate)
        samples[at:at + len(data)] = data
    out = BUILD / "narration.wav"
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())
    return out


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def assemble(video: Path, flash_clock: float, out_mp4: Path) -> dict:
    flash_video = flash_time_in_video(video)
    offset = flash_video - flash_clock            # video time = clock time + offset
    trim = flash_video + 0.55                      # start the final cut on the title card
    timeline = json.loads((BUILD / "timeline.json").read_text())
    total = timeline["end_card_at"] + offset + 4.2
    narration = build_audio(offset, total)
    cues = []
    for i, line in enumerate(SCRIPT, 1):
        a = line.start + offset - trim
        cues.append(f"{i}\n{srt_time(a)} --> {srt_time(a + line.dur + 0.15)}\n{line.caption}\n")
    out_mp4.with_suffix(".srt").write_text("\n".join(cues), encoding="utf-8")
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{trim:.3f}", "-i", str(video),
           "-ss", f"{trim:.3f}", "-i", str(narration),
           "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
           "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000",
           "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out_mp4)]
    subprocess.run(cmd, check=True)
    return {"offset": offset, "trim": trim, "flash_video": flash_video}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", required=True)
    ap.add_argument("--length-scale", type=float, default=1.0)
    ap.add_argument("--out", default=str(DEMO / "shutoff-demo.mp4"))
    a = ap.parse_args()
    synthesize(a.voice, a.length_scale)
    for r in qa_lines(a.voice, a.length_scale):
        print("qa:", r)
    speech = sum(l.dur for l in SCRIPT)
    print(f"narration: {len(SCRIPT)} lines, {speech:.1f}s of speech")
    video, flash = record()
    info = assemble(video, flash, Path(a.out))
    print(json.dumps(info), "->", a.out)


if __name__ == "__main__":
    main()
