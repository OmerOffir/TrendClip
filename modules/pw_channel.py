"""The Pricetag Why channel (@PricetagWhy): whiteboard answers to the money questions everyone wonders about, drawn
by Borderline Why's renderer in its own colours (modules.pw_render). Brief: channel-brief-pricetag-why.md.

This first step makes a video from a hand-checked script (the same JSON shape as a Borderline Why script, with a
"tag" scene first and "sources" checked by hand, two or more per claim); the schedule, Discord and YouTube come next.

    python -m modules.pw_channel make content/pricetagwhy/scripts/001-arizona-99c.json [--key sample-arizona]

The video is drawn in this process of its own (pw_render.use_style changes module-wide colours). Files:
output/pricetagwhy/<key>/ (script.json, voice.mp3, video.mp4, info.json).
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from modules import bw_render, channel_common
from modules.scene_images import setting
from trendclip.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

CHANNEL = "pricetagwhy"
NAME = "Pricetag Why"
DIR = channel_common.channel_dir(CHANNEL)
OUTPUT_DIR = PROJECT_ROOT / "output" / CHANNEL
SCRIPTS_DIR = PROJECT_ROOT / "content" / CHANNEL / "scripts"  # hand-checked scripts, kept in git
MIN_SECONDS, MAX_SECONDS = 30.0, 60.0  # the brief asks 35-50 s
VOICE = "af_heart"  # Kokoro-82M (Apache 2.0, commercial use allowed): warm and curious, unlike Borderline Why's voices
MUSIC = "funny"  # a music/ folder; PW_MUSIC=none for the voice alone
DISCLAIMER = "Not financial advice, just the fascinating reasons behind the prices."
HASHTAGS = ("money", "economics", "pricetagwhy", "shorts")


class ChannelError(RuntimeError):
    pass


def handle() -> str:
    return channel_common.handle(DIR) or "@PricetagWhy"


def out_root(out_dir: Path | None = None) -> Path:
    return out_dir or OUTPUT_DIR


def narration(script: dict) -> str:
    return " ".join(" ".join(str(s.get("narration") or "").split()) for s in script.get("scenes") or [] if s.get("narration"))


def words(text: str) -> int:
    return len(re.findall(r"[\w'¢$%.,]+", text))


def check_script(script: dict) -> list[str]:
    """The brief's rules a script must meet before it is drawn."""
    problems = []
    scenes = script.get("scenes") or []
    if not scenes or scenes[0].get("kind") != "tag":
        problems.append("the first scene must be the price tag (kind tag)")
    if not scenes or scenes[-1].get("kind") != "follow":
        problems.append("the last scene must be the follow line (kind follow)")
    sources = script.get("sources") or []
    if len(sources) < 2:
        problems.append("fewer than two sources")
    for c in script.get("claims") or []:
        if len(set(c.get("sources") or [])) < 2:
            problems.append(f"claim checked in fewer than two sources: {c.get('claim', '')[:60]}")
        if any(not 1 <= n <= len(sources) for n in c.get("sources") or []):
            problems.append(f"claim cites a source that isn't listed: {c.get('claim', '')[:60]}")
    if not script.get("claims"):
        problems.append("no claims listed with their sources")
    advice = re.compile(r"\b(you should (buy|invest|sell)|buy this|invest in|financial advice)\b", re.I)
    if advice.search(narration(script)):
        problems.append("the narration gives advice (explain, never advise)")
    return problems


def description(script: dict) -> str:
    """The YouTube description: the question, the short answer, the sources, the credits, the disclaimer, hashtags."""
    tags = list(dict.fromkeys([*(script.get("hashtags") or []), *HASHTAGS]))[:7]
    lines = [script.get("question", ""), "", script.get("description", ""), "", "Sources:"]
    lines += [f"- {s['title']}: {s['url']}" for s in script.get("sources", [])]
    lines += ["", "Whiteboard animation drawn by code. Icons: Noto Emoji by Google (Apache 2.0; animated Noto Emoji, "
              "CC BY 4.0). No AI-generated images.", "", DISCLAIMER, "", f"A new 'why' every day: {handle()}",
              "", " ".join(f"#{t}" for t in tags)]
    return "\n".join(lines).strip()


def record(text: str, out: Path, progress=lambda m: None) -> list[bw_render.Word]:
    """The narration in the channel's voice: Kokoro (the hub's) when installed, else an Edge voice."""
    from trendclip import kokoro_voice

    if kokoro_voice.available() and setting("PW_TTS_ENGINE", "kokoro").lower() == "kokoro":
        voice = setting("PW_TTS_VOICE", VOICE)
        progress(f"Recording the narration (Kokoro {voice})")
        got = kokoro_voice.synthesize(text, out, voice, float(setting("PW_TTS_SPEED", "1.0")))
        kokoro_voice.unload()
    else:
        from trendclip import voiceover

        voice = setting("PW_EDGE_VOICE", "en-US-EmmaNeural")
        progress(f"Recording the narration ({voice})")
        got = voiceover.synthesize(text, out, voice, setting("PW_TTS_RATE", "+5%"))
    return [bw_render.Word(w.text, float(w.start), float(w.end)) for w in got]


def pick_music(seed: int) -> Path | None:
    folder = setting("PW_MUSIC", MUSIC)
    if folder.lower() in ("none", "off", "0"):
        return None
    tracks = sorted((PROJECT_ROOT / "music" / folder).glob("*.mp3"))
    return random.Random(seed).choice(tracks) if tracks else None


def make(script: dict, key: str, out_dir: Path | None = None, progress=lambda f, m: None) -> dict:
    """Draw a script into output/pricetagwhy/<key>/ (in this process: call it from `python -m modules.pw_channel`)."""
    from modules import bw_brand, pw_render

    if problems := check_script(script):
        raise ChannelError("The script breaks the rules: " + "; ".join(problems))
    pw_render.use_style()
    work = out_root(out_dir) / key
    work.mkdir(parents=True, exist_ok=True)
    script = {**script, "narration": narration(script), "look": "satellite", "brand": True}
    script.setdefault("seed", random.randrange(1, 10_000))
    (work / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    progress(0.05, "Recording the narration")
    spoken = record(script["narration"], work / "voice.mp3", lambda m: progress(None, m))
    spec = bw_render.spec_from_dict(script, spoken)
    spec.hold = bw_brand.END_HOLD["short"]  # the end card stays a moment after the follow line
    if missing := bw_render.prepare(spec):
        raise ChannelError("Places not found on the map: " + ", ".join(missing))
    music = pick_music(int(script["seed"]))
    pw_render.render(spec, work / "voice.mp3", work / "video.mp4", music, float(setting("PW_MUSIC_VOLUME", "0.07")),
                     lambda f, m: progress(0.1 + 0.85 * (f or 0), m), handle=handle())
    info = {
        "key": key, "title": script["title"], "question": script.get("question", ""), "description": description(script),
        "hashtags": list(dict.fromkeys([*(script.get("hashtags") or []), *HASHTAGS]))[:6],
        "sources": script.get("sources", []), "claims": script.get("claims", []), "duration_seconds": round(spec.duration, 2),
        "words": words(script["narration"]), "voice": setting("PW_TTS_VOICE", VOICE), "music": music.stem if music else None,
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
    }
    seconds = info["duration_seconds"]
    info["check"] = [] if MIN_SECONDS <= seconds <= MAX_SECONDS else [f"{seconds:.0f} s long (wanted {MIN_SECONDS:.0f}-{MAX_SECONDS:.0f})"]
    (work / "info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    progress(1.0, "Video ready")
    return info


def start_make(script_path: Path, key: str, out_dir: Path | None = None) -> subprocess.Popen:
    """make() in a process of its own (the dashboard's way in), its log in output/pricetagwhy/<key>.log."""
    root = out_root(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    log = (root / f"{key}.log").open("w")
    cmd = [sys.executable, "-m", "modules.pw_channel", "make", str(script_path), "--key", key] + (["--out", str(out_dir)] if out_dir else [])
    return subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


# --------------------------------------------------------------------------- what the dashboard shows


def scripts() -> list[dict[str, Any]]:
    """The hand-checked scripts waiting in content/pricetagwhy/scripts/, with their rule check."""
    out = []
    for path in sorted(SCRIPTS_DIR.glob("*.json")):
        script = json.loads(path.read_text(encoding="utf-8"))
        out.append({"file": path.name, "title": script.get("title", ""), "question": script.get("question", ""),
                    "words": words(narration(script)), "sources": script.get("sources", []),
                    "claims": script.get("claims", []), "check": check_script(script)})
    return out


def videos(out_dir: Path | None = None) -> list[dict[str, Any]]:
    root = out_root(out_dir)
    found = []
    for path in root.glob("*/info.json"):
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        info["key"] = path.parent.name
        info["has_video"] = (path.parent / "video.mp4").is_file()
        found.append(info)
    return sorted(found, key=lambda v: v.get("created_at", ""), reverse=True)


def video_path(key: str, out_dir: Path | None = None) -> Path | None:
    if not re.fullmatch(r"[\w.-]+", key or ""):
        return None
    path = out_root(out_dir) / key / "video.mp4"
    return path if path.is_file() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m modules.pw_channel")
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="draw a hand-checked script")
    m.add_argument("script", type=Path)
    m.add_argument("--key", default="")
    m.add_argument("--out", type=Path, default=None)
    sub.add_parser("check", help="the rule check of every script in content/pricetagwhy/scripts/")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.cmd == "check":
        for s in scripts():
            print(f"{s['file']}: {s['words']} words, {len(s['sources'])} sources, " + ("OK" if not s["check"] else "; ".join(s["check"])))
        return 0
    script = json.loads(args.script.read_text(encoding="utf-8"))
    key = args.key or f"{datetime.now():%Y-%m-%d_%H%M}"
    info = make(script, key, args.out, lambda f, m: print(f"[{'' if f is None else f'{f:.0%}'}] {m}", flush=True))
    print(json.dumps({k: info[k] for k in ("key", "title", "duration_seconds", "words", "check")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
