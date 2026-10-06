"""Phase 4: turn a background gameplay clip + voiceover + word timestamps into a 9:16 Short.

    background MP4 ─┐
    voiceover MP3  ─┼─ ffmpeg: scale/crop to 1080x1920 → burn karaoke .ass subtitles → mux voice
    word timings   ─┘                                             → output/final_short.mp4

Subtitles show 2-4 words at a time (uppercase, white, heavy black outline); the word being spoken
turns yellow (or cyan) for exactly its duration. Optional extras: a title card at the top for the
first 3 seconds, transparent pop-up images (`overlays`, timed to spoken words) between the title and
the subtitles, and background music ducked under the voice.

Accepted timestamp JSON shapes (see `load_word_timings`):
    [{"word": "hello", "start": 0.12, "end": 0.48}, ...]          plain list (also text/start_time/end_time)
    {"words": [...]} / {"segments": [{"words": [...]}]}           Whisper / faster-whisper / WhisperX
    [{"text": "hello", "offset": 1200000, "duration": 3600000}]   edge-tts WordBoundary (100 ns ticks)
    {"alignment": {"characters": [...], "character_start_times_seconds": [...], ...}}   ElevenLabs

CLI:
    python -m trendclip.video_assembler --voice voice.mp3 --timestamps words.json
    python -m trendclip.video_assembler --demo "Text to speak with the macOS voice"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal

from .config import PROJECT_ROOT

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 1080, 1920
LANDSCAPE = (1920, 1080)  # long videos: 16:9 (YouTube treats them as regular videos, not Shorts)
LANDSCAPE_OVERLAY_SCALE = 0.72
# Karaoke / title sizes for the 16:9 canvas (the 9:16 defaults are the create_karaoke_ass_file arguments).
LANDSCAPE_SUBTITLES = {"font_size": 96, "margin_v": 80, "outline": 8, "title_size": 92, "top_margin": 60,
                       "title_chars": 26}
DEFAULT_BACKGROUND = PROJECT_ROOT / "assets" / "backgrounds" / "latest_gameplay.mp4"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "final_short.mp4"
DEFAULT_MUSIC = PROJECT_ROOT / "assets" / "music" / "background.mp3"

# ASS colours are &HAABBGGRR (alpha 00 = opaque).
HIGHLIGHT_COLORS = {
    "yellow": "&H0000FFFF",
    "cyan": "&H00FFFF00",
    "green": "&H0000FF00",
    "orange": "&H0000A5FF",
}
WHITE, BLACK = "&H00FFFFFF", "&H00000000"

FONT_PREFERENCE = ["Montserrat Black", "Montserrat ExtraBold", "Impact", "Arial Black"]
_FONT_FILES = {
    "Montserrat Black": ["Montserrat-Black.ttf", "Montserrat-Black.otf"],
    "Montserrat ExtraBold": ["Montserrat-ExtraBold.ttf", "Montserrat-ExtraBold.otf"],
    "Impact": ["Impact.ttf", "impact.ttf"],
    "Arial Black": ["Arial Black.ttf", "ariblk.ttf"],
}
_FONT_DIRS = [
    PROJECT_ROOT / "assets" / "fonts",
    Path.home() / "Library" / "Fonts",
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/usr/share/fonts/truetype/msttcorefonts"),
    Path("C:/Windows/Fonts"),
]

SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*$")
CLAUSE_END = re.compile(r"[,;:—–-]+[\"')\]]*$")


class AssemblyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


# --------------------------------------------------------------------------- timestamps


def _num(item: dict, *keys: str) -> float | None:
    for key in keys:
        if item.get(key) is not None:
            return float(item[key])
    return None


def _from_word_list(items: list[dict]) -> list[Word]:
    words = []
    for item in items:
        text = str(item.get("word") or item.get("text") or item.get("punctuated_word") or "").strip()
        if not text:
            continue
        if "offset" in item and "duration" in item:  # edge-tts: 100-nanosecond ticks
            start = float(item["offset"]) / 1e7
            end = start + float(item["duration"]) / 1e7
        else:
            start = _num(item, "start", "start_time", "startTime", "begin")
            end = _num(item, "end", "end_time", "endTime")
            if start is None and (ms := _num(item, "start_ms", "startMs")) is not None:
                start = ms / 1000
            if end is None and (ms := _num(item, "end_ms", "endMs")) is not None:
                end = ms / 1000
            if start is None:
                continue
            if end is None:
                end = start + float(item.get("duration", 0.3))
        words.append(Word(text, start, max(end, start + 0.05)))
    return words


def _from_char_alignment(al: dict) -> list[Word]:
    chars = al.get("characters") or al.get("chars") or []
    starts = al.get("character_start_times_seconds") or al.get("charStartTimesMs") or []
    ends = al.get("character_end_times_seconds") or al.get("charDurationsMs") or []
    in_ms = "charStartTimesMs" in al
    words, buf, w_start, w_end = [], "", None, None
    for i, ch in enumerate(chars):
        start = starts[i] / 1000 if in_ms else starts[i]
        end = (starts[i] + ends[i]) / 1000 if in_ms else ends[i]
        if ch.isspace():
            if buf:
                words.append(Word(buf, w_start, w_end))
            buf, w_start = "", None
            continue
        if w_start is None:
            w_start = start
        buf += ch
        w_end = end
    if buf:
        words.append(Word(buf, w_start, w_end))
    return words


def load_word_timings(data: Any) -> list[Word]:
    """Normalise any supported timestamps shape (or a path to a JSON file) into sorted Words."""
    if isinstance(data, (str, Path)):
        data = json.loads(Path(data).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if isinstance(data.get("words"), list):
            words = _from_word_list(data["words"])
        elif isinstance(data.get("segments"), list):
            words = _from_word_list([w for seg in data["segments"] for w in seg.get("words", [])])
        elif isinstance(al := data.get("alignment") or data.get("normalized_alignment"), dict):
            words = _from_char_alignment(al)
        elif "characters" in data:
            words = _from_char_alignment(data)
        else:
            raise AssemblyError("Unrecognised timestamps JSON: expected words/segments/alignment")
    elif isinstance(data, list):
        if data and all(isinstance(w, Word) for w in data):
            words = list(data)
        else:
            words = _from_word_list(data)
    else:
        raise AssemblyError(f"Unsupported timestamps type: {type(data).__name__}")
    if not words:
        raise AssemblyError("Timestamps contain no words")
    return sorted(words, key=lambda w: w.start)


def estimate_word_timings(text: str, duration: float, lead_in: float = 0.15) -> list[Word]:
    """Rough timings when only the script is known: spread words over the audio by length,
    with small pauses after punctuation. Real TTS/ASR timestamps are far more accurate."""
    tokens = text.split()
    if not tokens:
        raise AssemblyError("Empty text")
    weights = []
    for tok in tokens:
        w = 1.0 + len(re.sub(r"\W", "", tok)) * 0.35
        pause = 1.6 if SENTENCE_END.search(tok) else 0.8 if CLAUSE_END.search(tok) else 0.0
        weights.append((w, pause))
    span = max(duration - lead_in - 0.2, 0.5)
    unit = span / sum(w + p for w, p in weights)
    t, words = lead_in, []
    for tok, (w, p) in zip(tokens, weights):
        words.append(Word(tok, round(t, 3), round(t + w * unit, 3)))
        t += (w + p) * unit
    return words


# --------------------------------------------------------------------------- karaoke .ass


def chunk_words(words: list[Word], min_words: int = 2, max_words: int = 4,
                max_chars: int = 18, max_gap: float = 0.6) -> list[list[Word]]:
    """Group words into on-screen lines: break on max size, punctuation, long pauses."""
    chunks: list[list[Word]] = []
    current: list[Word] = []
    for i, word in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        if current and len(" ".join(w.text for w in current + [word])) > max_chars and len(current) >= min_words:
            chunks.append(current)
            current = []
        current.append(word)
        long_pause = nxt is not None and nxt.start - word.end > max_gap
        if (
            nxt is None
            or len(current) >= max_words
            or long_pause
            or (len(current) >= min_words and (SENTENCE_END.search(word.text) or CLAUSE_END.search(word.text)))
            or SENTENCE_END.search(word.text)
        ):
            chunks.append(current)
            current = []
    # A lone trailing word reads badly; fold it into the previous line when there is room.
    merged: list[list[Word]] = []
    for chunk in chunks:
        if (
            len(chunk) == 1 and merged and len(merged[-1]) < max_words
            and chunk[0].start - merged[-1][-1].end <= max_gap
            and not SENTENCE_END.search(merged[-1][-1].text)
        ):
            merged[-1] = merged[-1] + chunk
        elif (
            len(chunk) == 1 and merged and len(merged[-1]) > min_words
            and chunk[0].start - merged[-1][-1].end <= max_gap
            and not SENTENCE_END.search(merged[-1][-1].text)
        ):
            merged.append([merged[-1].pop(), chunk[0]])
        else:
            merged.append(chunk)
    return merged


def _ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(word: str, uppercase: bool) -> str:
    word = word.replace("\\", "").replace("{", "(").replace("}", ")")
    return word.upper() if uppercase else word


def _resolve_color(color: str) -> str:
    if color.lower() in HIGHLIGHT_COLORS:
        return HIGHLIGHT_COLORS[color.lower()]
    if re.fullmatch(r"&H[0-9A-Fa-f]{8}", color):
        return "&H" + color[2:].upper()
    if re.fullmatch(r"#?[0-9A-Fa-f]{6}", color):  # #RRGGBB → &H00BBGGRR
        rgb = color.lstrip("#")
        return f"&H00{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}".upper()
    raise AssemblyError(f"Unknown colour '{color}' (use yellow/cyan/green/orange, #RRGGBB or &HAABBGGRR)")


def wrap_title(text: str, max_chars: int = 14, max_lines: int = 3) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= max_chars:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines[:max_lines]


def title_card_event(text: str, seconds: float = 3.0, uppercase: bool = True, max_chars: int = 14) -> str:
    """Top banner for the first seconds: pops in, fades out."""
    lines = [_ass_text(line, uppercase) for line in wrap_title(text, max_chars=max_chars)]
    anim = r"{\fad(80,300)\fscx55\fscy55\t(0,140,\fscx110\fscy110)\t(140,240,\fscx100\fscy100)}"
    return f"Dialogue: 1,{_ass_time(0)},{_ass_time(seconds)},Title,,0,0,0,,{anim}" + r"\N".join(lines)


def end_card_event(text: str, start: float, end: float) -> str:
    """Top banner for the call to action at the end (case kept, so @Handles stay readable)."""
    wrapped = [line for seg in text.split("·") if seg.strip() for line in wrap_title(seg.strip(), max_chars=20)]
    lines = [_ass_text(line, False) for line in wrapped[:4]]
    anim = r"{\fad(120,0)\fscx60\fscy60\t(0,160,\fscx108\fscy108)\t(160,260,\fscx100\fscy100)}"
    return f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},EndCard,,0,0,0,,{anim}" + r"\N".join(lines)


def create_karaoke_ass_file(
    timestamps_data: Any,
    output_ass_path: str | Path,
    *,
    font: str = "Impact",
    font_size: int = 120,
    highlight: str = "yellow",
    outline: int = 9,
    shadow: int = 4,
    margin_v: int = 620,
    min_words: int = 2,
    max_words: int = 4,
    uppercase: bool = True,
    active_scale: int = 112,
    hold_seconds: float = 0.25,
    title_card: str = "",
    title_seconds: float = 3.0,
    title_size: int = 132,
    end_card: str = "",
    end_seconds: float = 3.5,
    size: tuple[int, int] = (WIDTH, HEIGHT),
    top_margin: int = 200,
    title_chars: int = 14,
) -> Path:
    """Write a 1080x1920 (or `size`) .ass file: 2-4 words per line, the spoken word recoloured for its duration.

    Each word gets its own Dialogue event showing the whole line with that word highlighted, from the
    word's start until the next word starts, so the colour moves exactly with the voice. An optional
    title card (black text on a white box, top centre) shows for the first `title_seconds`.
    """
    words = load_word_timings(timestamps_data)
    chunks = chunk_words(words, min_words=min_words, max_words=max_words)
    hl = _resolve_color(highlight)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {size[0]}
PlayResY: {size[1]}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{font},{font_size},{WHITE},{hl},{BLACK},&H80000000,-1,0,0,0,100,100,2,0,1,{outline},{shadow},2,70,70,{margin_v},1
Style: Title,{font},{title_size},{BLACK},{BLACK},{WHITE},&H64000000,-1,0,0,0,100,100,1,0,3,28,0,8,60,60,{top_margin},1
Style: EndCard,{font},{round(title_size * 96 / 132)},{BLACK},{BLACK},&H0000FFFF,&H64000000,-1,0,0,0,100,100,1,0,3,26,0,8,60,60,{top_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [title_card_event(title_card, title_seconds, uppercase, title_chars)] if title_card.strip() else []
    if end_card.strip() and words:
        last = words[-1].end
        start = max(last - end_seconds, title_seconds if title_card.strip() else 0.0)
        events.append(end_card_event(end_card, start, last + 1.0))
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0].start if ci + 1 < len(chunks) else None
        line_end = chunk[-1].end + hold_seconds
        if next_start is not None:
            line_end = min(line_end, next_start)
        line_end = max(line_end, chunk[-1].start + 0.05)
        texts = [_ass_text(w.text, uppercase) for w in chunk]
        for wi, word in enumerate(chunk):
            start = chunk[0].start if wi == 0 else word.start
            end = chunk[wi + 1].start if wi + 1 < len(chunk) else line_end
            if end - start < 0.01:
                continue
            parts = [
                f"{{\\c{hl}&\\fscx{active_scale}\\fscy{active_scale}}}{t}{{\\r}}" if j == wi else t
                for j, t in enumerate(texts)
            ]
            events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Karaoke,,0,0,0,,{' '.join(parts)}")

    path = Path(output_ass_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- ffmpeg


def _has_libass(ffmpeg: str) -> bool:
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(re.search(r"^\s*\S+\s+subtitles\s", proc.stdout, re.MULTILINE))


@lru_cache(maxsize=1)
def ffmpeg_with_libass() -> str:
    """An ffmpeg that can burn .ass subtitles. Homebrew's default ffmpeg lacks libass;
    the imageio-ffmpeg binary (in requirements) has it."""
    candidates = []
    if env := os.environ.get("FFMPEG_BINARY"):
        candidates.append(env)
    if system := shutil.which("ffmpeg"):
        candidates.append(system)
    try:
        import imageio_ffmpeg

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:  # noqa: BLE001 - optional dependency
        pass
    for candidate in candidates:
        if _has_libass(candidate):
            return candidate
    raise AssemblyError(
        "No ffmpeg with libass (subtitles filter) found. Run `pip install imageio-ffmpeg` "
        "or install an ffmpeg built with --enable-libass."
    )


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def media_duration(path: Path) -> float:
    proc = subprocess.run([ffmpeg_with_libass(), "-hide_banner", "-i", str(path)],
                          capture_output=True, text=True, timeout=30)
    m = _DURATION_RE.search(proc.stderr)
    if not m:
        raise AssemblyError(f"Could not read the duration of {path.name}")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def find_font_file(preferred: str | None = None) -> tuple[str, Path | None]:
    """(font family name, file) for the first available font; file None = let libass pick."""
    names = [preferred] if preferred else []
    names += [n for n in FONT_PREFERENCE if n != preferred]
    for name in names:
        for directory in _FONT_DIRS:
            for filename in _FONT_FILES.get(name, [f"{name}.ttf", f"{name}.otf"]):
                if (directory / filename).is_file():
                    return name, directory / filename
    return preferred or "Arial", None


def video_filter(fit: Literal["crop", "blur"], fps: int, size: tuple[int, int] = (WIDTH, HEIGHT)) -> str:
    """Filter graph from input 0 to a 1080x1920 (or `size`) [base] stream."""
    w, h = size
    cover = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    if fit == "crop":
        return f"[0:v]{cover},setsar=1,fps={fps}[base]"
    # Whole frame in the middle, a blurred zoomed copy filling the rest.
    return (
        f"[0:v]split=2[bgsrc][fgsrc];"
        f"[bgsrc]{cover},boxblur=24:2,eq=brightness=-0.08[bg];"
        f"[fgsrc]scale={w}:{h}:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps}[base]"
    )


def music_filter(duration: float, volume: float) -> str:
    """Input 2 (music) under input 1 (voice): fade in/out, duck while the voice speaks → [aout]."""
    fade_out = max(duration - 1.5, 0)
    return (
        # Mono voice copied to both channels at full level (a plain stereo upmix costs ~3 dB).
        "[1:a]aformat=sample_rates=48000:channel_layouts=mono,pan=stereo|c0=c0|c1=c0,asplit=2[voice][key];"
        # Every track is levelled to the same loudness first, so 12-15% sounds the same for a loud
        # EDM track and a quiet lo-fi one.
        "[2:a]loudnorm=I=-14:TP=-2:LRA=11,"
        f"aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,volume={volume:.3f},"
        f"afade=t=in:d=0.8,afade=t=out:st={fade_out:.2f}:d=1.5[bed];"
        # Gentle ducking: the music dips ~3-4 dB under speech and swells back in the pauses.
        "[bed][key]sidechaincompress=threshold=0.05:ratio=3:attack=20:release=400[ducked];"
        "[voice][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]"
    )


POPUP_CENTER_Y = 800  # between the title card (top) and the subtitles (lower middle)
POPUP_BOX = 460
POP_SCALES = (0.3, 0.62, 0.92, 1.1, 1.13, 1.07, 1.0)  # pop-in with a small bounce, one frame each
POP_ALPHA = (0.35, 0.7, 1.0)  # fade-in over the first frames


@dataclass
class Overlay:
    """An image shown from `start` to `end` seconds: a pop-up (default: centre, transparent PNG) or a
    sticker at `center` styled as a cut-out ("sticker") or a rounded meme card ("card")."""

    image: Path
    start: float
    end: float
    x_offset: int = 0
    tilt: float = 0.0
    center: tuple[int, int] | None = None
    box: int = POPUP_BOX
    style: Literal["asis", "sticker", "card"] = "asis"
    pulse: bool = False  # gentle breathing while shown (call-to-action stickers)
    animated: bool = False  # play the GIF / WebP animation

    def xy(self) -> tuple[int, int]:
        return self.center or (WIDTH // 2 + self.x_offset, POPUP_CENTER_Y)


def to_landscape(overlay: Overlay, size: tuple[int, int] = LANDSCAPE) -> Overlay:
    """Move an overlay laid out on the 9:16 canvas to the same spot of a 16:9 one. Things below the
    9:16 captions (call-to-action, bottom stickers) would land on the 16:9 captions, so they go to the side."""
    from dataclasses import replace

    w, h = size
    x, y = overlay.xy()
    nx, ny = round(x * w / WIDTH), round(y * h / HEIGHT)
    if y > 1310:
        ny = round(h * 0.62)
        if abs(x - WIDTH // 2) < 200:
            nx = round(w * 0.84)
    return replace(overlay, center=(nx, ny), box=round(overlay.box * LANDSCAPE_OVERLAY_SCALE))


def popup_layout(index: int) -> tuple[int, float]:
    """Alternate pop-ups slightly left/right with a small tilt so they feel hand placed."""
    return [(-70, -7.0), (70, 6.0), (0, -3.0), (60, -5.0), (-60, 5.0)][index % 5]


def _source_frames(overlay: Overlay) -> list[tuple[Any, float]]:
    """(styled RGBA frame, seconds) for each animation frame, or one frame for still images."""
    from PIL import Image, ImageSequence

    def prepare(img):
        img = img.convert("RGBA")
        if overlay.style != "asis":
            from . import stickers

            info = stickers.StickerInfo(filename=Path(overlay.image).name, transparent=overlay.style == "sticker")
            img = stickers.style(img, info, overlay.box)
        scale = overlay.box / max(img.size)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
        if overlay.tilt:
            img = img.rotate(overlay.tilt, resample=Image.BICUBIC, expand=True)
        return img

    with Image.open(overlay.image) as src:
        if not (overlay.animated and getattr(src, "n_frames", 1) > 1):
            return [(prepare(src), 1.0)]
        out = []
        for i, frame in enumerate(ImageSequence.Iterator(src)):  # one object, seeked: copy each frame now
            if i >= 120:
                break
            out.append((prepare(frame.copy()), max(frame.info.get("duration", 100), 20) / 1000))
        return out


def render_pop_frames(overlay: Overlay | Path, out_dir: Path, name: str, box: int = POPUP_BOX,
                      tilt: float = 0.0, fps: int = 30) -> str:
    """Pre-render the animation as PNG frames on a fixed canvas: pop-in with bounce and fade-in, then
    (animated / pulsing overlays) one frame per video frame; ffmpeg holds the last frame of still ones.
    Returns the frame pattern relative to out_dir."""
    import math

    from PIL import Image

    if not isinstance(overlay, Overlay):
        overlay = Overlay(Path(overlay), 0.0, 2.0, box=box, tilt=tilt)
    frames = _source_frames(overlay)
    biggest = max(max(f.size) for f, _ in frames)
    side = int(biggest * max(POP_SCALES) * (1.06 if overlay.pulse else 1)) + 4
    side += side % 2
    moving = len(frames) > 1 or overlay.pulse
    count = max(len(POP_SCALES), math.ceil((overlay.end - overlay.start) * fps)) if moving else len(POP_SCALES)
    loop = sum(d for _, d in frames)
    for k in range(count):
        t = k / fps
        if len(frames) > 1:
            at, i = t % loop, 0
            while i < len(frames) - 1 and at >= frames[i][1]:
                at -= frames[i][1]
                i += 1
            img = frames[i][0]
        else:
            img = frames[0][0]
        s = POP_SCALES[k] if k < len(POP_SCALES) else 1.0
        if overlay.pulse and k >= len(POP_SCALES):
            s = 1.0 + 0.045 * math.sin(2 * math.pi * 1.4 * (t - len(POP_SCALES) / fps))
        w, h = max(1, round(img.width * s)), max(1, round(img.height * s))
        sprite = img.resize((w, h), Image.BILINEAR)
        if k < len(POP_ALPHA):
            alpha = sprite.getchannel("A").point(lambda a, f=POP_ALPHA[k]: int(a * f))
            sprite.putalpha(alpha)
        frame = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        frame.alpha_composite(sprite, ((side - w) // 2, (side - h) // 2))
        frame.save(out_dir / f"{name}_{k:03d}.png")
    return f"{name}_%03d.png"


def overlay_filter(index: int, input_index: int, overlay: Overlay, fps: int, src: str, dst: str) -> str:
    """Chain one overlay (input `input_index`, PNG frames) onto video `src` → `dst`."""
    duration = max(overlay.end - overlay.start, 0.3)
    hold = max(duration - len(POP_SCALES) / fps, 0.05)
    fade = min(0.25, duration / 4)
    cx, cy = overlay.xy()
    return (
        f"[{input_index}:v]format=rgba,tpad=stop_mode=clone:stop_duration={hold:.3f},"
        f"trim=duration={duration:.3f},"
        f"fade=t=out:st={duration - fade:.3f}:d={fade:.3f}:alpha=1,"
        f"setpts=PTS-STARTPTS+{overlay.start:.3f}/TB[pop{index}];"
        f"[{src}][pop{index}]overlay=x={cx}-w/2:y={cy}-h/2:eof_action=pass[{dst}]"
    )


def assemble_video(
    background: str | Path,
    voiceover: str | Path,
    timestamps_data: Any,
    output: str | Path = DEFAULT_OUTPUT,
    *,
    fit: Literal["crop", "blur"] = "crop",
    background_start: float = 0.0,
    fps: int = 30,
    crf: int = 20,
    preset: str = "medium",
    keep_ass: bool = True,
    progress: Callable[[float | None, str], None] | None = None,
    music: str | Path | None = None,
    music_volume: float = 0.14,
    music_start: float = 0.0,
    overlays: list[Overlay] | None = None,
    landscape: bool = False,
    **subtitle_style: Any,
) -> Path:
    """Render the final Short: 9:16 background + pop-up images + karaoke subtitles (+ title card via
    `title_card=`) + voiceover (+ ducked music). `landscape`: a 16:9 1920x1080 video instead (overlays
    are laid out for 9:16 and moved over)."""
    background, voiceover, output = Path(background), Path(voiceover), Path(output)
    overlays = [o for o in overlays or [] if o.end > o.start]
    size = LANDSCAPE if landscape else (WIDTH, HEIGHT)
    if landscape:
        overlays = [to_landscape(o) for o in overlays]
        subtitle_style = {**LANDSCAPE_SUBTITLES, **subtitle_style, "size": size}
    checks = [(background, "Background video"), (voiceover, "Voiceover")]
    if music is not None:
        checks.append((Path(music), "Music track"))
    checks += [(Path(o.image), "Pop-up image") for o in overlays]
    for path, label in checks:
        if not path.is_file():
            raise AssemblyError(f"{label} not found: {path}")

    ffmpeg = ffmpeg_with_libass()
    voice_len = media_duration(voiceover)
    bg_len = media_duration(background)
    if bg_len > 1:
        background_start %= bg_len  # seeking past the end of a looped input fails, so wrap around
    if music is not None and music_start and (music_len := media_duration(Path(music))) > 1:
        music_start %= music_len
    loop = bg_len - background_start < voice_len
    output.parent.mkdir(parents=True, exist_ok=True)

    font_name, font_file = find_font_file(subtitle_style.pop("font", None))
    with tempfile.TemporaryDirectory(prefix="trendclip-assemble-") as work:
        work_dir = Path(work)
        fonts_dir = work_dir / "fonts"
        fonts_dir.mkdir()
        if font_file:
            shutil.copyfile(font_file, fonts_dir / font_file.name)
        ass_path = create_karaoke_ass_file(timestamps_data, work_dir / "subs.ass", font=font_name, **subtitle_style)

        tmp_out = output.with_name(output.stem + ".rendering.mp4")
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
        if loop:
            cmd += ["-stream_loop", "-1"]
        if background_start:
            cmd += ["-ss", f"{background_start:.2f}"]
        cmd += ["-i", str(background.resolve()), "-i", str(voiceover.resolve())]
        graph = video_filter(fit, fps, size)
        audio_out = "1:a:0"
        if music is not None:
            cmd += ["-stream_loop", "-1", "-ss", f"{music_start:.2f}", "-i", str(Path(music).resolve())]
            graph += ";" + music_filter(voice_len, music_volume)
            audio_out = "[aout]"
        current = "base"
        first_input = 3 if music is not None else 2
        for i, ov in enumerate(overlays):
            pattern = render_pop_frames(ov, work_dir, f"pop{i}", fps=fps)
            cmd += ["-framerate", str(fps), "-i", pattern]
            graph += ";" + overlay_filter(i, first_input + i, ov, fps, current, f"ov{i}")
            current = f"ov{i}"
        # Relative names: the subtitles filter's own escaping breaks on ':' and quotes in paths.
        graph += f";[{current}]subtitles=subs.ass:fontsdir=fonts[v]"
        cmd += [
            "-filter_complex", graph,
            "-map", "[v]", "-map", audio_out,
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{voice_len:.3f}",
            "-movflags", "+faststart",
            str(tmp_out.resolve()),
        ]
        logger.info("Rendering %s (%.1fs, font %s, fit %s)", output.name, voice_len, font_name, fit)
        with tempfile.TemporaryFile(mode="w+") as err_log:
            proc = subprocess.Popen(cmd, cwd=work_dir, stdout=subprocess.PIPE, stderr=err_log, text=True)
            for line in proc.stdout:
                if progress and line.startswith("out_time_us=") and line[12:].strip().isdigit():
                    done = int(line[12:]) / 1e6
                    progress(min(done / voice_len, 0.99), f"Rendering video: {done:.0f}s of {voice_len:.0f}s")
            proc.wait()
            err_log.seek(0)
            stderr = err_log.read()
        if proc.returncode != 0 or not tmp_out.exists():
            tmp_out.unlink(missing_ok=True)
            raise AssemblyError(f"ffmpeg failed: {stderr.strip()[-600:]}")
        os.replace(tmp_out, output)
        if keep_ass:
            shutil.copyfile(ass_path, output.with_suffix(".ass"))
    return output


# --------------------------------------------------------------------------- demo voice


def synthesize_demo_voice(text: str, output_mp3: Path, voice: str | None = None) -> Path:
    """Free local test voice via macOS `say` (no word timestamps; pair with estimate_word_timings)."""
    say = shutil.which("say")
    if not say:
        raise AssemblyError("Demo voice needs macOS `say`; pass --voice and --timestamps instead")
    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        aiff = Path(work) / "voice.aiff"
        cmd = [say, "-o", str(aiff)] + (["-v", voice] if voice else []) + [text]
        subprocess.run(cmd, check=True, timeout=120)
        subprocess.run(
            [ffmpeg_with_libass(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(aiff),
             "-c:a", "libmp3lame", "-b:a", "192k", str(output_mp3)],
            check=True, timeout=120,
        )
    return output_mp3


DEMO_TEXT = (
    "This Minecraft trick is going viral right now. Watch closely, because most players "
    "never notice it. Follow for more gaming secrets!"
)


def _cli_overlays(popups_json: str, words: list[Word]) -> list[Overlay]:
    from . import popups
    from .config import get_settings

    settings = get_settings()
    wanted = [popups.Popup(**p) for p in json.loads(Path(popups_json).read_text(encoding="utf-8"))]
    overlays = []
    for timed in popups.schedule(wanted, words):
        if asset := popups.fetch_asset(settings, timed.popup):
            x, tilt = popup_layout(len(overlays))
            overlays.append(Overlay(settings.assets_dir / "popups" / asset.filename, timed.start, timed.end, x, tilt))
            print(f"pop-up {timed.popup.word!r} {timed.start:.2f}-{timed.end:.2f}s: {asset.title}")
    return overlays


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble a 9:16 Short with karaoke subtitles.")
    parser.add_argument("--background", default=str(DEFAULT_BACKGROUND), help="Background MP4 (default: latest_gameplay.mp4)")
    parser.add_argument("--voice", help="Voiceover audio (MP3/WAV/M4A)")
    parser.add_argument("--timestamps", help="Word timestamps JSON")
    parser.add_argument("--text", help="Script text: estimate timings instead of --timestamps (approximate)")
    parser.add_argument("--demo", nargs="?", const=DEMO_TEXT, metavar="TEXT",
                        help="Make a test voiceover with macOS `say` and estimated timings")
    parser.add_argument("--say-voice", help="macOS voice for --demo (e.g. Samantha, Daniel)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--fit", choices=["crop", "blur"], default="crop",
                        help="crop = fill 9:16 (cuts the sides); blur = whole frame over a blurred fill")
    parser.add_argument("--start", type=float, default=0.0, help="Start offset in the background video (s)")
    parser.add_argument("--font", help=f"Font family (default: first available of {', '.join(FONT_PREFERENCE)})")
    parser.add_argument("--font-size", type=int, default=120)
    parser.add_argument("--highlight", default="yellow", help="yellow | cyan | green | orange | #RRGGBB")
    parser.add_argument("--max-words", type=int, default=4, choices=range(1, 7))
    parser.add_argument("--position", type=int, default=620, help="Subtitle distance from the bottom (px of 1920)")
    parser.add_argument("--music", default=str(DEFAULT_MUSIC) if DEFAULT_MUSIC.is_file() else None,
                        help="Background music (default: assets/music/background.mp3 if it exists)")
    parser.add_argument("--no-music", action="store_true")
    parser.add_argument("--music-volume", type=float, default=0.14, help="Music level under the voice (0.12-0.15 ≈ -18..-16 dB)")
    parser.add_argument("--title-card", default="", help="Top banner for the first 3 seconds, e.g. 'CAT LOGIC 101'")
    parser.add_argument("--popups", help='JSON file: [{"word": "cat", "emoji": "🐈", "query": "cat"}, ...]')
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    output = Path(args.output)
    try:
        if args.demo is not None:
            voice = synthesize_demo_voice(args.demo, output.with_name("demo_voice.mp3"), args.say_voice)
            timings: Any = estimate_word_timings(args.demo, media_duration(voice))
            output.with_name("demo_timestamps.json").write_text(
                json.dumps([w.__dict__ for w in timings], indent=2), encoding="utf-8")
        else:
            if not args.voice or not (args.timestamps or args.text):
                parser.error("pass --voice with --timestamps (or --text), or use --demo")
            voice = Path(args.voice)
            timings = args.timestamps or estimate_word_timings(args.text, media_duration(voice))

        overlays = _cli_overlays(args.popups, load_word_timings(timings)) if args.popups else []
        result = assemble_video(
            args.background, voice, timings, output,
            fit=args.fit, background_start=args.start, font=args.font, font_size=args.font_size,
            highlight=args.highlight, max_words=args.max_words, margin_v=args.position,
            music=None if args.no_music else args.music, music_volume=args.music_volume,
            title_card=args.title_card, overlays=overlays,
        )
    except (AssemblyError, OSError, subprocess.CalledProcessError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
