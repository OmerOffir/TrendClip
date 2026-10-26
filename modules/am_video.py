"""After Market video: one session's Short, from the facts to the finished file, in minutes before it posts.

    python -m modules.am_video                         # the slot that fits the clock, with Gemini
    python -m modules.am_video --slot close --template  # the built-in script (no Gemini)
    python -m modules.am_video --facts output/aftermarket/2026-10-27_close/facts.json --images placeholder

Flow (output/aftermarket/<date>_<slot>/):
  1. facts      modules.am_data (facts.json), unless --facts points at a saved one;
  2. script     modules.am_script with Gemini (script.json); --template or a Gemini failure: the template;
  3. voice      Kokoro on the GPU (AM_TTS_VOICE, default am_michael, AM_TTS_SPEED) or the Edge voice
                (AM_TTS_ENGINE=edge), which times every word;
  4. pictures   chart cards from modules.am_charts for board / ticker / crypto / calendar scenes; the
                "broll" scenes drawn by scene_images (the GPU, AM_IMAGE_BACKEND) with a finance style;
  5. motion     the Dark Outliers push-in / dissolves (modules.video_gen.build_background);
  6. assembly   trendclip.video_assembler: karaoke captions, each scene's overlay flashing in near the
                top, the thumbnail headline on frame 0, the end card, chill music (AM_MUSIC) low.
The result: video.mp4 and info.json (title, description with the sources and a disclaimer, hashtags).
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from modules import am_charts, am_data, am_script, scene_images, video_gen
from modules.scene_images import Picture, setting
from modules.video_gen import Beat

logger = logging.getLogger(__name__)

OUTPUT_DIR = am_data.OUTPUT_DIR
DEFAULT_VOICE = "am_michael"
DEFAULT_SPEED = "1.05"
DEFAULT_RATE = "+12%"  # the Edge engine
DEFAULT_ENGINE = "kokoro"
DEFAULT_CTA_LINE = {"premarket": "Follow for the close.", "close": "Follow for tomorrow's open.", "weekend": "Follow for Monday's open."}
DEFAULT_END_CARD = "Follow for the next session"
BROLL_STYLE = ", cinematic, photoreal, dark blue and amber tones, shallow depth of field, no text, no logos"
HASHTAGS = ["stockmarket", "stocks", "trading", "premarket", "earnings", "crypto", "shorts"]
DISCLAIMER = "Not financial advice. Data from Yahoo Finance, Nasdaq, ForexFactory, CoinGecko and alternative.me."
TAIL = 1.2
FLASH_Y = 0.24  # the overlay near the top (y 400-520 of 1920): the chart cards keep that band free
CARD_ZOOM = 0.05  # a gentle push-in: the cards keep their edges
ProgressFn = Callable[[float | None, str], None]


class VideoError(RuntimeError):
    pass


@dataclass
class AmBeat(Beat):
    visual: str = "board"  # board | ticker:<SYM> | crypto | calendar | broll


def channel_handle() -> str:
    from dotenv import dotenv_values

    path = am_data.PROJECT_ROOT / "channels" / "aftermarket" / "channel.env"
    value = ((dotenv_values(path).get("CHANNEL_HANDLE") or "") if path.is_file() else "").strip()
    return value if value.startswith("@") and len(value) > 1 else ""


def beats_of(script: am_script.Script, cta_line: str = "") -> list[Beat]:
    """The hook (over the first scene's picture), then every scene; the call to action joins the last."""
    first = script.scenes[0] if script.scenes else None
    beats = [AmBeat(0, "hook", script.hook_text, "", first.image_prompt if first and first.visual == "broll" else "",
                    visual=first.visual if first else "board")]
    beats += [AmBeat(s.scene_id, f"scene {s.scene_id}", s.narration, s.overlay_text, s.image_prompt, visual=s.visual)
              for s in script.scenes]
    if cta_line.strip():
        beats[-1].text = f"{beats[-1].text.strip()} {cta_line.strip()}"
    return beats


def description_for(script: am_script.Script, facts: dict, music_credit: str = "") -> str:
    h = channel_handle()
    lines = [script.hook_text.strip(), ""]
    if h:
        lines += [f"Follow {h} for the next session.", ""]
    lines.append(f"{facts.get('ny_time', '')} · {script.slot}".strip(" ·"))
    for m in (facts.get("movers") or [])[:5]:
        ext = f" · {m['session']} {m['extended_change_pct']:+.2f}%" if m.get("extended_change_pct") is not None else ""
        lines.append(f"{m['symbol']} {m['change_pct']:+.2f}%{ext}")
    lines += ["", DISCLAIMER]
    if music_credit:
        lines.append(music_credit)
    lines += ["", " ".join(f"#{t}" for t in HASHTAGS)]
    return "\n".join(lines).strip()


def pictures_for(beats: list[AmBeat], facts: dict, work: Path, images: str | None, seed: int,
                 progress: ProgressFn) -> tuple[list[Picture], str]:
    """One picture per beat: a chart card for a chart visual, the GPU for broll. A ticker card for a
    symbol the facts do not have falls back to the board."""
    cards = am_charts.render(facts, work / "pictures")
    pictures: list[Picture | None] = [None] * len(beats)
    broll = [(i, b.image_prompt + BROLL_STYLE) for i, b in enumerate(beats)
             if b.visual == "broll" and b.image_prompt.strip()]
    used = "cards"
    if broll:
        drawn, used_backend = scene_images.make_pictures([p for _, p in broll], work / "pictures" / "broll", images, seed=seed,
                                                         progress=lambda msg: progress(0.15, msg))
        used = f"cards + {used_backend}"
        for (i, _), pic in zip(broll, drawn):
            pictures[i] = Picture(scene_images.graded_copy(pic.path), pic.source, prompt=pic.prompt)
    for i, b in enumerate(beats):
        if pictures[i] is not None:
            continue
        path = cards.get(b.visual) or cards["board"]
        pictures[i] = Picture(path, "chart", prompt=b.visual)
    return [p for p in pictures if p], used


def make_video(facts: dict, script: am_script.Script, progress: ProgressFn = lambda f, m: None, *, images: str | None = None,
               out_dir: Path | None = None, settings=None) -> dict:
    """facts + script -> output/aftermarket/<date>_<slot>/video.mp4 and info.json."""
    from trendclip import voiceover
    from trendclip.config import get_settings
    from trendclip.quiz import TimedFlash
    from trendclip.video_assembler import assemble_video, media_duration

    settings = settings or get_settings()
    work = (out_dir or OUTPUT_DIR) / f"{facts['date']}_{facts['slot']}"
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()
    am_script.save(script, work / "script.json")

    cta_line = setting("AM_CTA_LINE", DEFAULT_CTA_LINE.get(script.slot, DEFAULT_CTA_LINE["close"]))
    cta_line = "" if video_gen.off(cta_line) else cta_line.strip()
    engine = setting("AM_TTS_ENGINE", DEFAULT_ENGINE).strip().lower()
    text = f"{am_script.spoken(script)} {cta_line}".strip()
    if engine == "kokoro":
        from trendclip import kokoro_voice

        voice = setting("AM_TTS_VOICE", DEFAULT_VOICE)
        rate = setting("AM_TTS_SPEED", DEFAULT_SPEED)
        progress(0.02, f"Recording the narration (Kokoro {voice})")
        words = kokoro_voice.synthesize(text, work / "voice.mp3", voice, float(rate))
        kokoro_voice.unload()
    else:
        voice = setting("AM_TTS_VOICE", settings.tts_voice)
        rate = setting("AM_TTS_RATE", DEFAULT_RATE)
        progress(0.02, f"Recording the narration ({voice})")
        words = voiceover.synthesize(text, work / "voice.mp3", voice, rate, local_rate=rate)
        from trendclip import local_voice

        local_voice.unload()
    beats = video_gen.split_beats(beats_of(script, cta_line), words, TAIL)

    progress(0.1, "Drawing the chart cards")
    seed = int(facts["date"].replace("-", "")) % 100000 + (0 if facts["slot"] == "premarket" else 7)
    pictures, backend_used = pictures_for(beats, facts, work, images, seed, progress)
    background = video_gen.build_background([p.path for p in pictures], beats, work / "pictures" / "background.mp4",
                                            progress=lambda f, m: progress(0.5 + 0.2 * (f or 0), m), zoom=CARD_ZOOM)

    from trendclip import music as music_mod

    track = None
    source = setting("AM_MUSIC", "folder:chill")
    if not video_gen.off(source):
        try:
            track = music_mod.pick(settings, source)
        except music_mod.MusicError as err:
            progress(None, f"No music: {err}")
    flashes = [TimedFlash(b.overlay_text, round(b.start + 0.2, 3), round(min(b.start + 3.2, b.end - 0.15), 3), "", FLASH_Y)
               for b in beats if b.overlay_text and b.duration > 1.0]
    end_card = setting("AM_END_CARD", f"{channel_handle()} · Follow for the next session" if channel_handle() else DEFAULT_END_CARD)
    end_card = "" if video_gen.off(end_card) else end_card
    overlays, stickers_used = video_gen.cta_sticker(settings, words, flashes, False, bool(end_card))
    cover = setting("AM_COVER", "auto")
    cover = "" if video_gen.off(cover) else (script.thumbnail_text or script.title).upper() if cover.lower() == "auto" else cover.upper()
    progress(0.75, "Rendering the Short")
    output = assemble_video(
        background, work / "voice.mp3", words, work / "video.mp4", fit="crop",
        progress=lambda f, m: progress(0.75 + 0.24 * (f or 0), m),
        music=music_mod.track_path(settings, track) if track else None,
        music_volume=float(setting("AM_MUSIC_VOLUME", "0.10")), music_fade_in=0.0,
        highlight=setting("AM_HIGHLIGHT", "yellow"), max_words=3, font_size=88, outline=7, margin_v=600,
        title_card="", flashes=flashes, flash_scale=0.8, end_card=end_card, overlays=overlays,
        title_pop=False, cover_text=cover, cover_seconds=0.8, tail_seconds=TAIL,
        preset=setting("AM_X264_PRESET", "fast"),
    )
    info = {
        "slot": facts["slot"], "date": facts["date"], "title": script.title, "filename": output.name,
        "description": description_for(script, facts, track.credit if track else ""), "hashtags": HASHTAGS,
        "duration_seconds": round(media_duration(output), 2), "voice": voice, "rate": rate, "engine": engine,
        "pictures": backend_used, "music": {"title": track.title, "credit": track.credit} if track else None,
        "cta_line": cta_line, "end_card": end_card, "stickers": stickers_used, "cover_text": cover, "writer": script.writer,
        "beats": [{**asdict(b), "picture": pictures[i].path.name, "picture_source": pictures[i].source}
                  for i, b in enumerate(beats)],
        "words": len(words), "seconds_to_make": round(time.time() - started), "problems": facts.get("problems", []),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (work / "info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    progress(1.0, "Done")
    return info


def produce(slot: str | None = None, progress: ProgressFn = lambda f, m: None, *, facts: dict | None = None,
            template: bool = False, images: str | None = None, out_dir: Path | None = None, settings=None) -> dict:
    """The whole thing: facts (fetched unless given), the script (Gemini, or the template when asked or
    when Gemini fails), the video."""
    if facts is None:
        progress(0.0, "Reading the market")
        facts = am_data.build(slot, progress=lambda m: progress(0.0, m))
        am_data.save(facts, out_dir)
    if template:
        script = am_script.template_script(facts)
    else:
        try:
            progress(0.01, "Gemini is writing the script")
            script = am_script.write_script(facts)
        except am_script.ScriptError as err:
            logger.warning("Gemini failed (%s); using the template script", err)
            progress(None, f"Gemini failed ({str(err)[:80]}); using the template script")
            script = am_script.template_script(facts)
    return make_video(facts, script, progress, images=images, out_dir=out_dir, settings=settings)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slot", choices=am_data.SLOTS)
    parser.add_argument("--facts", type=Path, help="a saved facts.json instead of fetching")
    parser.add_argument("--template", action="store_true", help="the built-in script, no Gemini")
    parser.add_argument("--images", choices=["auto", "local", "gemini", "placeholder"], help="who draws the broll")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    def show(frac, msg):
        print(f"[{'' if frac is None else f'{frac:4.0%}'}] {msg}")

    facts = json.loads(args.facts.read_text(encoding="utf-8")) if args.facts else None
    info = produce(args.slot, show, facts=facts, template=args.template, images=args.images)
    print(f"{info['title']} ({info['duration_seconds']}s, {info['words']} words, by {info['writer']}, {info['seconds_to_make']}s)")
    print(OUTPUT_DIR / f"{info['date']}_{info['slot']}" / info["filename"])


if __name__ == "__main__":
    main()
