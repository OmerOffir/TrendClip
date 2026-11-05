"""The map's locals: the owner's own chibi characters (stickers/borderlinewhy/<name>.gif) standing on a place,
popping in, bobbing, and reacting on a word.

The owner's call (2026-11-05): no code-drawn white bean-heads on Borderline Why; the people on the map are their own
GIF characters (the professor in his suits, the scholar with the quill, the citizens, the farmer, the king...). A
reaction keeps the character: when the character has a variant for the mood (citizen_cheering,
the professor's "surprised" face for his many outfits) the GIF switches to it; otherwise the character hops and an
emote pops over its head (a burst for a shock, a cross for a loss, a check for a win). Price Tag Why's story cast
stays the bean-head people of modules.pw_cast (the owner likes them there).
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

from modules import bw_fx

MOODS = ("neutral", "smile", "grin", "sad", "shocked", "think", "smug", "wink")
# a mood's GIF variants, tried in order as <character>_<suffix> (the owner adds them)
VARIANTS = {"shocked": ("surprised",), "grin": ("celebrating", "laughing", "cheering"), "sad": ("sad", "thinking", "listening"),
            "think": ("thinking",), "smile": ("nodding", "greeting", "speaking"), "smug": ("pointing", "gesturing"), "wink": ("waving",)}
PROFESSOR = frozenset({"scholar", "speaker", "leader", "merchant", "worker", "mapmaker", "officer"})  # the owner's professor, in his outfits
EMOTES = {"shocked": "burst", "grin": "check", "sad": "cross", "think": "question", "smug": "check"}  # over the head when there is no variant
DEFAULT = "citizen_walking"
POP = 0.4  # seconds of the hop when a mood changes


def smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def pop(t: float, since: float) -> float:
    """The hop (pixels up) a mood change gives the whole character."""
    s = (t - since) / POP
    return -22 * math.sin(math.pi * s) if 0 <= s < 1 else 0.0


def character(name: str = "") -> str:
    """The character the script names (an icon name or an alias like "farmer"), as its file's name; else the
    walking citizen."""
    code = bw_fx.icon_code(name) if name else ""
    path = bw_fx.custom_icons().get(code) if code else None
    return path.stem if path is not None else DEFAULT


def variant(code: str, mood: str) -> str | None:
    """The character's own GIF for a mood, when the owner drew one; None otherwise."""
    base = code.split("_")[0]
    files = bw_fx.custom_icons()
    for suffix in VARIANTS.get(mood, ()):
        if f"{base}_{suffix}" in files and not bw_fx.is_brand(f"{base}_{suffix}"):
            return f"{base}_{suffix}"
    if mood == "shocked" and base in PROFESSOR and "surprised" in files:
        return "surprised"
    return None


def draw(ov: Image.Image, code: str, xy: tuple[float, float], size: int, t: float, at: float, mood: str = "",
         mood_at: float | None = None, flip: bool = False) -> None:
    """The character at xy (its centre), `size` pixels, popping in at `at`, bobbing; from mood_at on in `mood`:
    its variant GIF when there is one, else the same GIF with a hop and an emote over its head."""
    u = (t - at) / 0.4
    if u <= 0:
        return
    s = bw_fx.backout(u)
    reacted = bool(mood) and mood_at is not None and t >= mood_at
    now = (variant(code, mood) if reacted else None) or code
    im, _ = bw_fx.icon_frame(now, size, t - (mood_at if reacted and now != code else at))
    if flip:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    if s < 1:
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.BILINEAR)
    hop = pop(t, mood_at) if reacted else 0.0
    if reacted and mood == "sad" and now == code:
        hop += 10 * smooth((t - mood_at) / 0.6)  # no sad face drawn: the character sinks a little
    x, y = xy[0], xy[1] + 5 * math.sin(2.4 * t + at) + hop
    w, h = im.size
    sh = Image.new("RGBA", (w, max(2, h // 5)), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([w * 0.2, 0, w * 0.8, max(2, h // 5) - 1], fill=(0, 0, 0, int(90 * min(1, s))))
    ov.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6)), (int(x - w / 2), int(xy[1] + h * 0.4)))
    ov.alpha_composite(im, (int(x - w / 2), int(y - h / 2)))
    if reacted and now == code and (emote := EMOTES.get(mood)):  # no variant: the emote says it
        es = bw_fx.backout((t - mood_at) / 0.4)
        if es > 0.02:
            px = max(4, int(size * 0.42 * es))
            badge, _ = bw_fx.icon_frame(bw_fx.icon_code(emote) or emote, px, t - mood_at)
            badge = badge.rotate(8 * math.sin(3 * t), resample=Image.BILINEAR)
            ov.alpha_composite(badge, (int(x + w * 0.28 - badge.width / 2), int(y - h / 2 - badge.height * 0.45 + 4 * math.sin(2.8 * t))))
