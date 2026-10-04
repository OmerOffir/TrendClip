"""Phase 4: turn a background gameplay clip + voiceover + word timestamps into a 9:16 Short.

    background MP4 ─┐
    voiceover MP3  ─┼─ ffmpeg: scale/crop to 1080x1920 → burn karaoke .ass subtitles → mux voice
    word timings   ─┘                                             → output/final_short.mp4

Subtitles show 2-4 words at a time (uppercase, white, heavy black outline); the word being spoken
turns yellow (or cyan) for exactly its duration.

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
DEFAULT_BACKGROUND = PROJECT_ROOT / "assets" / "backgrounds" / "latest_gameplay.mp4"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "final_short.mp4"

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
) -> Path:
    """Write a 1080x1920 .ass file: 2-4 words per line, the spoken word recoloured for its duration.

    Each word gets its own Dialogue event showing the whole line with that word highlighted, from the
    word's start until the next word starts, so the colour moves exactly with the voice.
    """
    words = load_word_timings(timestamps_data)
    chunks = chunk_words(words, min_words=min_words, max_words=max_words)
    hl = _resolve_color(highlight)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{font},{font_size},{WHITE},{hl},{BLACK},&H80000000,-1,0,0,0,100,100,2,0,1,{outline},{shadow},2,70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
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


def video_filter(fit: Literal["crop", "blur"], fps: int) -> str:
    """Filter graph from input 0 to a 1080x1920 [base] stream."""
    cover = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}"
    if fit == "crop":
        return f"[0:v]{cover},setsar=1,fps={fps}[base]"
    # Whole 16:9 frame in the middle, a blurred zoomed copy filling the rest.
    return (
        f"[0:v]split=2[bgsrc][fgsrc];"
        f"[bgsrc]{cover},boxblur=24:2,eq=brightness=-0.08[bg];"
        f"[fgsrc]scale={WIDTH}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps}[base]"
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
    **subtitle_style: Any,
) -> Path:
    """Render the final Short: 9:16 background + voiceover + burned-in karaoke subtitles."""
    background, voiceover, output = Path(background), Path(voiceover), Path(output)
    for path, label in ((background, "Background video"), (voiceover, "Voiceover")):
        if not path.is_file():
            raise AssemblyError(f"{label} not found: {path}")

    ffmpeg = ffmpeg_with_libass()
    voice_len = media_duration(voiceover)
    bg_len = media_duration(background)
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
        cmd += [
            "-i", str(background.resolve()),
            "-i", str(voiceover.resolve()),
            # Relative names: the subtitles filter's own escaping breaks on ':' and quotes in paths.
            "-filter_complex", f"{video_filter(fit, fps)};[base]subtitles=subs.ass:fontsdir=fonts[v]",
            "-map", "[v]", "-map", "1:a:0",
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

        result = assemble_video(
            args.background, voice, timings, output,
            fit=args.fit, background_start=args.start, font=args.font, font_size=args.font_size,
            highlight=args.highlight, max_words=args.max_words, margin_v=args.position,
        )
    except (AssemblyError, OSError, subprocess.CalledProcessError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
