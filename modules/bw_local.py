"""The map's locals: the owner's own chibi characters (stickers/borderlinewhy/<name>.gif) standing on a place,
popping in, bobbing, and reacting on a word.

The owner's call (2026-11-05): no code-drawn white bean-heads on Borderline Why; the people on the map are their own
GIF characters (the professor in his suits, the scholar with the quill, the citizens, the farmer, the king...). A
reaction keeps the character: when the character has a variant for the mood (citizen_cheering,
the professor's "surprised" face for his many outfits) the GIF switches to it; otherwise the character hops and an
emote pops over its head (a burst for a shock, a cross for a loss, a check for a win). Price Tag Why's story cast
stays the bean-head people of modules.pw_cast (the owner likes them there).

Mood families (the owner, 2026-11-05): citizen, farmer, king, settler and soldier came as a set of their own,
<name>_surprised / _sad / _happy, one simple chibi person per name, every GIF opening and closing on the same calm
stance. A local of a family stands still on that stance, plays its mood GIF once on the react word and rests on the
last frame (the mood stays on the face); every GIF of a family is scaled to the same standing height and centred on the stance, so the person keeps its size.
Any <name>_happy the owner adds makes <name> a family.
"""

from __future__ import annotations

import bisect
import math

from PIL import Image, ImageDraw, ImageFilter

from modules import bw_fx

MOODS = ("neutral", "smile", "grin", "sad", "shocked", "think", "smug", "wink")
# a mood's GIF variants, tried in order as <character>_<suffix> (the owner adds them)
VARIANTS = {"shocked": ("surprised",), "grin": ("happy", "celebrating", "laughing", "cheering"), "sad": ("sad", "thinking", "listening"),
            "think": ("thinking",), "smile": ("happy", "nodding", "greeting", "speaking"), "smug": ("pointing", "gesturing"), "wink": ("waving",)}
PROFESSOR = frozenset({"scholar", "speaker", "leader", "merchant", "worker", "mapmaker", "officer"})  # the owner's professor, in his outfits
EMOTES = {"shocked": "burst", "grin": "check", "sad": "cross", "think": "question", "smug": "check"}  # over the head when there is no variant
DEFAULT = "citizen_walking"
FAMILY_MOODS = {"shocked": "surprised", "sad": "sad", "grin": "happy", "smile": "happy", "smug": "happy", "wink": "happy"}
FAMILY_NAMES = {"villager": "citizen", "person": "citizen", "migrant": "citizen", "local": "citizen", "people": "citizen",
                "pioneer": "settler", "settlers": "settler", "colonist": "settler", "homesteader": "settler",
                "soldiers": "soldier", "troops": "soldier", "army": "soldier", "monarch": "king", "farmers": "farmer"}
_families: dict[tuple[str, int], dict[str, tuple[list[Image.Image], list[float]]]] = {}
POP = 0.4  # seconds of the hop when a mood changes


def smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def pop(t: float, since: float) -> float:
    """The hop (pixels up) a mood change gives the whole character."""
    s = (t - since) / POP
    return -22 * math.sin(math.pi * s) if 0 <= s < 1 else 0.0


def family(name: str) -> str | None:
    """The mood family a name belongs to ("farmer", "settlers", "farmer_working" -> "farmer"), when the owner drew
    one (<family>_happy is there); None otherwise."""
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    files = bw_fx.custom_icons()
    for c in (key, FAMILY_NAMES.get(key, ""), key.split("_")[0], FAMILY_NAMES.get(key.split("_")[0], "")):
        if c and f"{c}_happy" in files:
            return c
    return None


def family_frames(base: str, size: int) -> dict[str, tuple[list[Image.Image], list[float]]]:
    """A family's GIFs by mood ("happy", "surprised", "sad"), each scaled so its calm stance is `size` tall and
    cropped around it so the stance sits in the middle (a jump reaches above it); with the frames' end times."""
    key = (base, size)
    if key not in _families:
        files = bw_fx.custom_icons()
        raw: dict[str, tuple[list[Image.Image], list[float]]] = {}
        for mood in ("happy", "surprised", "sad"):
            if f"{base}_{mood}" not in files:
                continue
            im = Image.open(files[f"{base}_{mood}"])
            frames, ends, end = [], [], 0.0
            for n in range(getattr(im, "n_frames", 1)):
                im.seek(n)
                frames.append(im.convert("RGBA"))
                end += max(0.02, (im.info.get("duration") or 40) / 1000)
                ends.append(end)
            raw[mood] = (frames, ends)
        out = {}
        for m, (fr, ends) in raw.items():  # each GIF on its own: the owner drew the person a little bigger in some
            sx0, sy0, sx1, sy1 = fr[0].getchannel("A").getbbox()  # the calm stance it opens on
            boxes = [b for f in fr if (b := f.getchannel("A").getbbox())]
            cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
            hw = max(max(cx - b[0], b[2] - cx) for b in boxes)
            hh = max(max(cy - b[1], b[3] - cy) for b in boxes)
            box = (round(cx - hw), round(cy - hh), round(cx + hw), round(cy + hh))  # the stance's centre in the middle
            scale = size / max(1, sy1 - sy0)  # the stance `size` tall in every GIF
            wh = (max(1, round((box[2] - box[0]) * scale)), max(1, round((box[3] - box[1]) * scale)))
            out[m] = ([f.crop(box).resize(wh, Image.LANCZOS) for f in fr], ends)
        _families[key] = out
    return _families[key]


def character(name: str = "") -> str:
    """The character the script names: "@<family>" for a mood family, else the GIF's file name (an icon name or
    an alias like "farmer"); the citizen when the name is empty or unknown."""
    if name and (base := family(name)):
        return f"@{base}"
    code = bw_fx.icon_code(name) if name else ""
    path = bw_fx.custom_icons().get(code) if code else None
    if path is not None:
        return path.stem
    return "@citizen" if family("citizen") else DEFAULT


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
    if code.startswith("@"):  # a mood family: the calm stance, then the mood GIF once, resting on its last frame
        fam = family_frames(code[1:], size)
        clip = fam.get(FAMILY_MOODS.get(mood, "")) if reacted else None
        if clip is None:
            im, now = next(iter(fam.values()))[0][0], code
        else:
            frames, ends = clip
            im, now = frames[min(len(frames) - 1, bisect.bisect_right(ends, t - mood_at))], "@played"
    else:
        now = (variant(code, mood) if reacted else None) or code
        im, _ = bw_fx.icon_frame(now, size, t - (mood_at if reacted and now != code else at))
    if flip:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    if s < 1:
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.BILINEAR)
    hop = pop(t, mood_at) if reacted and now != "@played" else 0.0  # a played mood moves by itself
    if reacted and mood == "sad" and now == code:
        hop += 10 * smooth((t - mood_at) / 0.6)  # no sad face drawn: the character sinks a little
    x, y = xy[0], xy[1] + 5 * math.sin(2.4 * t + at) + hop
    w, h = im.size
    body_w, body_h = (size * 0.75 * min(1, s), size * min(1, s)) if code.startswith("@") else (w, h)  # a family's crop has room for the jump
    sh = Image.new("RGBA", (int(body_w), max(2, int(body_h) // 5)), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([body_w * 0.2, 0, body_w * 0.8, max(2, int(body_h) // 5) - 1], fill=(0, 0, 0, int(90 * min(1, s))))
    ov.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6)), (int(x - body_w / 2), int(xy[1] + body_h * 0.4)))
    ov.alpha_composite(im, (int(x - w / 2), int(y - h / 2)))
    if reacted and now == code and (emote := EMOTES.get(mood)):  # no variant: the emote says it
        es = bw_fx.backout((t - mood_at) / 0.4)
        if es > 0.02:
            px = max(4, int(size * 0.42 * es))
            badge, _ = bw_fx.icon_frame(bw_fx.icon_code(emote) or emote, px, t - mood_at)
            badge = badge.rotate(8 * math.sin(3 * t), resample=Image.BILINEAR)
            ov.alpha_composite(badge, (int(x - body_w * 0.3 - badge.width / 2), int(y - body_h / 2 - badge.height * 0.7 + 4 * math.sin(2.8 * t))))
