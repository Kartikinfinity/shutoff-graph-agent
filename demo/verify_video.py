"""Objective checks of the final demo video (no human listening implied).

    python demo/verify_video.py [demo/shutoff-demo.mp4]

Reports: container/streams and duration; full-decode errors; loudness (EBU R128)
and true peak; caption-to-speech sync (speech onsets from silencedetect vs .srt
cue starts); and a local speech-recognition transcript (faster-whisper base.en)
compared word-by-word with the narration script.
"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import produce_video as P  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()


def run(*args: str) -> str:
    return subprocess.run([FF, "-hide_banner", *args], capture_output=True, text=True).stderr


def secs(t: str) -> float:
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def main(mp4: str) -> None:
    info = run("-i", mp4)
    print("streams:", *[l.strip()[:120] for l in info.splitlines() if "Duration" in l or "Stream" in l], sep="\n  ")
    dec = subprocess.run([FF, "-v", "error", "-i", mp4, "-f", "null", "-"], capture_output=True, text=True)
    print("full decode errors:", dec.stderr.strip() or "none")
    loud = run("-i", mp4, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-")
    i = re.findall(r"I:\s+([-\d.]+) LUFS", loud)
    pk = re.findall(r"Peak:\s+([-\d.]+) dBFS", loud)
    print(f"integrated loudness: {i[-1] if i else '?'} LUFS | true peak: {pk[-1] if pk else '?'} dBFS")

    srt = Path(mp4).with_suffix(".srt").read_text(encoding="utf-8")
    cues = [(secs(a), secs(b)) for a, b in re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", srt)]
    sd = run("-i", mp4, "-vn", "-af", "silencedetect=noise=-38dB:d=0.25", "-f", "null", "-")
    onsets = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", sd)]
    diffs = [round(min(onsets, key=lambda o: abs(o - a)) - a, 2) for a, _ in cues]
    print(f"captions: {len(cues)} cues; speech onset minus caption start: min {min(diffs)} s, max {max(diffs)} s")

    from faster_whisper import WhisperModel

    raw = subprocess.run([FF, "-v", "error", "-i", mp4, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    heard = " ".join(s.text.strip() for s in model.transcribe(audio, beam_size=5)[0])
    norm = lambda s: re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("-", " ")).split()  # noqa: E731
    e, h = norm(" ".join(l.say or l.caption for l in P.SCRIPT)), norm(heard)
    sm = difflib.SequenceMatcher(a=e, b=h, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    print(f"speech recognition: {matched}/{len(e)} script words heard in order ({matched / len(e):.0%})")
    print("differences (expected -> heard; digits vs words and 'shut off' vs 'ShutOff' are formatting):")
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op != "equal":
            print(f"  {' '.join(e[a1:a2])!r} -> {' '.join(h[b1:b2])!r}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent / "shutoff-demo.mp4"))
