"""A thumbnail for every planned video, to upload by hand in YouTube Studio (the API can't set one on a Short).

One 1080x1920 JPEG per video, drawn once and kept beside the video (redrawn when the video is newer):
  - Borderline Why  the place close up on the map with the story's sides, circled in red marker, the cover
                    question in huge letters and the panda as a sticker (modules.bw_long.thumbnail, portrait)
  - Dark Outliers   the hook's picture, darkened, the cover text huge with its last words in red
  - SideQuestLogic  a clean frame of the gameplay, "LEVEL N" huge in yellow, the rest of the title card under it

The dashboard's Upcoming tab shows each one with a Download button (trendclip.web.app). Drawing runs in its own
process (python -m modules.thumbs <channel> <ref>): the map renderer keeps its layout in module globals.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from modules import bw_geo

PROJECT_ROOT = bw_geo.PROJECT_ROOT
W, H = 1080, 1920
CHANNELS = ("borderlinewhy", "darkarchives", "sidequestlogic")
YELLOW, RED, WHITE, BLACK = (255, 214, 0), (226, 38, 38), (255, 255, 255), (0, 0, 0)


class ThumbError(ValueError):
    """No video (or nothing to draw it from) for that reference."""


def _safe(ref: str) -> str:
    ref = str(ref)
    if not re.fullmatch(r"[\w.\-]+", ref) or ref.startswith("."):
        raise ThumbError(f"bad reference {ref!r}")
    return ref


def paths(channel: str, ref: str, out_dir: Path | None = None) -> tuple[Path, Path]:
    """(the video, its thumbnail) for a dashboard row's channel and ref."""
    out = out_dir or PROJECT_ROOT / "output"
    ref = _safe(ref)
    if channel == "borderlinewhy":
        folder = out / "borderlinewhy" / ref
        return folder / "video.mp4", folder / "thumbnail.jpg"
    if channel == "darkarchives":
        stem = f"story_{int(ref)}"
        return out / "darkarchives" / f"{stem}.mp4", out / "darkarchives" / f"{stem}.thumbnail.jpg"
    if channel == "sidequestlogic":
        video = out / "shorts" / (ref if ref.endswith(".mp4") else f"{ref}.mp4")
        return video, video.with_suffix(".thumbnail.jpg")
    raise ThumbError(f"unknown channel {channel!r}")


def fresh(channel: str, ref: str, out_dir: Path | None = None) -> Path | None:
    """The thumbnail when it is drawn and newer than its video, else None."""
    video, thumb = paths(channel, ref, out_dir)
    if thumb.is_file() and (not video.is_file() or thumb.stat().st_mtime >= video.stat().st_mtime):
        return thumb
    return None


def ensure(channel: str, ref: str, out_dir: Path | None = None, timeout: float = 240) -> Path:
    """The thumbnail, drawn now (in its own process) when it is missing or older than the video."""
    if done := fresh(channel, ref, out_dir):
        return done
    video, thumb = paths(channel, ref, out_dir)
    if not video.is_file():
        raise ThumbError("the video isn't made yet")
    args = [sys.executable, "-m", "modules.thumbs", channel, str(ref)] + ([str(out_dir)] if out_dir else [])
    run = subprocess.run(args, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout)
    if run.returncode != 0 or not thumb.is_file():
        raise ThumbError((run.stderr or run.stdout or "drawing failed").strip().splitlines()[-1][:300])
    return thumb


def draw(channel: str, ref: str, out_dir: Path | None = None) -> Path:
    """Draw the thumbnail in this process (the subprocess's work)."""
    video, thumb = paths(channel, ref, out_dir)
    if not video.is_file():
        raise ThumbError("the video isn't made yet")
    {"borderlinewhy": borderline, "darkarchives": dark, "sidequestlogic": sidequest}[channel](video, thumb)
    return thumb


# --------------------------------------------------------------------------- drawing helpers


def font(size: int):
    from modules import bw_render

    return bw_render.font(size)


def cover(img: Image.Image) -> Image.Image:
    """`img` cropped and scaled to fill 1080x1920 (centered)."""
    img = img.convert("RGB")
    s = max(W / img.width, H / img.height)
    img = img.resize((max(W, round(img.width * s)), max(H, round(img.height * s))), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


def video_frame(video: Path, at: float) -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "frame.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, at):.2f}", "-i", str(video),
                        "-frames:v", "1", str(out)], check=True)
        return Image.open(out).convert("RGB")


def duration(video: Path) -> float:
    run = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True)
    try:
        return float(run.stdout.strip())
    except ValueError:
        return 0.0


def fit(text: str, start: int, width: int, lines: int, least: int = 80) -> tuple[int, list[str]]:
    """The biggest size (from `start`, down by 6) at which `text` wraps into at most `lines` lines of `width`."""
    from modules import bw_render

    size = start
    while size > least and (len(bw_render.wrap(text, size, width)) > lines
                            or max(font(size).getlength(w) for w in text.split()) > width):
        size -= 6
    return size, bw_render.wrap(text, size, width)


def shade(img: Image.Image, top: float, bottom: float, strength: int = 200) -> None:
    """Darken the top `top` and the bottom `bottom` of the frame (fractions of its height), fading to the middle."""
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for y in range(0, H, 4):
        a = max(0.0, 1 - y / (H * top)) if top else 0.0
        b = max(0.0, 1 - (H - y) / (H * bottom)) if bottom else 0.0
        d.rectangle([0, y, W, y + 4], fill=(0, 0, 0, int(strength * max(a, b))))
    img.alpha_composite(ov)


def badge(img: Image.Image, text: str, fill, ink, y: int = 70) -> None:
    """A rounded pill with the channel's name, centered at the top."""
    d = ImageDraw.Draw(img)
    f = font(54)
    w = f.getlength(text)
    box = ((W - w) / 2 - 34, y, (W + w) / 2 + 34, y + 84)
    d.rounded_rectangle(box, radius=42, fill=fill)
    d.text((W / 2, y + 42), text, font=f, fill=ink, anchor="mm")


# --------------------------------------------------------------------------- the channels


def borderline(video: Path, out: Path) -> None:
    from modules import bw_long, bw_render

    folder = video.parent
    script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
    raw = json.loads((folder / "voice.words.json").read_text(encoding="utf-8"))
    words = [bw_render.Word(w["word"], float(w["start"]), float(w["end"]), w.get("who", "")) for w in raw]
    bw_render.use_layout("portrait")
    spec = bw_render.spec_from_dict(script, words)
    bw_render.prepare(spec)
    bw_long.thumbnail(spec, out, script.get("thumb_text", ""), script.get("thumb_icon", ""))


def dark(video: Path, out: Path) -> None:
    """The hook's picture (the first beat's, graded like the video), else a frame from the middle of the video."""
    stem = video.stem
    info = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
    pics = video.parent / stem / "pictures"
    pic = next((p for p in (pics / "graded_beat_1.jpg", pics / "beat_1.jpg") if p.is_file()), None)
    img = cover(Image.open(pic) if pic else video_frame(video, duration(video) * 0.35))
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = img.convert("RGBA")
    shade(img, 0.55, 0.3, 215)
    text = " ".join((info.get("cover_text") or info.get("title") or "").upper().split())
    size, lines = fit(text, 190, W - 120, 4)
    f = font(size)
    d = ImageDraw.Draw(img)
    # the last line (the twist, or the "?") in red, the rest white; set high so the Shorts shelf's crop keeps it
    y = 230
    for n, line in enumerate(lines):
        last = n == len(lines) - 1 and len(lines) > 1
        d.text((W / 2, y), line, font=f, fill=RED if last else WHITE, stroke_width=12, stroke_fill=BLACK, anchor="ma")
        y += int(size * 1.05)
    badge(img, "DARK OUTLIERS", (12, 12, 14, 235), (230, 230, 230))
    img.convert("RGB").save(out, quality=92)


def sidequest(video: Path, out: Path) -> None:
    """A clean frame of the gameplay (the clip the video was cut from), else of the video itself."""
    info = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
    clip = PROJECT_ROOT / "assets" / "backgrounds" / str(info.get("background") or "")
    if info.get("background") and clip.is_file():
        img = video_frame(clip, duration(clip) * 0.3)
    else:
        img = video_frame(video, 0.1)
    img = ImageEnhance.Color(ImageEnhance.Contrast(cover(img)).enhance(1.15)).enhance(1.3).convert("RGBA")
    shade(img, 0.5, 0.25, 190)
    card = " ".join(str(info.get("title_card") or info.get("title") or "").upper().split())
    head, _, rest = card.partition(" - ")
    if not rest:  # no "LEVEL 12 - ..." form: the whole card in the big box
        head, rest = card, ""
    d = ImageDraw.Draw(img)
    size, lines = fit(head, 230, W - 160, 2)
    f = font(size)
    y = 210
    for line in lines:  # yellow box, black letters: the series' look
        w = f.getlength(line)
        box = d.textbbox((W / 2, y), line, font=f, anchor="ma")
        d.rectangle(((W - w) / 2 - 36, box[1] - 26, (W + w) / 2 + 36, box[3] + 26), fill=YELLOW)
        d.text((W / 2, y), line, font=f, fill=BLACK, anchor="ma")
        y = box[3] + 40
    if rest:
        size, lines = fit(rest, 130, W - 120, 2)
        f = font(size)
        y += 10
        for line in lines:
            d.text((W / 2, y), line, font=f, fill=WHITE, stroke_width=10, stroke_fill=BLACK, anchor="ma")
            y += int(size * 1.08)
    q = font(420)  # the big question mark, a little tilted
    mark = Image.new("RGBA", (420, 520), (0, 0, 0, 0))
    ImageDraw.Draw(mark).text((210, 260), "?", font=q, fill=YELLOW, stroke_width=18, stroke_fill=BLACK, anchor="mm")
    mark = mark.rotate(-12, expand=True, resample=Image.BICUBIC)
    glow = Image.new("RGBA", mark.size, (0, 0, 0, 0))
    glow.putalpha(mark.getchannel("A").filter(ImageFilter.GaussianBlur(18)).point(lambda a: a * 0.6))
    at = ((W - mark.width) // 2, max(y + 20, int(H * 0.5)))
    img.alpha_composite(glow, at)
    img.alpha_composite(mark, at)
    badge(img, "GAMER IQ", (12, 12, 14, 235), YELLOW, y=H - 230)
    img.convert("RGB").save(out, quality=92)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m modules.thumbs <channel> <ref> [output dir]")
        return 2
    print(draw(argv[0], argv[1], Path(argv[2]) if len(argv) > 2 else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
