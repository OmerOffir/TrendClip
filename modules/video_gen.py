"""DarkArchives video: a scripted story (assets/generated_scripts/story_<id>.json) becomes a finished Short.

    python -m modules.video_gen --id 12                 # output/darkarchives/story_12.mp4 (+ .json, pictures/)
    python -m modules.video_gen --id 12 --images placeholder --no-photos

The beats are the hook and every scene. Flow:
  1. voice      hook + narrations spoken by trendclip.voiceover (Edge; F5-TTS on the GPU when Microsoft
                refuses), which gives the word timings;
  2. timing     each beat starts when its first word is spoken (split_beats);
  3. pictures   one 9:16 picture per beat from scene_images (the GPU, Gemini or placeholders). A Wikipedia
                story gets the article's real photos for scene 1 and the middle scene, with credits;
  4. motion     ffmpeg: a slow push-in or pull-out on every picture (alternating), 0.6 s dissolves between
                beats, a cold grade, vignette and film grain -> pictures/background.mp4;
  5. assembly   trendclip.video_assembler: karaoke captions (3 words, amber highlight), the scene's
                overlay text flashing in above them, the title card for 3 s, dramatic music from
                music/dramatic under the voice.
The story becomes USED and story_<id>.json next to the video holds the title, description (source and
credits), hashtags, beats and what drew the pictures.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from db import repository
from modules import scene_images, script_gen
from modules.scene_images import Picture, setting

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "darkarchives"
FPS = 30
DISSOLVE = 0.6  # seconds of cross-fade between beats
ZOOM = 0.12  # push in / pull out by 12% over a beat
OVERSIZE = 1.3  # the picture is scaled up this much before the zoom window moves over it (less jitter)
MIN_BEAT = 1.0
DEFAULT_RATE = "+6%"  # F5 (the Linux voice) is slow at its "normal"; 130 words should take about 50 s
HASHTAGS = ["darkarchives", "mystery", "unsolved", "truestory", "history", "shorts"]
ProgressFn = Callable[[float | None, str], None]


class VideoGenError(RuntimeError):
    pass


@dataclass
class Beat:
    index: int  # 0 = hook, then the scene ids
    label: str  # "hook" or "scene 3"
    text: str
    overlay_text: str
    image_prompt: str
    start: float = 0.0
    end: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start


# --------------------------------------------------------------------------- timing


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def spoken_text(script: script_gen.Script) -> str:
    return " ".join(part.strip() for part in [script.hook_text, *(s.narration for s in script.scenes)] if part.strip())


def beats_of(script: script_gen.Script) -> list[Beat]:
    hook_prompt = (script.scenes[0].image_prompt if script.scenes else script.title) + ", wide establishing shot"
    beats = [Beat(0, "hook", script.hook_text, "", hook_prompt)]
    beats += [Beat(s.scene_id, f"scene {s.scene_id}", s.narration, s.overlay_text, s.image_prompt) for s in script.scenes]
    return beats


def split_beats(beats: list[Beat], words: list, tail: float) -> list[Beat]:
    """Give every beat its start and end from the spoken words: a beat ends where the next one's first
    word starts; the last one ends `tail` seconds after the last word. Words are matched by their
    letters, so a token the voice split in two ("GTA-6" -> "GTA", "6") still lands in the right beat."""
    if not words:
        raise VideoGenError("The voice produced no words")
    targets = ["".join(_norm(w) for w in b.text.split()) for b in beats]
    starts: list[float] = []
    spoken, wi = "", 0
    for target in targets:
        starts.append(words[wi].start if wi < len(words) else words[-1].end)
        want = len(spoken) + len(target)
        while wi < len(words) and len(spoken) < want:
            spoken += _norm(words[wi].text)
            wi += 1
    starts[0] = 0.0
    end = words[-1].end + tail
    timed = []
    for i, beat in enumerate(beats):
        beat.start = round(starts[i], 3)
        beat.end = round(starts[i + 1] if i + 1 < len(beats) else end, 3)
        timed.append(beat)
    # A beat the voice rushed through still gets a picture long enough to see.
    for i in range(len(timed) - 1):
        if timed[i].duration < MIN_BEAT:
            timed[i].end = timed[i + 1].start = round(timed[i].start + MIN_BEAT, 3)
    return [b for b in timed if b.duration > 0.05]


# --------------------------------------------------------------------------- motion


def kenburns_filter(index: int, beat_seconds: float, w: int, h: int, fps: int = FPS, zoom: float = ZOOM) -> str:
    """zoompan over an oversized picture: even beats push in, odd beats pull out, with a slight drift."""
    frames = max(int(round(beat_seconds * fps)), 1)
    ow, oh = int(w * OVERSIZE) // 2 * 2, int(h * OVERSIZE) // 2 * 2
    pos = f"on/{frames}"
    z = f"1+{zoom}*{pos}" if index % 2 == 0 else f"{1 + zoom}-{zoom}*{pos}"
    drift = (-1 if index % 4 in (1, 2) else 1) * int(ow * 0.03)
    x = f"iw/2-(iw/zoom/2)+{drift}*({pos}-0.5)"
    return (f"[{index}:v]scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},"
            f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps},setsar=1,format=yuv420p[v{index}]")


GRADE = ("eq=saturation=0.78:contrast=1.06:brightness=-0.02,"
         "colorbalance=rs=-0.04:gs=-0.01:bs=0.06:rm=-0.03:bm=0.05,"
         "vignette=angle=PI/4.2,noise=alls=9:allf=t,format=yuv420p")


def build_background(pictures: list[Path], beats: list[Beat], output: Path, size: tuple[int, int] = scene_images.SIZE,
                     fps: int = FPS, dissolve: float = DISSOLVE, progress: ProgressFn = lambda f, m: None,
                     crf: int = 18, preset: str = "medium") -> Path:
    """The moving background: every picture for its beat, dissolves centred on the beat changes
    (clip i runs its beat plus the dissolve; the first one plus half), graded dark."""
    from trendclip.video_assembler import ffmpeg_with_libass

    if len(pictures) != len(beats) or not beats:
        raise VideoGenError("One picture per beat is needed")
    w, h = size
    n = len(beats)
    total = beats[-1].end
    cmd = [ffmpeg_with_libass(), "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
    filters = []
    for i, (pic, beat) in enumerate(zip(pictures, beats)):
        extra = dissolve / 2 if i == 0 else dissolve
        if i == n - 1:
            extra += 1.0  # a little spare at the end; the assembler cuts at the voice
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{beat.duration + extra:.3f}", "-i", str(pic.resolve())]
        filters.append(kenburns_filter(i, beat.duration + extra, w, h, fps))
    current = "v0"
    for i in range(1, n):
        offset = beats[i - 1].end - dissolve / 2
        out = f"x{i}" if i < n - 1 else "mix"
        filters.append(f"[{current}][v{i}]xfade=transition=fade:duration={dissolve}:offset={max(offset, 0):.3f}[{out}]")
        current = out
    filters.append(f"[{current}]{GRADE}[out]")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.stem + ".rendering.mp4")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-an",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(fps),
            "-t", f"{total + 1.0:.3f}", "-movflags", "+faststart", str(tmp.resolve())]
    logger.info("Background: %d beats, %.1fs", n, total)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout:
        if line.startswith("out_time_us=") and line[12:].strip().isdigit():
            done = int(line[12:]) / 1e6
            progress(min(done / max(total, 0.1), 0.99), f"Moving the pictures: {done:.0f}s of {total:.0f}s")
    proc.wait()
    err = proc.stderr.read()
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise VideoGenError(f"ffmpeg failed on the background: {err.strip()[-400:]}")
    tmp.replace(output)
    return output


# --------------------------------------------------------------------------- the video


def video_path(story_id: int, out_dir: Path | None = None) -> Path:
    return (out_dir or OUTPUT_DIR) / f"story_{story_id}.mp4"


def info_path(story_id: int, out_dir: Path | None = None) -> Path:
    return video_path(story_id, out_dir).with_suffix(".json")


def existing_video_ids(out_dir: Path | None = None) -> list[int]:
    folder = out_dir or OUTPUT_DIR
    return sorted(int(m.group(1)) for p in folder.glob("story_*.mp4") if (m := re.fullmatch(r"story_(\d+)\.mp4", p.name)))


def photo_slots(n_beats: int, n_photos: int) -> list[int]:
    """Which beats show real photos: scene 1, then the middle scene (never the hook)."""
    slots = [1] if n_beats > 1 else []
    mid = n_beats // 2 + 1
    if n_photos > 1 and n_beats > 3 and mid not in slots:
        slots.append(mid)
    return slots[:n_photos]


def description_for(script: script_gen.Script, story: dict, credits: list[str], music_credit: str = "") -> str:
    lines = [script.hook_text.strip(), ""]
    source = story.get("url") or ""
    name = (story.get("source_name") or "").replace("/", " · ")
    lines.append(f"Source: {source}" if source else f"Source: {name}" if name else "")
    lines += [c for c in dict.fromkeys(credits) if c]
    if music_credit:
        lines.append(music_credit)
    lines += ["", " ".join(f"#{t}" for t in HASHTAGS)]
    return "\n".join(line for line in lines if line is not None).strip()


def _music(settings, progress: ProgressFn):
    from trendclip import music

    source = setting("DA_MUSIC", "folder:dramatic")
    if source.lower() in ("none", "off", "0"):
        return None
    try:
        return music.pick(settings, source)
    except music.MusicError as err:
        progress(None, f"No music: {err}")
        return None


def make_video(story_id: int, progress: ProgressFn = lambda f, m: None, images: str | None = None,
               photos: bool | None = None, out_dir: Path | None = None, scripts_dir: Path | None = None,
               db_path=None, settings=None) -> dict:
    """Build output/darkarchives/story_<id>.mp4 from its script; the story becomes USED.
    `images`: a scene_images backend (None = DA_IMAGE_BACKEND / auto); `photos`: Wikipedia photos
    (None = DA_ARCHIVE_PHOTOS, default on)."""
    from trendclip import voiceover
    from trendclip.config import get_settings
    from trendclip.quiz import TimedFlash
    from trendclip.video_assembler import TAIL_SECONDS, assemble_video, media_duration

    settings = settings or get_settings()
    path = script_gen.script_path(story_id, scripts_dir)
    if not path.is_file():
        raise VideoGenError(f"No script for story {story_id}: write it first (python -m modules.script_gen --id {story_id})")
    script = script_gen.Script.model_validate_json(path.read_text(encoding="utf-8"))
    story = repository.get_story(story_id, db_path) or {"id": story_id, "url": "", "source_name": ""}
    out_dir = out_dir or OUTPUT_DIR
    work = out_dir / f"story_{story_id}"
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()

    voice = setting("DA_TTS_VOICE", settings.tts_voice)
    rate = setting("DA_TTS_RATE", DEFAULT_RATE)
    progress(0.02, f"Recording the narration ({voice})")
    text = spoken_text(script)
    words = voiceover.synthesize(text, work / "voice.mp3", voice, rate, local_rate=rate)
    beats = split_beats(beats_of(script), words, TAIL_SECONDS)

    # The GPU voice and the GPU pictures do not fit on an 8 GB card together.
    from trendclip import local_voice

    local_voice.unload()

    want_photos = photos if photos is not None else setting("DA_ARCHIVE_PHOTOS", "1") not in ("0", "false", "off")
    photo_list: list[Picture] = []
    if want_photos and story.get("url"):
        progress(0.1, "Looking for the article's photos on Wikimedia Commons")
        photo_list = scene_images.wikipedia_photos(story["url"], work / "pictures")
    slots = photo_slots(len(beats), len(photo_list))
    prompts = [b.image_prompt for b in beats]
    drawn, backend_used = scene_images.make_pictures(
        prompts, work / "pictures", images, seed=story_id * 101,
        progress=lambda msg: progress(0.15, msg))
    pictures: list[Picture] = list(drawn)
    for slot, photo in zip(slots, photo_list):
        pictures[slot] = photo

    progress(0.55, "Moving the pictures")
    background = build_background([p.path for p in pictures], beats, work / "pictures" / "background.mp4",
                                  progress=lambda f, m: progress(0.55 + 0.2 * (f or 0), m))

    track = _music(settings, progress)
    from trendclip import music as music_mod

    flashes = [TimedFlash(b.overlay_text, round(b.start + 0.25, 3), round(min(b.start + 3.4, b.end - 0.15), 3), "", 0.3)
               for b in beats if b.overlay_text and b.duration > 1.0]
    title_card = script.title if setting("DA_TITLE_CARD", "1") not in ("0", "false", "off") else ""
    progress(0.75, "Rendering the Short")
    output = assemble_video(
        background, work / "voice.mp3", words, video_path(story_id, out_dir), fit="crop",
        progress=lambda f, m: progress(0.75 + 0.24 * (f or 0), m),
        music=music_mod.track_path(settings, track) if track else None,
        music_volume=float(setting("DA_MUSIC_VOLUME", "0.16")), music_fade_in=0.0,
        highlight=setting("DA_HIGHLIGHT", "orange"), max_words=3, font_size=92, outline=7, margin_v=600,
        title_card=title_card, title_chars=16, title_size=104, flashes=flashes, flash_scale=1.0,
    )

    credits = [p.credit for p in pictures if p.credit]
    info = {
        "story_id": story_id, "title": script.title, "filename": output.name,
        "description": description_for(script, story, credits, track.credit if track else ""),
        "hashtags": HASHTAGS, "duration_seconds": round(media_duration(output), 2),
        "voice": voice, "rate": rate, "pictures": backend_used,
        "photos": [p.credit for p in pictures if p.source == "wikipedia"],
        "music": {"title": track.title, "credit": track.credit} if track else None,
        "beats": [{**asdict(b), "picture": pictures[i].path.name, "picture_source": pictures[i].source}
                  for i, b in enumerate(beats)],
        "words": len(words), "seconds_to_make": round(time.time() - started),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    info_path(story_id, out_dir).write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if repository.get_story(story_id, db_path):
        repository.set_status(story_id, "USED", db_path, note=f"video {output.name}")
    progress(1.0, "Done")
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--id", type=int, required=True, help="a SCRIPT_READY story (its story_<id>.json)")
    parser.add_argument("--images", choices=["auto", "local", "gemini", "placeholder"], help="who draws the pictures")
    parser.add_argument("--no-photos", action="store_true", help="no Wikipedia photos even for a Wikipedia story")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    def show(frac, msg):
        print(f"[{'' if frac is None else f'{frac:4.0%}'}] {msg}")

    try:
        info = make_video(args.id, show, images=args.images, photos=False if args.no_photos else None)
    except (VideoGenError, scene_images.ImageError) as err:
        raise SystemExit(f"error: {err}") from None
    print(f"\n{info['filename']}: {info['duration_seconds']}s, pictures by {info['pictures']}, "
          f"{len(info['photos'])} photo(s), music {info['music']['title'] if info['music'] else 'none'} "
          f"({info['seconds_to_make']}s)\nTITLE: {info['title']}")


if __name__ == "__main__":
    main()
