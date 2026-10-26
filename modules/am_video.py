"""After Market video: one session's Short, from the facts to the finished file, in minutes before it posts.

    python -m modules.am_video                         # the slot that fits the clock, with Gemini
    python -m modules.am_video --slot close --template  # the built-in script (no Gemini)
    python -m modules.am_video --facts output/aftermarket/2026-10-27_close/facts.json --images placeholder

Flow (output/aftermarket/<date>_<slot>/):
  1. facts      modules.am_data (facts.json), unless --facts points at a saved one;
  2. script     modules.am_script with Gemini (script.json); --template or a Gemini failure: the template;
  3. voice      Kokoro on the GPU (AM_TTS_VOICE, default am_michael, AM_TTS_SPEED), which times every
                word; AM_TTS_ENGINE=f5 copies a reference clip (AM_TTS_REF, a wav with its .txt
                transcript next to it: record an energetic reader) with estimated timings;
                AM_TTS_ENGINE=edge is the gameplay voice;
  4. pictures   chart cards from modules.am_charts for board / ticker / crypto / calendar scenes, the
                ticker and crypto cards as clips whose line draws itself (AM_ANIMATE_CHARTS=0: stills);
                the "broll" scenes drawn by scene_images (the GPU, AM_IMAGE_BACKEND) with a finance style;
  5. motion     the Dark Outliers push-in / dissolves (modules.video_gen.build_background), a whoosh at
                every cut and a tick as a chart starts drawing (AM_SFX_VOLUME, 0 = off), the end right
                after the last word;
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
DEFAULT_SPEED = "1.15"  # Shorts pace
DEFAULT_RATE = "+12%"  # the Edge engine
DEFAULT_ENGINE = "kokoro"
DEFAULT_MUSIC = "folder:electronic"  # music/electronic/*.mp3: deep electronic beds (drop files there)
FALLBACK_MUSIC = "folder:chill"  # until the electronic folder has music
DEFAULT_CTA_LINE = {"premarket": "Follow for the close.", "close": "Follow for tomorrow's open.", "weekend": "Follow for Monday's open."}
DEFAULT_END_CARD = "Follow for the next session"
BROLL_STYLE = ", cinematic, photoreal, dark blue and amber tones, shallow depth of field, no text, no logos"
HASHTAGS = ["stockmarket", "stocks", "trading", "premarket", "earnings", "crypto", "shorts"]
DISCLAIMER = "Not financial advice. Data from Yahoo Finance, Nasdaq, ForexFactory, CoinGecko and alternative.me."
TAIL = 0.6  # the video ends right after the last word: a static end is where Shorts lose the viewer
DRAW_SECONDS = 1.4  # a chart card's line draws itself over this long, then holds
CLIP_FPS = 24
BROLL_ZOOM = 0.18  # the AI pictures keep moving
SPLIT_SECONDS = 4.0  # a broll beat longer than this cuts between several pictures
PICTURE_SECONDS = 3.2  # about this long on each
HOOK_PROMPT = {  # the hook's own picture (frame 0 is the thumbnail): the moment, not a chart
    "premarket": "sunrise over Wall Street, the New York Stock Exchange facade, early traders, dramatic morning light",
    "close": "the closing bell on a crowded trading floor, screens full of charts, dramatic light",
    "weekend": "an empty trading floor at night, screens glowing with charts, cinematic",
}
FLASH_Y = 0.24  # the overlay near the top (y 400-520 of 1920): the chart cards keep that band free
CARD_ZOOM = 0.05  # a gentle push-in on a still card: it keeps its edges
SFX_DIR = am_data.PROJECT_ROOT / "assets" / "sfx"
ProgressFn = Callable[[float | None, str], None]


class VideoError(RuntimeError):
    pass


@dataclass
class AmBeat(Beat):
    visual: str = "board"  # board | ticker:<SYM> | crypto | calendar | broll


def channel_handle() -> str:
    """The channel's @handle from channels/aftermarket/channel.env ("" until it is set)."""
    from modules import channel_common

    return channel_common.handle("aftermarket")


def beats_of(script: am_script.Script, cta_line: str = "") -> list[Beat]:
    """The hook (over the first scene's picture), then every scene; the call to action joins the last."""
    beats = [AmBeat(0, "hook", script.hook_text, "", HOOK_PROMPT.get(script.slot, HOOK_PROMPT["close"]), visual="broll")]
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


def _ffmpeg() -> str:
    from trendclip.video_assembler import ffmpeg_with_libass

    return ffmpeg_with_libass()


def animated_clip(frames: list[Path], output: Path, seconds: float, draw_seconds: float = DRAW_SECONDS, fps: int = CLIP_FPS) -> Path:
    """The draw animation as a clip: the frames over `draw_seconds`, then the last one held with a slow
    push-in, `seconds` long in all."""
    import subprocess

    pattern = frames[0].with_name("frame_%03d.jpg")
    rate = len(frames) / max(draw_seconds, 0.2)
    total = int(seconds * fps) + 1
    big_w, big_h = int(am_charts.W * video_gen.OVERSIZE) // 2 * 2, int(am_charts.H * video_gen.OVERSIZE) // 2 * 2
    zoom = (f"tpad=stop_mode=clone:stop_duration={seconds:.3f},trim=duration={seconds:.3f},fps={fps},scale={big_w}:{big_h},"
            f"zoompan=z='1+{CARD_ZOOM}*on/{total}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={am_charts.W}x{am_charts.H}:fps={fps},"
            f"format=yuv420p")
    cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-framerate", f"{rate:.3f}", "-i", str(pattern),
           "-vf", zoom, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(output)]
    subprocess.run(cmd, check=True, capture_output=True)
    return output


def ensure_sfx(sfx_dir: Path | None = None) -> dict[str, Path]:
    """The two sounds, made once with ffmpeg: a soft whoosh for the cut between beats, a short tick as
    a chart starts drawing. Synthesised, so there is nothing to license."""
    import subprocess

    sfx_dir = sfx_dir or SFX_DIR
    sfx_dir.mkdir(parents=True, exist_ok=True)
    whoosh, tick = sfx_dir / "whoosh.wav", sfx_dir / "tick.wav"
    if not whoosh.is_file():
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.5:seed=7:d=0.45",
                        "-af", "highpass=f=700,lowpass=f=5000,afade=t=in:d=0.12,afade=t=out:st=0.2:d=0.25,volume=0.9",
                        "-ar", "44100", "-ac", "1", str(whoosh)], check=True, capture_output=True)
    if not tick.is_file():
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=1320:duration=0.09",
                        "-af", "afade=t=in:d=0.005,afade=t=out:st=0.03:d=0.06,volume=0.5", "-ar", "44100", "-ac", "1", str(tick)],
                       check=True, capture_output=True)
    return {"whoosh": whoosh, "tick": tick}


def mix_sfx(voice: Path, beats: list[AmBeat], output: Path, volume: float, sfx: dict[str, Path] | None = None) -> Path:
    """The voice with a whoosh at every beat change and a tick where a chart starts drawing, `volume`
    (0-1) under the voice. The voice's length and timing never change (amix keeps the first input)."""
    import subprocess

    sfx = sfx or ensure_sfx()
    cues: list[tuple[Path, float]] = []
    for b in beats[1:]:
        cues.append((sfx["whoosh"], max(b.start - 0.12, 0.0)))
        if b.visual.startswith(am_charts.ANIMATED):
            cues.append((sfx["tick"], b.start + 0.05))
    if not cues or volume <= 0:
        return voice
    cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(voice)]
    filters = []
    for i, (path, at) in enumerate(cues, 1):
        cmd += ["-i", str(path)]
        ms = int(at * 1000)
        filters.append(f"[{i}:a]adelay={ms}|{ms},volume={volume:.3f}[s{i}]")
    inputs = "[0:a]" + "".join(f"[s{i}]" for i in range(1, len(cues) + 1))
    filters.append(f"{inputs}amix=inputs={len(cues) + 1}:duration=first:normalize=0[out]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "3", str(output)]
    subprocess.run(cmd, check=True, capture_output=True)
    return output


def variants(beat: AmBeat) -> int:
    """How many pictures a broll beat gets: one, or several when it is long (a still for 8 s is where
    Shorts lose the viewer)."""
    if beat.visual != "broll" or beat.duration <= SPLIT_SECONDS:
        return 1
    return min(int(round(beat.duration / PICTURE_SECONDS)), 4)


ANGLES = ["", ", wide shot", ", close-up, shallow depth of field", ", low angle, dramatic"]


def pictures_for(beats: list[AmBeat], facts: dict, work: Path, images: str | None, seed: int,
                 progress: ProgressFn, animate: bool = True) -> tuple[list[Picture], str, dict[int, list[Picture]]]:
    """One picture per beat: a chart card for a chart visual (a ticker or crypto card as a clip whose
    line draws itself), the GPU for broll. A ticker card for a symbol the facts do not have falls back
    to the board. The third value: the extra pictures of a long broll beat (background_plan cuts
    between them)."""
    cards = am_charts.render(facts, work / "pictures")
    pictures: list[Picture | None] = [None] * len(beats)
    extras: dict[int, list[Picture]] = {}
    broll = []  # (beat index, prompt), several per long beat
    for i, b in enumerate(beats):
        if b.visual == "broll" and b.image_prompt.strip():
            for k in range(variants(b)):
                broll.append((i, b.image_prompt + ANGLES[k % len(ANGLES)] + BROLL_STYLE))
    used = "cards"
    if broll:
        drawn, used_backend = scene_images.make_pictures([p for _, p in broll], work / "pictures" / "broll", images, seed=seed,
                                                         progress=lambda msg: progress(0.15, msg))
        used = f"cards + {used_backend}"
        for (i, _), pic in zip(broll, drawn):
            picture = Picture(scene_images.graded_copy(pic.path), pic.source, prompt=pic.prompt)
            if pictures[i] is None:
                pictures[i] = picture
            else:
                extras.setdefault(i, []).append(picture)
    clips: dict[str, Path] = {}
    for i, b in enumerate(beats):
        if pictures[i] is not None:
            continue
        path = cards.get(b.visual) or cards["board"]
        if b.visual == "calendar" and b.text.strip():  # the card lists the companies this scene names
            path = am_charts.calendar(facts, work / "pictures" / f"calendar_{i}.jpg", mention=b.text)
        if animate and b.visual in cards and b.visual.startswith(am_charts.ANIMATED) and b.duration >= DRAW_SECONDS:
            if b.visual not in clips:
                progress(0.3, f"Drawing the {b.visual} chart")
                name = b.visual.replace(":", "_")
                frames = am_charts.frames(facts, b.visual, work / "pictures" / f"frames_{name}")
                longest = max(x.duration for x in beats if x.visual == b.visual) + video_gen.DISSOLVE + 1.5
                clips[b.visual] = animated_clip(frames, work / "pictures" / f"{name}.mp4", longest)
            pictures[i] = Picture(clips[b.visual], "chart-animated", prompt=b.visual)
            continue
        pictures[i] = Picture(path, "chart", prompt=b.visual)
    return [p for p in pictures if p], used, extras


def background_plan(beats: list[AmBeat], pictures: list[Picture], extras: dict[int, list[Picture]]) -> tuple[list[Path], list[Beat], list[float]]:
    """What build_background gets: the pictures, their beats and zooms, with a long broll beat cut into
    one sub-beat per picture (each pushes in on its own)."""
    paths, bg_beats, zooms = [], [], []
    for i, (b, p) in enumerate(zip(beats, pictures)):
        parts = [p] + extras.get(i, [])
        step = b.duration / len(parts)
        for k, part in enumerate(parts):
            sub = Beat(b.index, b.label if len(parts) == 1 else f"{b.label} ({k + 1}/{len(parts)})", b.text, b.overlay_text,
                       b.image_prompt, round(b.start + k * step, 3), round(b.start + (k + 1) * step, 3) if k < len(parts) - 1 else b.end)
            paths.append(part.path)
            bg_beats.append(sub)
            zooms.append(CARD_ZOOM if part.source in ("chart", "chart-animated") else BROLL_ZOOM)
    return paths, bg_beats, zooms


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
    elif engine == "f5":  # the GPU voice that copies a reference clip: an energetic reader in AM_TTS_REF
        from trendclip import local_voice

        ref = setting("AM_TTS_REF", "")
        voice = ref or str(local_voice.REF_WAV)
        rate = setting("AM_TTS_RATE", DEFAULT_RATE)
        progress(0.02, f"Recording the narration (F5, {Path(voice).name})")
        audio, words = local_voice.speak(text, rate, ref_wav=Path(ref) if ref else None)
        (work / "voice.mp3").write_bytes(audio)
        local_voice.unload()
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
    pictures, backend_used, extras = pictures_for(beats, facts, work, images, seed, progress,
                                                  animate=not video_gen.off(setting("AM_ANIMATE_CHARTS", "1")))
    paths, bg_beats, zooms = background_plan(beats, pictures, extras)
    background = video_gen.build_background(paths, bg_beats, work / "pictures" / "background.mp4",
                                            progress=lambda f, m: progress(0.5 + 0.2 * (f or 0), m), zoom=zooms)
    sfx_volume = float(setting("AM_SFX_VOLUME", "0.5"))
    voice_file = work / "voice.mp3"
    if sfx_volume > 0:
        try:
            voice_file = mix_sfx(work / "voice.mp3", beats, work / "voice_fx.mp3", sfx_volume)
        except Exception as err:  # noqa: BLE001 - the sounds never fail the video
            logger.warning("Sound effects failed: %s", err)
            progress(None, "Sound effects failed; rendering without them")

    from trendclip import music as music_mod

    track = None
    source = setting("AM_MUSIC", DEFAULT_MUSIC)
    if not video_gen.off(source):
        for candidate in dict.fromkeys([source, FALLBACK_MUSIC]):
            try:
                track = music_mod.pick(settings, candidate)
                break
            except music_mod.MusicError as err:
                progress(None, f"No music from {candidate}: {err}")
    flashes = [TimedFlash(b.overlay_text, round(b.start + 0.2, 3), round(min(b.start + 3.2, b.end - 0.15), 3), "", FLASH_Y)
               for b in beats if b.overlay_text and b.duration > 1.0]
    end_card = setting("AM_END_CARD", f"{channel_handle()} · Follow for the next session" if channel_handle() else DEFAULT_END_CARD)
    end_card = "" if video_gen.off(end_card) else end_card
    overlays, stickers_used = video_gen.cta_sticker(settings, words, flashes, False, bool(end_card))
    cover = setting("AM_COVER", "auto")
    cover = "" if video_gen.off(cover) else (script.thumbnail_text or script.title).upper() if cover.lower() == "auto" else cover.upper()
    progress(0.75, "Rendering the Short")
    output = assemble_video(
        background, voice_file, words, work / "video.mp4", fit="crop",
        progress=lambda f, m: progress(0.75 + 0.24 * (f or 0), m),
        music=music_mod.track_path(settings, track) if track else None,
        music_volume=float(setting("AM_MUSIC_VOLUME", "0.20")), music_fade_in=0.0,
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
        "sfx": voice_file.name == "voice_fx.mp3", "animated": sorted({p.prompt for p in pictures if p.source == "chart-animated"}),
        "background_cuts": len(paths),
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
