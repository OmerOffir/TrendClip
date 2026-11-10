"""Quest: "Can You Beat Minecraft?" - a long vertical adventure the bot draws itself, no gameplay behind it.

The owner's idea (2026-11-10): about three minutes, the way from the first tree to the Ender Dragon in seven
stops, with a pause moment at every stop. The viewer starts with ten hearts; what they stop on says how many
hearts they lose (or win back). Only hearts are counted, on fingers: every stop stands on its own, and the one
thing asked at the end is "Comment your hearts".

A pixel world drawn at 180x320 and blown up 6x, with the HUD, the cards and the captions drawn sharp on top.
The narrator plays along ("I stopped on a stone sword"), so every stop's outcome is seen, not only written.
Every stop is a `Stop` class: its words, its options (what a pause can land on), its backdrop and its scene.
"""
from __future__ import annotations

import array
import logging
import math
import os
import random
import shutil
import subprocess
import sys
import time
import wave
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

from .config import Settings
from .video_assembler import Word

logger = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30
S = 6  # one world pixel on screen
LW, LH = W // S, H // S
GROUND = 176  # the grass line, in world pixels
STRIP = 720  # the world repeats after this many pixels
HERO_X, WALK = 52, 28.0  # where the hero stands; world pixels a second while walking
HOLD = 2  # video frames per option, as in the pause Shorts
HEARTS = 10
GAP = 0.5  # quiet after a stop's last word
FADE = 0.28  # to black and back between stops
GREEN, ORANGE, RED, GREY = (40, 170, 70), (230, 140, 20), (200, 40, 40), (90, 96, 110)

ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def _say(n: int) -> str:
    """A number as the narrator says it (the cues are found by the letters, so no digits in the script)."""
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else "")
    return ONES[n // 100] + " hundred" + (" " + _say(n % 100) if n % 100 else "")


def _hearts(n: int) -> str:
    return f"{_say(abs(n))} heart{'' if abs(n) == 1 else 's'}"


# --------------------------------------------------------------------------- the pixel world
def _noise(x: int, y: int, salt: int = 0) -> float:
    n = (x * 374761393 + y * 668265263 + salt * 2147483647) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 0xFFFF


def _shade(colour: tuple, k: float) -> tuple:
    return tuple(max(0, min(255, int(c * k))) for c in colour)


def _mix(a: tuple, b: tuple, k: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def _ease(k: float) -> float:
    k = max(0.0, min(1.0, k))
    return k * k * (3 - 2 * k)


def _tree(d, x: int, base: int, height: int, leaf: tuple, trunk: tuple, salt: int) -> None:
    """An oak: a bark trunk and a ragged canopy of leaf blocks, lit from the top left."""
    top = base - height
    for y in range(top + 14, base):
        for px in range(x - 4, x + 4):
            n = _noise(px // 2, y // 3, salt)
            k = (0.76 if px >= x + 2 else 1.0) * (0.82 if n < 0.3 else 1.12 if n > 0.85 else 1.0)
            d.point((px, y), fill=_shade(trunk, k))
    for x0, y0, x1, y1 in ((x - 22, top + 4, x + 21, top + 19), (x - 16, top - 6, x + 15, top + 4),
                           (x - 8, top - 14, x + 7, top - 6)):
        for px in range(x0, x1 + 1):
            for py in range(y0, y1 + 1):
                rim = px - x0 < 2 or x1 - px < 2 or py - y0 < 2 or y1 - py < 2
                if rim and _noise(px // 2, py // 2, salt + 1) < 0.3:
                    continue  # a ragged edge
                n = _noise(px // 2, py // 2, salt)
                light = 1.14 - 0.36 * (py - top + 14) / 34 - 0.1 * (px - x) / 22
                d.point((px, py), fill=_shade(leaf, light * (0.72 if n < 0.18 else 1.2 if n > 0.86 else 1.0)))


ORES = ((92, 226, 236), (44, 44, 50), (222, 176, 150), (250, 214, 74))  # diamond, coal, iron, gold


def _strips():
    """(hills, far trees, near trees, ground): each STRIP wide, wrapped as the world scrolls."""
    from PIL import Image, ImageDraw

    hills = Image.new("RGBA", (STRIP, LH), (0, 0, 0, 0))
    d = ImageDraw.Draw(hills)
    turn = 2 * math.pi / STRIP
    for x in range(0, STRIP, 4):
        h = 4 * int((58 + 26 * math.sin(turn * 3 * x) + 14 * math.sin(turn * 7 * x + 1.3)
                     + 6 * math.sin(turn * 17 * x)) / 4)
        d.rectangle([x, GROUND - h, x + 3, GROUND], fill=(78, 92, 138))
        d.rectangle([x, GROUND - h, x + 3, GROUND - h + 3], fill=(104, 118, 160))
        d.rectangle([x, GROUND - h // 2, x + 3, GROUND], fill=(66, 80, 124))
    far = Image.new("RGBA", (STRIP, LH), (0, 0, 0, 0))
    d = ImageDraw.Draw(far)
    for i, x in enumerate(range(20, STRIP, 48)):
        _tree(d, x + int(_noise(i, 1) * 20), GROUND, 62 + int(_noise(i, 2) * 24), (40, 92, 60), (58, 44, 36), 5)
    near = Image.new("RGBA", (STRIP, LH), (0, 0, 0, 0))
    d = ImageDraw.Draw(near)
    for i, x in enumerate(range(36, STRIP, 96)):
        _tree(d, x + int(_noise(i, 3) * 30), GROUND, 88 + int(_noise(i, 4) * 22), (58, 136, 62), (108, 80, 50), 9)
    ground = Image.new("RGBA", (STRIP, LH), (0, 0, 0, 0))
    px = ground.load()
    for x in range(STRIP):
        fringe = 5 + int(_noise(x // 2, 0, 23) * 4)  # the grass hangs over the dirt
        for y in range(GROUND, LH):
            depth = y - GROUND
            n = _noise(x // 2, y // 2, 11)
            if depth < fringe:
                c = _shade((96, 172, 68), (1.12 if depth < 2 else 0.92) * (0.86 + 0.26 * n))
            elif depth < 48:
                c = _shade((132, 94, 62), 0.84 + 0.3 * n)
                if _noise(x // 2, y // 2, 13) > 0.95:
                    c = _shade(c, 0.66)
            else:
                c = _shade((116, 118, 124), 0.8 + 0.3 * n)
                if _noise(x // 6, y // 2, 15) > 0.9:
                    c = _shade(c, 0.74)
                bx, by = x // 16, depth // 16
                kind = _noise(bx, by, 17)
                if kind > 0.88 and 2 <= x % 16 < 14 and 2 <= depth % 16 < 14 and _noise(x // 2, y // 2, 19) > 0.6:
                    c = ORES[int(_noise(bx, by, 21) * 3.99)]
            if depth >= fringe and (x % 16 == 0 or depth % 16 == 0):
                c = _shade(c, 0.88)  # the block grid
            px[x, y] = (*_shade(c, 1 - 0.42 * depth / (LH - GROUND)), 255)
    d = ImageDraw.Draw(ground)
    for x in range(0, STRIP, 5):
        n = _noise(x, 0, 31)
        if n < 0.42:  # grass blades
            for i in range(3):
                tall = 2 + int(_noise(x, i, 33) * 4)
                d.line([(x + i * 2, GROUND - tall), (x + i * 2, GROUND - 1)], fill=_shade((96, 172, 68), 0.8 + 0.12 * i))
        elif n > 0.93:  # a flower
            d.line([(x, GROUND - 5), (x, GROUND - 1)], fill=(60, 130, 50))
            d.rectangle([x - 1, GROUND - 8, x + 1, GROUND - 6], fill=((230, 60, 60), (250, 214, 74), (240, 240, 250))[x % 3])
            d.point((x, GROUND - 7), fill=(250, 240, 180))
    return hills, far, near, ground


def _wrap(strip, offset: float):
    from PIL import Image

    out = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
    x = -int(offset) % STRIP
    out.paste(strip, (x - STRIP, 0))
    out.paste(strip, (x, 0))
    return out


def _limb(d, x: float, y: float, angle: float, length: float, colour: tuple, tip: tuple, width: int = 8) -> tuple:
    """A blocky limb from the joint (x, y), its last pixels the hand or the shoe; angle from straight down,
    positive forward. Returns its end."""
    sx, cy = math.sin(angle), math.cos(angle)
    mid, end = (x + sx * (length - 5), y + cy * (length - 5)), (x + sx * length, y + cy * length)
    d.line([(x, y), mid], fill=colour, width=width)
    d.line([mid, end], fill=tip, width=width)
    ox, oy = -cy * (width / 2 - 1), sx * (width / 2 - 1)
    d.line([(x + ox, y + oy), (mid[0] + ox, mid[1] + oy)], fill=_shade(colour, 0.8), width=2)  # the back edge
    return end


FIGURE, FEET = 128, (64, 116)  # a figure's own picture, and where its feet stand in it
HERO_LOOK = {"skin": (228, 176, 134), "hair": (78, 46, 26), "top": (238, 122, 40), "legs": (52, 62, 128),
             "shoe": (40, 40, 48), "eye": (255, 255, 255), "pupil": (40, 30, 80), "hood": True}
ZOMBIE_LOOK = {"skin": (88, 154, 78), "hair": (58, 112, 54), "top": (32, 152, 152), "legs": (72, 62, 152),
               "shoe": (70, 70, 80), "eye": (14, 14, 14), "pupil": (14, 14, 14), "hood": False}


def _figure(look: dict, legs: float, back_arm: float, front_arm: float, flash: float):
    """A blocky figure facing right on its own picture. Returns (picture, the front hand in it)."""
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (FIGURE, FIGURE), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = {k: _mix(v, (255, 56, 56), flash) for k, v in look.items() if isinstance(v, tuple)}
    x, base = FEET
    hip, body, head = base - 24, base - 48, base - 64
    _limb(d, x, hip, -legs, 24, _shade(c["legs"], 0.74), _shade(c["shoe"], 0.8))
    _limb(d, x, body + 4, back_arm, 24, _shade(c["top"], 0.72), _shade(c["skin"], 0.78))
    if look["hood"]:
        d.rectangle([x - 7, body - 2, x - 4, body + 6], fill=_shade(c["top"], 0.8))
    d.rectangle([x - 4, body, x + 3, hip + 1], fill=c["top"])
    d.rectangle([x - 4, body, x - 3, hip + 1], fill=_shade(c["top"], 0.8))
    d.rectangle([x - 4, hip - 1, x + 3, hip + 1], fill=_shade(c["top"], 0.68))
    _limb(d, x, hip, legs, 24, c["legs"], c["shoe"])
    d.rectangle([x - 8, head, x + 7, head + 15], fill=c["skin"])
    d.rectangle([x - 8, head + 14, x + 7, head + 15], fill=_shade(c["skin"], 0.86))
    d.rectangle([x - 8, head, x + 7, head + 3], fill=c["hair"])
    d.rectangle([x - 8, head, x - 4, head + 10], fill=c["hair"])
    d.rectangle([x - 8, head, x + 7, head + 1], fill=_shade(c["hair"], 1.25))
    d.rectangle([x + 2, head + 7, x + 3, head + 8], fill=c["eye"])
    d.rectangle([x + 4, head + 7, x + 5, head + 8], fill=c["pupil"])
    d.rectangle([x + 4, head + 12, x + 7, head + 12], fill=_shade(c["skin"], 0.62))
    hand = _limb(d, x, body + 4, front_arm, 24, c["top"], c["skin"])
    return im, hand


def _outlined(im, colour: tuple = (14, 14, 24)):
    """A dark pixel line around a figure, so it reads against the night."""
    from PIL import Image, ImageFilter

    out = Image.new("RGBA", im.size, (*colour, 0))
    out.putalpha(im.getchannel("A").filter(ImageFilter.MaxFilter(3)))
    out.alpha_composite(im)
    return out


def _zombie(step: float, flash: float, fall: float):
    """The zombie, arms out, facing left; it tips onto its back as it falls."""
    from PIL import Image, ImageOps

    im, _ = _figure(ZOMBIE_LOOK, step, 1.38 + step * 0.2, 1.52 - step * 0.2, flash)
    im = ImageOps.mirror(im)
    if fall > 0:
        im = im.rotate(-90 * _ease(fall), center=(FIGURE - 1 - FEET[0], FEET[1] - 4), resample=Image.NEAREST)
    return _outlined(im)


def _vignette():
    """Soft dark corners over the whole frame."""
    from PIL import Image

    small = Image.new("L", (LW // 4, LH // 4))
    px = small.load()
    for x in range(small.width):
        for y in range(small.height):
            r = math.hypot((x / small.width - 0.5) * 1.5, y / small.height - 0.5) * 2
            px[x, y] = int(120 * max(0.0, min(1.0, (r - 0.62) / 0.6)) ** 1.5)
    dark = Image.new("RGBA", (W, H), (0, 0, 8, 0))
    dark.putalpha(small.resize((W, H), Image.BILINEAR))
    return dark


def _since(t: float, times: list[float], span: float) -> float:
    """1 right at the latest of `times`, fading to 0 over `span` seconds."""
    past = [t - x for x in times if 0 <= t - x < span]
    return 1 - min(past) / span if past else 0.0




# --------------------------------------------------------------------------- more world pieces
def _blank(size: tuple = (STRIP, LH)):
    from PIL import Image

    return Image.new("RGBA", size, (0, 0, 0, 0))


def _fill(im, y0: int, y1: int, colour: tuple, salt: int, brick: bool = False, specks: tuple = (),
          dark: float = 0.42, lit: bool = True) -> None:
    """Blocks (or bricks) from y0 down to y1 across a strip, darker with depth, ores as `specks`."""
    px = im.load()
    for x in range(im.width):
        for y in range(y0, y1):
            depth = y - y0
            c = _shade(colour, 0.82 + 0.3 * _noise(x // 2, y // 2, salt))
            bx, by = x // 16, depth // 16
            if specks and _noise(bx, by, salt + 1) > 0.87 and 2 <= x % 16 < 14 and 2 <= depth % 16 < 14 \
                    and _noise(x // 2, y // 2, salt + 2) > 0.6:
                c = specks[int(_noise(bx, by, salt + 3) * len(specks) * 0.999)]
            if brick:
                edge = (x + 8 * ((depth // 8) % 2)) % 16 == 0 or depth % 8 == 0
            else:
                edge = x % 16 == 0 or depth % 16 == 0
            if edge:
                c = _shade(c, 0.72 if brick else 0.86)
            if lit and depth < 2:
                c = _shade(c, 1.22)
            px[x, y] = (*_shade(c, 1 - dark * depth / max(1, y1 - y0)), 255)


def _tinted(im, tint: tuple):
    from PIL import Image

    r, g, b, a = im.split()
    return Image.merge("RGBA", (r.point(lambda v: v * tint[0] // 255), g.point(lambda v: v * tint[1] // 255),
                                b.point(lambda v: v * tint[2] // 255), a))


def _sky(top: tuple, low: tuple):
    from PIL import Image, ImageDraw

    sky = Image.new("RGBA", (LW, LH), (0, 0, 0, 255))
    d = ImageDraw.Draw(sky)
    for y in range(GROUND + 1):
        d.line([(0, y), (LW, y)], fill=_mix(top, low, (y / GROUND) ** 1.6))
    return sky


def _house(d, x: int, salt: int) -> None:
    """A village house standing on the grass line: planks, log corners, a stepped roof, a door, a window."""
    wide, tall = 56, 40
    top = GROUND - tall
    for px in range(x, x + wide):
        for py in range(top, GROUND):
            n = _noise(px // 3, py // 4, salt)
            c = _shade((182, 140, 88), 0.86 + 0.24 * n)
            if (py - top) % 8 == 0:
                c = _shade(c, 0.74)
            if px - x < 4 or x + wide - px <= 4:
                c = _shade((104, 78, 48), 0.86 + 0.24 * n)
            d.point((px, py), fill=c)
    for i in range(7):  # the roof, a step at a time
        d.rectangle([x - 6 + i * 5, top - 4 - i * 4, x + wide + 5 - i * 5, top - i * 4], fill=_shade((150, 66, 52), 1 - 0.05 * i))
        d.rectangle([x - 6 + i * 5, top - 4 - i * 4, x + wide + 5 - i * 5, top - 3 - i * 4], fill=(186, 92, 70))
    d.rectangle([x + 10, GROUND - 24, x + 21, GROUND - 1], fill=(92, 62, 36))
    d.rectangle([x + 12, GROUND - 22, x + 19, GROUND - 14], fill=(120, 84, 50))
    d.point((x + 19, GROUND - 11), fill=(230, 220, 160))
    d.rectangle([x + 32, GROUND - 28, x + 45, GROUND - 15], fill=(74, 52, 32))
    d.rectangle([x + 34, GROUND - 26, x + 43, GROUND - 17], fill=(150, 210, 240))
    d.line([(x + 38, GROUND - 26), (x + 38, GROUND - 17)], fill=(74, 52, 32))


def _tile(kind: str, salt: int = 41):
    """A 16x16 block seen from the side: stone, cobble, magma or lava."""
    from PIL import Image

    im = Image.new("RGBA", (16, 16), (0, 0, 0, 255))
    px = im.load()
    for x in range(16):
        for y in range(16):
            n, m = _noise(x // 2, y // 2, salt), _noise(x // 3, y // 3, salt + 5)
            if kind == "stone":
                c = _shade((126, 128, 134), 0.84 + 0.3 * n)
            elif kind == "cobble":
                c = _shade((122, 122, 126), 0.62 if m < 0.3 else 0.86 + 0.34 * n)
            elif kind == "magma":
                c = (255, 150, 36) if abs(_noise(x // 2 + y // 4, y // 2, salt + 7) - 0.5) < 0.13 else _shade((78, 26, 22), 0.8 + 0.4 * n)
            else:
                c = _mix((236, 92, 16), (255, 206, 70), n)
            if kind != "lava" and (x in (0, 15) or y in (0, 15)):
                c = _shade(c, 0.7)
            px[x, y] = (*c, 255)
    return im


SKELETON_LOOK = {"skin": (212, 212, 212), "hair": (212, 212, 212), "top": (190, 190, 194), "legs": (172, 172, 178),
                 "shoe": (150, 150, 156), "eye": (30, 30, 34), "pupil": (30, 30, 34), "hood": False}
VILLAGER_LOOK = {"skin": (196, 150, 118), "hair": (196, 150, 118), "top": (112, 74, 46), "legs": (92, 60, 38),
                 "shoe": (60, 44, 30), "eye": (255, 255, 255), "pupil": (40, 110, 60), "hood": False}


def _dragon(t: float, flash: float):
    """The Ender Dragon facing left on its own picture, the body's middle at (64, 56); wings beat with t."""
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (128, 100), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    hot = (255, 255, 255)
    body, light, bone = _mix((46, 38, 64), hot, flash), _mix((86, 74, 112), hot, flash), _mix((120, 116, 130), hot, flash)
    cx, cy = 64, 56
    f = math.sin(t * 7)
    d.polygon([(cx - 6, cy - 5), (cx + 12, cy - 5), (cx + 30, cy - 9 - 26 * f), (cx + 6, cy - 12 - 34 * f)],
              fill=_shade(light, 0.7))  # the far wing
    for i, (x0, x1, y0, y1) in enumerate(((16, 30, -3, 3), (30, 42, -1, 3), (42, 52, 1, 3))):  # the tail
        d.rectangle([cx + x0, cy + y0, cx + x1, cy + y1], fill=body)
        d.rectangle([cx + x0 + 3, cy + y0 - 2, cx + x0 + 5, cy + y0 - 1], fill=bone)
    d.rectangle([cx - 14, cy - 6, cx + 16, cy + 6], fill=body)
    d.rectangle([cx - 14, cy + 3, cx + 16, cy + 6], fill=light)
    for x in range(-10, 16, 6):
        d.rectangle([cx + x, cy - 8, cx + x + 2, cy - 7], fill=bone)
    d.rectangle([cx - 8, cy + 6, cx - 4, cy + 12], fill=body)
    d.rectangle([cx + 8, cy + 6, cx + 12, cy + 12], fill=body)
    d.rectangle([cx - 24, cy - 9, cx - 14, cy], fill=body)  # the neck
    d.rectangle([cx - 38, cy - 14, cx - 22, cy - 4], fill=body)  # the head
    d.rectangle([cx - 46, cy - 10, cx - 38, cy - 4], fill=body)
    d.rectangle([cx - 46, cy - 5, cx - 30, cy - 4], fill=light)
    d.rectangle([cx - 33, cy - 12, cx - 30, cy - 10], fill=_mix((228, 90, 255), hot, flash))
    d.rectangle([cx - 26, cy - 18, cx - 24, cy - 14], fill=bone)
    d.rectangle([cx - 31, cy - 17, cx - 29, cy - 14], fill=bone)
    wing = [(cx - 10, cy - 5), (cx + 10, cy - 5), (cx + 26, cy - 7 - 30 * f), (cx - 2, cy - 10 - 38 * f)]
    d.polygon(wing, fill=light)
    d.line([wing[0], wing[3], wing[2]], fill=body, width=2)
    d.line([wing[3], (cx + 10, cy - 5)], fill=body, width=1)
    return _outlined(im, (150, 84, 210))


# --------------------------------------------------------------------------- what a pause can land on
class Option(NamedTuple):
    name: str
    tier: str  # "" for the stops with no tiers
    sub: str  # the card's second line
    delta: int  # hearts won or lost
    weight: float  # its share of the frames
    icon: str = ""  # the picture's name
    turn: float = 0.0  # the picture turned, in degrees


def _by_tier(tiers: list[str]) -> list[float]:
    """Each tier its share of the frames (spin.TIERS), split between the tier's items."""
    from . import spin

    per = Counter(tiers)
    return [spin.TIERS[t][1] / per[t] for t in tiers]


def _deck(options: list[Option], count: int, rng: random.Random) -> list[Option]:
    out: list[Option] = []
    weights = [o.weight for o in options]
    for _ in range(count):
        while True:
            pick = rng.choices(options, weights)[0]
            if not out or pick != out[-1]:
                break
        out.append(pick)
    return out


def _letters(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _cues(lines: list[tuple[str, str]], words: list[Word]) -> dict[str, list[Word]]:
    """The spoken words of every scripted sentence, by its role: the sentences are matched by their letters."""
    out, i = {}, 0
    for role, text in lines:
        need, got, start = len(_letters(text)), 0, i
        while i < len(words) and got < need:
            got += len(_letters(words[i].text))
            i += 1
        if got != need:
            raise ValueError(f"The voice's words do not match the script at: {text}")
        out[role] = words[start:i]
    return out


# --------------------------------------------------------------------------- sharp layer: HUD, cards, captions
HEART = ("0110110", "1111111", "1111111", "0111110", "0011100", "0001000")
ROAD = ("FOREST", "CAVE", "VILLAGE", "NETHER", "FORTRESS", "STRONGHOLD", "THE END")


def _heart(d, x: int, y: int, cell: int, colour: tuple, shine: bool = True) -> None:
    for r, row in enumerate(HEART):
        for c, on in enumerate(row):
            if on == "1":
                d.rectangle([x + c * cell - 4, y + r * cell - 4, x + (c + 1) * cell + 3, y + (r + 1) * cell + 3],
                            fill=(12, 12, 16))
    for r, row in enumerate(HEART):
        for c, on in enumerate(row):
            if on == "1":
                d.rectangle([x + c * cell, y + r * cell, x + (c + 1) * cell - 1, y + (r + 1) * cell - 1], fill=colour)
    if shine:
        d.rectangle([x + cell, y + cell, x + 2 * cell - 1, y + 2 * cell - 1], fill=_mix(colour, (255, 255, 255), 0.6))


def _label(d, xy: tuple, text: str, font, fill=(255, 255, 255), stroke: int = 8) -> None:
    d.text(xy, text, font=font, fill=fill, anchor="mm", stroke_width=stroke, stroke_fill=(10, 10, 14))


def _caption_groups(words: list[Word], size: int = 3) -> list[list[Word]]:
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == size or w.text[-1:] in ".?!:,":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def _verdict(delta: int, zero: tuple) -> tuple[str, tuple]:
    if delta > 0:
        return f"+{delta} HEART{'S' if delta > 1 else ''}", GREEN
    if delta == 0:
        return zero
    return f"YOU LOSE {-delta} HEART{'S' if delta < -1 else ''}", ORANGE if delta == -1 else RED


class Art:
    """Fonts, pictures and cached cards."""

    def __init__(self, settings: Settings):
        from . import dungeon

        self.fonts = settings.assets_dir / "fonts"
        self._fonts: dict = {}
        self.title = self.font("Anton-Regular", 150)
        self.big = self.font("Poppins-Black", 92)
        self.caption = self.font("Poppins-Black", 78)
        self.name = self.font("Poppins-Black", 62)
        self.bar = self.font("Poppins-Black", 54)
        self.head = self.font("Poppins-ExtraBold", 40)
        self.small = self.font("Poppins-Bold", 36)
        self.tiny = self.font("Poppins-Bold", 26)
        self.pics = dungeon.pictures(settings)
        self.extra = {f"tile:{k}": _tile(k) for k in ("stone", "cobble", "magma", "lava")}
        self.forest = _strips()
        self.vignette = _vignette()
        self._icons: dict = {}
        self._cards: dict = {}

    def font(self, name: str, size: int):
        from PIL import ImageFont

        if (name, size) not in self._fonts:
            self._fonts[name, size] = ImageFont.truetype(str(self.fonts / f"{name}.ttf"), size)
        return self._fonts[name, size]

    def fit(self, d, text: str, width: int, name: str = "Anton-Regular", size: int = 150):
        while size > 40 and d.textlength(text, font=self.font(name, size)) > width:
            size -= 6
        return self.font(name, size)

    def icon(self, name: str, size: int, turn: float = 0.0):
        from PIL import Image

        key = (name, size, turn)
        if key not in self._icons:
            if name in self.extra:
                im = self.extra[name]
            elif name in self.pics:
                im = Image.open(self.pics[name]["path"]).convert("RGBA")
            else:
                raise ValueError(f"No picture of {name}: run the pause Shorts' picture fetch first")
            im = im.resize((size, size), Image.NEAREST)
            self._icons[key] = im.rotate(turn, resample=Image.NEAREST) if turn else im
        return self._icons[key]

    def shell(self, colour: tuple, head: str, bar: str, bar_colour: tuple):
        """A card's frame: the header band on top, the verdict bar at the bottom."""
        from PIL import Image, ImageDraw

        im = Image.new("RGBA", (960, 410), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 959, 409], fill=colour)
        d.rectangle([10, 10, 949, 399], fill=(22, 24, 32))
        d.rectangle([10, 10, 949, 82], fill=colour)
        d.text((480, 47), head, font=self.head, fill=(12, 12, 16), anchor="mm")
        if bar:
            d.rectangle([10, 318, 949, 399], fill=bar_colour)
            d.text((480, 357), bar, font=self.bar, fill=(255, 255, 255), anchor="mm")
        return im, d

    def card(self, stop, option: Option, mine: bool):
        from . import spin

        key = (stop.N, option.name, mine)
        if key in self._cards:
            return self._cards[key]
        bar, bar_colour = _verdict(option.delta, stop.ZERO)
        colour = spin.TIERS[option.tier][0] if option.tier else bar_colour
        head = f"{'MY' if mine else 'YOUR'} {stop.KIND}" + (f": {option.tier.upper()}" if option.tier else "")
        im, d = self.shell(colour, head, bar, bar_colour)
        d.rectangle([40, 100, 252, 312], fill=(128, 132, 146))
        im.alpha_composite(self.icon(option.icon, 208, option.turn), (42, 102))
        parts = option.name.upper().split()
        whole = " ".join(parts)
        lines = [whole] if d.textlength(whole, font=self.name) <= 640 or len(parts) < 2 else \
            [" ".join(parts[:len(parts) // 2]), " ".join(parts[len(parts) // 2:])]
        y = 178 - 36 * (len(lines) - 1)
        for line in lines:
            d.text((610, y), line, font=self.fit(d, line, 650, "Poppins-Black", 62), fill=(255, 255, 255), anchor="mm")
            y += 72
        d.text((610, y + 6), option.sub, font=self.small, fill=(170, 176, 190), anchor="mm")
        self._cards[key] = im
        return im

    def rules(self, tag: str):
        """A stop alone: what the viewer starts with, where the road card stands in the long video."""
        key = ("rules", tag)
        if key not in self._cards:
            im, d = self.shell((255, 196, 0), tag, "COMMENT YOUR HEARTS", GREY)
            d.text((480, 150), "YOU START WITH", font=self.bar, fill=(255, 255, 255), anchor="mm")
            d.text((480, 248), "10 HEARTS", font=self.big, fill=(255, 222, 0), anchor="mm")
            self._cards[key] = im
        return self._cards[key]

    def road(self, here: int):
        """The seven stops in a row: the ones passed ticked green, this one gold."""
        key = ("road", here)
        if key in self._cards:
            return self._cards[key]
        im, d = self.shell((255, 196, 0), "THE ROAD TO THE ENDER DRAGON", "", GREY)
        d.rectangle([90, 196, 870, 206], fill=(70, 74, 90))
        d.rectangle([90, 196, 90 + 130 * min(6, here - 1), 206], fill=GREEN)
        for i, name in enumerate(ROAD):
            x, n = 90 + 130 * i, i + 1
            colour = GREEN if n < here else (255, 196, 0) if n == here else (70, 74, 90)
            size = 40 if n == here else 30
            d.rectangle([x - size - 5, 201 - size - 5, x + size + 5, 201 + size + 5], fill=(12, 12, 16))
            d.rectangle([x - size, 201 - size, x + size, 201 + size], fill=colour)
            d.text((x, 199), str(n), font=self.name if n == here else self.head,
                   fill=(12, 12, 16) if n <= here else (170, 176, 190), anchor="mm")
            d.text((x, 290 if i % 2 else 112), name, font=self.tiny,
                   fill=(255, 255, 255) if n == here else (170, 176, 190), anchor="mm")
        d.text((480, 358), f"STOP {here} OF 7" if here <= 7 else "YOU MADE IT!", font=self.bar, fill=(255, 222, 0), anchor="mm")
        self._cards[key] = im
        return im


# --------------------------------------------------------------------------- a stop: the shared flow
def _swing(t: float, times: list[float]) -> tuple[float, float]:
    """The arm and the held thing's turn around a blow: wind up, then down through it."""
    phase = min([x - t for x in times if x - t > -0.18] or [9])
    if phase < 0.2:
        k = 1 - max(0.0, phase) / 0.2 if phase > 0 else 1 + phase / 0.18
        return (2.2 - 1.2 * k, 40 - 110 * k) if phase > 0 else (1.0 + 0.15 * (1 - k), -70 * k)
    return 1.15, 0.0


class Stop:
    """Walk in, the threat shows, pause (the options flick), the narrator's own stop, what it does, walk on.

    Times are the stop's own (0 at its first word). A subclass gives the words (`lines`), the options, the
    backdrop and the scene; `plan` fills `hits` and `hurts` inside the window between `act` and `go`."""

    N, NAME, NEXT, COLOUR, KIND, MINE = 0, "", "", (255, 255, 255), "WEAPON", ""
    COUNT = "COUNT YOUR HEARTS"
    ZERO = ("NO HEARTS LOST", GREEN)
    L = 0.0  # how far the hero steps ahead of his usual place at this stop
    STAYS = False  # the world does not move on at the end
    # the stop as a Short of its own (SOLO empty: it has none): its title, its first words, the dare on screen
    SOLO, HOOK, DARE, TAG, SLUG = "", "", "", "PAUSE CHALLENGE", ""
    ALONE: dict = {}  # the sentences that change when the stop is alone, by role
    solo = False  # drawn alone: no road, no next stop

    @classmethod
    def options(cls) -> list[Option]:
        return []

    @classmethod
    def mine(cls) -> Option:
        return next(o for o in cls.options() if o.name == cls.MINE)

    @classmethod
    def lines(cls, before: int) -> list[tuple[str, str]]:
        raise NotImplementedError

    @classmethod
    def after(cls) -> int:
        """The narrator's hearts when this stop is the whole video."""
        return max(0, min(HEARTS, HEARTS + cls.mine().delta))

    @classmethod
    def alone(cls) -> list[tuple[str, str]]:
        """The stop's words as a Short of its own: a dare first, and the comment asked for at the end."""
        swap = {"title": cls.HOOK, "next": "Comment your hearts.", **cls.ALONE}
        return [(role, swap.get(role, text)) for role, text in cls.lines(HEARTS) if role != "intro"]

    def __init__(self, art: Art, lines: list[tuple[str, str]], words: list[Word], seed: str = "quest"):
        self.art, self.words = art, words
        self.sent = _cues(lines, words)
        cue = lambda role: self.sent[role][0].start  # noqa: E731
        self.title, self.threat, self.pause = cue("title"), cue("threat"), cue("pause")
        self.mine_at, self.next = cue("mine"), cue("next")
        self.count = cue("count") if "count" in self.sent else None
        self.total = words[-1].end + GAP
        self.halt = self.threat + 0.6
        self.halt_scroll = WALK * self.halt
        self.act, self.go = self.mine_at + 0.9, self.next
        self.window = max(1.5, self.go - 0.3 - self.act)
        self.rng = random.Random(f"{seed}{self.N}")
        self.all = self.options()
        self.pick = self.mine()
        self.deck = _deck(self.all, int((self.mine_at - self.pause) * FPS / HOLD) + 2, self.rng)
        self.hits: list[float] = []
        self.hurts: list[tuple[float, int]] = []
        self.quakes: list[float] = []
        self.sounds: list[tuple[float, str]] = [(self.mine_at, "blip")]
        self.groups = _caption_groups(words)
        self.build()
        self.plan()
        self.sounds += [(x, "hit") for x in self.hits] + [(x, "boom") for x in self.quakes]
        self.sounds += [(x, "hurt" if dl < 0 else "heal") for x, dl in self.hurts]

    # ---- for a subclass
    def build(self) -> None:
        pass

    def plan(self) -> None:
        pass

    def backdrop(self, t: float, scroll: float):
        raise NotImplementedError

    def scene(self, c) -> None:
        pass

    def sharp(self, c, img, d) -> None:
        pass

    def pose(self, t: float) -> tuple:
        """(the front arm's angle or None, the held thing's turn, its picture or None, its size)."""
        return None, 0.0, None, 192

    def advance(self, t: float) -> float:
        return self.L * _ease((t - self.halt) / 0.6)

    def jump(self, t: float) -> float:
        return 0.0

    def hidden(self, t: float) -> bool:
        return False

    # ---- the flow
    def option(self, t: float) -> Option | None:
        if t < self.pause or t >= self.go:
            return None
        if t < self.mine_at:
            return self.deck[int((t - self.pause) * FPS / HOLD) % len(self.deck)]
        return self.pick

    def scroll(self, t: float) -> float:
        if self.STAYS:
            return WALK * min(t, self.halt)
        return WALK * (min(t, self.halt) + max(0.0, t - self.go)) + self.L * _ease((t - self.go) / 0.8)

    def lead(self, t: float) -> float:
        if t < self.go or self.STAYS:
            return self.advance(t)
        return self.L * (1 - _ease((t - self.go) / 0.8))

    def walking(self, t: float) -> bool:
        stepping = self.L and self.halt <= t < self.halt + 0.6
        return t < self.halt or stepping or (t >= self.go and not self.STAYS)

    def card(self, t: float):
        option = self.option(t)
        if option is None and self.solo and t >= self.mine_at:
            return self.art.card(self, self.pick, True), 0.0  # alone, the narrator's card stays to the end
        if option is None:
            return self.before(), 0.0
        return self.art.card(self, option, t >= self.mine_at), _since(t, [self.mine_at], 0.2)

    def before(self):
        """The card until the options flick: the road, or the rules when the stop is alone."""
        return self.art.rules(self.TAG) if self.solo else self.art.road(self.N)

    def sky(self, t: float, d) -> None:
        art = self.art
        if self.solo and t >= self.next:
            _label(d, (W // 2, 420), "COMMENT", art.title, (255, 255, 255), 12)
            _label(d, (W // 2, 590), "YOUR HEARTS", art.title, (255, 222, 0), 12)
        elif self.solo and self.title <= t < self.threat + 0.3:
            _label(d, (W // 2, 420), "CAN YOU", art.title, (255, 255, 255), 12)
            _label(d, (W // 2, 590), self.DARE, art.fit(d, self.DARE, 960), self.COLOUR, 12)
        elif self.title <= t < self.threat + 0.3:
            _label(d, (W // 2, 420), f"STOP {self.N}", art.title, (255, 255, 255), 12)
            _label(d, (W // 2, 590), self.NAME, art.fit(d, self.NAME, 960), self.COLOUR, 12)
        elif self.pause <= t < self.mine_at:
            self.pause_words(t, d)
        elif self.count is not None and self.count <= t < self.next:
            _label(d, (W // 2, 440), self.COUNT, art.fit(d, self.COUNT, 980, "Poppins-Black", 92), (255, 222, 0), 10)
        elif t >= self.next and self.NEXT:
            _label(d, (W // 2, 400), "NEXT STOP", art.big, (255, 255, 255), 10)
            _label(d, (W // 2, 540), self.NEXT, art.fit(d, self.NEXT, 960), (255, 222, 0), 12)

    def pause_words(self, t: float, d) -> None:
        pulse = int(8 * abs(math.sin((t - self.pause) * 6)))
        d.rectangle([150 - pulse, 340 - pulse, W - 150 + pulse, 520 + pulse], fill=(255, 222, 0, 255),
                    outline=(10, 10, 14, 255), width=10)
        d.text((W // 2, 432), "PAUSE NOW!", font=self.art.title, fill=(10, 10, 14), anchor="mm")

    def frame(self, t: float):
        """The world, the figures and the scene's own labels; the HUD and the cards come on top (Quest)."""
        from PIL import Image, ImageDraw

        scroll = self.scroll(t)
        hit = _since(t, self.hits, 0.16)
        bite = _since(t, [x for x, dl in self.hurts if dl < 0], 0.22)
        world = self.backdrop(t, scroll)
        under, over = _blank((LW, LH)), _blank((LW, LH))
        walking = self.walking(t)
        c = SimpleNamespace(t=t, scroll=scroll, hit=hit, bite=bite, world=world, du=ImageDraw.Draw(under),
                            d=ImageDraw.Draw(over), sprites=[], shift=self.halt_scroll - scroll,
                            hx=HERO_X + self.lead(t) - 4 * bite, option=self.option(t))
        self.scene(c)
        hx, d = c.hx, c.d
        swing = 0.55 * math.sin(t * 9) if walking else 0.0
        arm, turn, item, size = self.pose(t)
        figure, hand = _figure(HERO_LOOK, swing, swing * 0.9, -swing * 0.9 if arm is None else arm, bite * 0.8)
        up = self.jump(t)
        bob = 1 if walking and abs(math.sin(t * 9)) > 0.75 else 0
        corner = (int(hx) - FEET[0], GROUND - FEET[1] - bob - int(up))
        c.hand = hand = (corner[0] + hand[0], corner[1] + hand[1])
        hidden = self.hidden(t)
        if not hidden:
            c.du.ellipse([hx - 12, GROUND - 2, hx + 12, GROUND + 3], fill=(0, 0, 0, 90))
        world.alpha_composite(under)
        for sprite, pos in c.sprites:
            world.paste(sprite, pos, sprite)
        if not hidden:
            figure = _outlined(figure)
            world.paste(figure, corner, figure)
            if walking and not up:  # dust at the heel, a puff a step
                age = (t * 9 / math.pi) % 1.0
                if age < 0.7:
                    for i in range(3):
                        x, y = hx - 8 - age * (10 + 6 * i), GROUND - 2 - age * (3 + 3 * i)
                        d.rectangle([x, y, x + 1, y + 1], fill=(214, 204, 186, int(200 * (1 - age / 0.7))))
        if self.threat <= t < self.threat + 1.1 and int(t * 6) % 2 == 0:  # "!"
            for box, colour in (((-3, 95, 2, 79), (14, 14, 24)), ((-3, 77, 2, 72), (14, 14, 24)),
                                ((-2, 94, 1, 80), (255, 222, 0)), ((-2, 76, 1, 73), (255, 222, 0))):
                d.rectangle([hx + box[0], GROUND - box[1], hx + box[2], GROUND - box[3]], fill=(*colour, 255))
        world.alpha_composite(over)
        most = max(hit, bite, _since(t, self.quakes, 0.3))
        shake = (int(round(math.sin(t * 90) * 2.4 * most)), int(round(math.cos(t * 70) * 2 * most)))
        img = world.convert("RGB").resize((W, H), Image.NEAREST)
        if shake != (0, 0):  # over itself, so a shaken frame has no black edge
            img.paste(img.copy(), (shake[0] * S, shake[1] * S))
        img = img.convert("RGBA")
        img.alpha_composite(self.art.vignette)
        d = ImageDraw.Draw(img)
        c.shake = (shake[0] * S, shake[1] * S)
        if item and not hidden:
            held = _blank((size * 2, size * 2))
            held.alpha_composite(self.art.icon(item, size), (size - int(size * 0.16), size - int(size * 0.84)))
            held = held.rotate(turn, resample=Image.NEAREST)
            img.alpha_composite(held, (max(0, int(hand[0] * S) - size + c.shake[0]),
                                       max(0, int(hand[1] * S) - size + c.shake[1])))
        self.sharp(c, img, d)
        for x, dl in self.hurts:  # the hearts won or lost float up from the hero
            if 0 <= t - x < 0.9:
                k = (t - x) / 0.9
                cx, cy = int(hx * S), (GROUND - 76) * S - int(130 * k)
                _label(d, (cx - 34, cy), f"{dl:+d}", self.art.name, (255, 80, 80) if dl < 0 else (110, 240, 120))
                _heart(d, cx + 36, cy - 30, 8, (226, 30, 40))
        return img


# --------------------------------------------------------------------------- stops 1 and 7: a weapon against a mob
class Fight(Stop):
    """Pause for a weapon; the faster it kills the foe, the fewer hearts lost."""

    FOE, EDGES, SHOWN = "Zombie", (3, 5), 8  # hits up to EDGES[0] cost 1 heart, up to EDGES[1] 2, more 3

    @classmethod
    def lost(cls, hits: int) -> int:
        return 1 if hits <= cls.EDGES[0] else 2 if hits <= cls.EDGES[1] else 3

    @classmethod
    def options(cls) -> list[Option]:
        from . import spin

        names = list(spin.WEAPONS)
        weights = _by_tier([spin.WEAPONS[n][0] for n in names])
        out = []
        for name, weight in zip(names, weights):
            hits = spin.hits_to_kill(cls.FOE, name) or 999
            out.append(Option(name, spin.WEAPONS[name][0], f"{hits} {'SHOTS' if name == 'Bow' else 'HITS'} TO WIN",
                              -cls.lost(hits), weight, name))
        return out

    @classmethod
    def mine_hits(cls) -> int:
        from . import spin

        return spin.hits_to_kill(cls.FOE, cls.MINE)

    def plan(self) -> None:
        self.real = self.mine_hits()
        shown = min(self.real, self.SHOWN)
        room = (self.count if self.count is not None else self.go) - 0.3 - self.act
        span = min(max(1.5, room), 0.5 * shown + 0.5)
        self.hits = [self.act + span * (i + 0.6) / shown for i in range(shown)]
        self.dead = self.hits[-1]
        lost = -self.pick.delta
        self.hurts = [(self.act + span * (j + 0.9) / (lost + 1), -1) for j in range(lost)]
        self.sounds.append((self.dead, "boom"))
        self.puff = [(self.rng.uniform(-1, 1), self.rng.uniform(-1.4, 0.2), self.rng.random()) for _ in range(30)]

    def pose(self, t: float) -> tuple:
        option = self.option(t)
        if option is None:
            return None, 0.0, None, 192
        arm, turn = _swing(t, self.hits) if t >= self.act else (1.15, 0.0)
        return arm, turn, option.icon, 192

    def blows(self, t: float, d, x: int, y: int) -> None:
        """HIT n! floats up from the foe: the count runs to the real number of hits."""
        for i, at in enumerate(self.hits):
            if 0 <= t - at < 0.6:
                n = round(self.real * (i + 1) / len(self.hits))
                _label(d, (x, y - int(110 * (t - at) / 0.6)), f"HIT {n}!", self.art.name, (255, 232, 60))

    def health(self, t: float) -> float:
        return 1 - sum(t >= x for x in self.hits) / len(self.hits)


class Forest(Fight):
    N, NAME, NEXT, COLOUR, MINE = 1, "THE FOREST", "THE CAVE", (120, 230, 110), "Stone Sword"
    SOLO, HOOK = "Pause To Survive Your First Night ⚔️", "Ten hearts. Can you survive your first night?"
    DARE, SLUG = "SURVIVE THE NIGHT?", "forest"
    ALONE = {"count": "How many hearts do you have left?"}

    @classmethod
    def lines(cls, before: int) -> list[tuple[str, str]]:
        hits, lost = cls.mine_hits(), -cls.mine().delta
        return [("intro", "Can you beat Minecraft? You start with ten hearts, and nothing else."),
                ("title", "Stop one: the forest."),
                ("threat", "Night falls, and a zombie is coming."),
                ("pause", "Pause the video right now! The weapon you stop on is yours. "
                          "A stronger weapon means fewer hearts lost."),
                ("mine", f"I stopped on a {cls.MINE.lower()}. {_say(hits).capitalize()} hits, and I lose {_hearts(lost)}."),
                ("count", "Count your hearts on your fingers."),
                ("next", "Next stop: the cave.")]

    def build(self) -> None:
        rng = self.rng
        self.hills, self.far, self.near, self.ground = self.art.forest
        self.stars = [(rng.randrange(LW), rng.randrange(GROUND - 30), rng.random()) for _ in range(90)]
        self.clouds = [(rng.randrange(LW + 80), rng.randrange(50, 110), rng.randrange(26, 44), rng.uniform(1.5, 4))
                       for _ in range(4)]
        self.flies = [(rng.randrange(LW), rng.randrange(GROUND - 60, GROUND - 8), rng.random()) for _ in range(9)]

    def night(self, t: float) -> float:
        return _ease((t - 1.0) / max(0.5, self.threat - 1.0))

    def backdrop(self, t: float, scroll: float):
        from PIL import ImageDraw

        night = self.night(t)
        sky = _sky(_mix((70, 90, 170), (6, 8, 26), night), _mix((246, 150, 86), (26, 32, 74), night))
        lights = _blank((LW, LH))
        d = ImageDraw.Draw(lights)
        for sx, sy, tw in self.stars:
            glow = night * (0.5 + 0.5 * math.sin(t * 3 + tw * 20))
            if glow > 0.12:
                big = int(tw > 0.82)
                d.rectangle([sx, sy, sx + big, sy + big], fill=(255, 255, 235, int(255 * glow)))

        def orb(x: int, y: int, size: int, colour: tuple, halo: tuple) -> None:
            for grow, alpha in ((14, 16), (8, 30), (3, 52)):
                d.rectangle([x - grow, y - grow, x + size + grow, y + size + grow], fill=(*halo, alpha))
            d.rectangle([x, y, x + size, y + size], fill=(*colour, 255))

        orb(30, int(92 + 120 * night), 21, (255, 222, 150), (255, 190, 110))
        moon = int(214 - 150 * night)
        orb(126, moon, 19, (236, 240, 222), (190, 200, 255))
        for cx, cy in ((4, 4), (11, 9), (5, 13)):
            d.rectangle([126 + cx, moon + cy, 129 + cx, moon + cy + 2], fill=(204, 210, 196, 255))
        cloud = (*_mix((255, 196, 170), (58, 66, 108), night), 215)
        for cx, cy, cw, speed in self.clouds:
            x = int((cx - t * speed - scroll * 0.06) % (LW + 80)) - 60
            d.rectangle([x, cy + 4, x + cw, cy + 9], fill=cloud)
            d.rectangle([x + 6, cy, x + cw - 10, cy + 4], fill=cloud)
            d.rectangle([x + 14, cy - 3, x + cw // 2, cy], fill=cloud)
        sky.alpha_composite(lights)
        land = _blank((LW, LH))
        for strip, speed in ((self.hills, 0.12), (self.far, 0.45), (self.near, 1.0), (self.ground, 1.0)):
            land.alpha_composite(_wrap(strip, scroll * speed))
        sky.alpha_composite(_tinted(land, _mix((255, 232, 208), (96, 112, 176), night)))
        return sky

    def scene(self, c) -> None:
        t, d = c.t, c.d
        c.zx = None
        if t >= self.act:
            c.hx += 4 * c.hit
        if self.threat <= t < self.dead + 0.45:
            if t < self.pause:
                zx = 208 - 84 * _ease((t - self.threat) / (self.pause - self.threat))
            elif t < self.act:
                zx = 124 - 12 * (t - self.pause) / (self.act - self.pause)
            else:
                zx = 112 + 6 * c.hit - 12 * math.sin(math.pi * c.bite)
            fall = (t - self.dead) / 0.3 if t >= self.dead else 0.0
            step = 0.35 * math.sin(t * 7) if t < self.act else 0.12 * math.sin(t * 9)
            zombie = _zombie(step, max(c.hit, 1.0 if t >= self.dead else 0.0) * 0.75, fall)
            c.du.ellipse([zx - 13, GROUND - 2, zx + 13, GROUND + 3], fill=(0, 0, 0, 90))
            c.sprites.append((zombie, (int(zx) - FEET[0], GROUND - FEET[1] - (1 if math.sin(t * 7) > 0.6 else 0))))
            c.zx = zx
        if self.dead + 0.15 <= t < self.dead + 1.0:
            k = (t - self.dead - 0.15) / 0.85
            for px, py, pr in self.puff:
                x, y = 112 + px * 32 * k, GROUND - 24 + py * 40 * k
                size = 1 + int(pr * 3)
                if pr > k * 0.9:
                    d.rectangle([x, y, x + size, y + size], fill=(*_shade((238, 238, 238), 0.7 + 0.3 * pr), 255))
        if c.hit > 0.3 and c.zx is not None:  # the blade's arc and the sparks
            d.arc([c.hx + 2, GROUND - 74, c.hx + 58, GROUND - 14], -62, 58, fill=(255, 255, 255, int(255 * c.hit)), width=3)
            for dx, dy in ((-8, -6), (6, -10), (-4, 8), (8, 4), (0, -14), (-12, 2)):
                d.rectangle([c.zx - 12 + dx, GROUND - 44 + dy, c.zx - 10 + dx, GROUND - 42 + dy], fill=(255, 244, 170, 255))
        night = self.night(t)
        for fx, fy, ph in self.flies:
            blink = night * max(0.0, math.sin(t * 2.2 + ph * 9))
            if blink > 0.25:
                x = int((fx - c.scroll + 8 * math.sin(t * 0.9 + ph * 7)) % LW)
                y = int(fy + 5 * math.sin(t * 1.3 + ph * 5))
                d.rectangle([x - 1, y - 1, x + 2, y + 2], fill=(210, 255, 120, int(80 * blink)))
                d.rectangle([x, y, x + 1, y + 1], fill=(238, 255, 160, int(255 * blink)))

    def sharp(self, c, img, d) -> None:
        t = c.t
        if c.zx is not None and t < self.dead:
            cx, cy = int(c.zx * S), (GROUND - 80) * S
            _label(d, (cx, cy - 34), "ZOMBIE", self.art.small, stroke=6)
            d.rectangle([cx - 110, cy, cx + 110, cy + 26], fill=(12, 12, 16, 255))
            if self.health(t) > 0:
                d.rectangle([cx - 104, cy + 6, cx - 104 + int(208 * self.health(t)), cy + 20], fill=(226, 30, 40, 255))
        self.blows(t, d, 112 * S + 70, (GROUND - 88) * S)

    def sky(self, t: float, d) -> None:
        if t < self.title:
            k = min(1.0, t / 0.18)
            for i, line in enumerate(("CAN YOU BEAT", "MINECRAFT?")):
                _label(d, (W // 2, 420 + i * 170 - int(40 * (1 - k))), line, self.art.title, (255, 222, 0), 12)
        else:
            super().sky(t, d)


# --------------------------------------------------------------------------- stop 2: the cave
class Cave(Stop):
    N, NAME, NEXT, COLOUR, KIND, MINE = 2, "THE CAVE", "THE VILLAGE", (150, 220, 255), "PICKAXE", "Iron Pickaxe"
    SOLO, HOOK = "Pause To Dig Out Of The Cave ⛏️", "Ten hearts. Can you dig out of this cave?"
    DARE, SLUG = "DIG OUT?", "cave"
    L = 20.0
    WALL, BLOCKS = 100, 4  # the fallen blocks' left edge when the hero has stopped, and how many

    @classmethod
    def options(cls) -> list[Option]:
        from . import dungeon

        metals = list(dungeon.METALS)
        out = []
        for metal, weight in zip(metals, _by_tier([dungeon.METALS[m][0] for m in metals])):
            name = f"{metal} Pickaxe"
            secs = dungeon.break_seconds("Cobblestone", name)
            out.append(Option(name, dungeon.METALS[metal][0], f"{secs:g} S A BLOCK",
                              0 if secs <= 0.4 else -1 if secs <= 0.6 else -2, weight, name))
        return out

    @classmethod
    def lines(cls, before: int) -> list[tuple[str, str]]:
        lost = -cls.mine().delta
        return [("title", "Stop two: the cave."),
                ("threat", "The roof falls in, and a skeleton is shooting at your back."),
                ("pause", "Pause now! That is your pickaxe. A fast one digs you out in time. "
                          "A slow one, and the arrows find you."),
                ("mine", f"I stopped on an iron pickaxe. Half a second a block. One arrow gets me, so I lose {_hearts(lost)}."),
                ("count", "Fun fact: gold is the fastest pickaxe in the game."),
                ("next", "Next stop: the village.")]

    COUNT = "GOLD IS THE FASTEST!"

    def build(self) -> None:
        from PIL import ImageDraw

        self.wall, self.ground = _blank(), _blank()
        _fill(self.wall, 0, GROUND, (62, 64, 74), 51, dark=-0.25, lit=False)
        d = ImageDraw.Draw(self.wall)
        for i, x in enumerate(range(8, STRIP, 26)):  # stalactites under the roof
            tall = 8 + int(_noise(i, 0, 53) * 30)
            for step in range(3):
                d.rectangle([x + step * 2, 44, x + 11 - step * 2, 52 + tall * (step + 1) // 3], fill=_shade((48, 50, 60), 1 - 0.1 * step))
        self.torches = list(range(60, STRIP, 120))
        for x in self.torches:
            d.rectangle([x, GROUND - 60, x + 1, GROUND - 52], fill=(120, 84, 50))
        _fill(self.ground, GROUND, LH, (112, 114, 122), 55, specks=ORES)
        self.bits = [(self.rng.uniform(-1, 1), self.rng.uniform(-1.2, 0.3), self.rng.random()) for _ in range(14)]

    def plan(self) -> None:
        span = min(self.window, 3.2)
        self.breaks = [self.act + span * (i + 1) / self.BLOCKS for i in range(self.BLOCKS)]  # from the top down
        self.swings = [x - 0.25 for x in self.breaks] + self.breaks
        self.hits = list(self.breaks)
        lost = -self.pick.delta
        self.hurts = [(self.act + span * (j + 1.4) / (lost + 1.6), -1) for j in range(lost)]
        self.misses = [self.act + 0.35]
        self.lands = [self.threat + 0.25 + 0.14 * i for i in range(self.BLOCKS)]
        self.quakes = [self.lands[0], self.lands[-1]]

    def backdrop(self, t: float, scroll: float):
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (LW, LH), (0, 0, 0, 255))
        img.alpha_composite(_wrap(self.wall, scroll * 0.7))
        glow = _blank((LW, LH))
        d = ImageDraw.Draw(glow)
        for i, tx in enumerate(self.torches):
            x = int((tx - scroll * 0.7) % STRIP)
            if x > LW + 40:
                x -= STRIP
            flick = 0.8 + 0.2 * math.sin(t * 11 + i * 2)
            for grow, alpha in ((34, 14), (22, 22), (12, 34), (5, 60)):
                d.rectangle([x - grow, GROUND - 62 - grow, x + 1 + grow, GROUND - 60 + grow], fill=(255, 170, 70, int(alpha * flick)))
            d.rectangle([x - 1, GROUND - 65, x + 2, GROUND - 61], fill=(255, 150, 40, 255))
            d.rectangle([x, GROUND - 64 - int(flick * 2), x + 1, GROUND - 62], fill=(255, 236, 130, 255))
        img.alpha_composite(glow)
        img.alpha_composite(_wrap(self.ground, scroll))
        return img

    def pose(self, t: float) -> tuple:
        option = self.option(t)
        if option is None:
            return None, 0.0, None, 168
        arm, turn = _swing(t, self.swings) if self.act <= t < self.breaks[-1] + 0.2 else (1.15, 0.0)
        return arm, turn, option.icon, 168

    def scene(self, c) -> None:
        t, d, du = c.t, c.d, c.du
        x0 = int(self.WALL + c.shift)
        tile = self.art.extra["tile:cobble"]
        for i in range(self.BLOCKS):  # block 0 is the lowest; the top one breaks first
            broke = self.breaks[self.BLOCKS - 1 - i]
            if t < self.lands[i] - 0.3 or t >= broke:
                if 0 <= t - broke < 0.45:
                    k = (t - broke) / 0.45
                    for px, py, pr in self.bits:
                        x, y = x0 + 8 + px * 22 * k, GROUND - 8 - 16 * i + py * 18 * k + 30 * k * k
                        d.rectangle([x, y, x + 2, y + 2], fill=(*_shade((122, 122, 126), 0.6 + 0.5 * pr), 255))
                continue
            drop = 1 - min(1.0, (t - self.lands[i] + 0.3) / 0.3)
            y = GROUND - 16 * (i + 1) - int(drop * drop * 130)
            c.sprites.append((tile, (x0, y)))
            crack = (t - broke + 0.5) / 0.5
            if crack > 0:
                for j in range(int(crack * 6) + 1):
                    cx, cy = x0 + 2 + int(_noise(i, j, 61) * 12), y + 2 + int(_noise(i, j, 63) * 12)
                    d.line([(cx, cy), (cx + 3, cy + 2)], fill=(20, 20, 24, 255))
        for at in self.lands[::3]:  # dust where the roof lands
            if 0 <= t - at < 0.5:
                k = (t - at) / 0.5
                for i in range(6):
                    x = x0 + 8 + (i - 2.5) * 9 * (0.4 + k)
                    d.rectangle([x, GROUND - 4 - 14 * k, x + 3, GROUND - 1 - 14 * k], fill=(200, 200, 204, int(220 * (1 - k))))
        # the skeleton behind, and its arrows
        show = self.threat + 0.9
        c.sk = None
        if t >= show:
            sx = -22 + 38 * _ease((t - show) / 0.6) + c.shift
            if sx > -30:
                aim = 1.45 + 0.08 * math.sin(t * 5)
                figure, hand = _figure(SKELETON_LOOK, 0.0, 0.6, aim, 0.0)
                corner = (int(sx) - FEET[0], GROUND - FEET[1])
                c.sprites.append((_outlined(figure), corner))
                du.ellipse([sx - 12, GROUND - 2, sx + 12, GROUND + 3], fill=(0, 0, 0, 90))
                c.sk = (corner[0] + hand[0], corner[1] + hand[1])
        for at, hits in [(x, True) for x, _ in self.hurts] + [(x, False) for x in self.misses]:
            k = (t - at + 0.22) / 0.22
            if 0 <= k < 1 or (not hits and 0 <= t - at < 2.5):
                end = c.hx - 8 if hits else x0 - 1
                x = 34 + (end - 34) * min(1.0, k)
                y = GROUND - 42 - (0 if hits else 10 * min(1.0, k))
                d.line([(x - 9, y), (x, y)], fill=(150, 110, 70, 255))
                d.rectangle([x, y - 1, x + 1, y + 1], fill=(230, 230, 230, 255))
                d.rectangle([x - 10, y - 1, x - 9, y + 1], fill=(240, 240, 240, 255))

    def sharp(self, c, img, d) -> None:
        if c.sk is not None:
            bow = self.art.icon("Bow", 132)
            img.alpha_composite(bow, (max(0, int(c.sk[0] * S) - 50 + c.shake[0]), max(0, int(c.sk[1] * S) - 80 + c.shake[1])))
        for i, at in enumerate(self.breaks):
            if 0 <= c.t - at < 0.6:
                _label(d, (int((self.WALL + c.shift + 8) * S), (GROUND - 80) * S - int(90 * (c.t - at) / 0.6)),
                       f"{i + 1} OF {self.BLOCKS}", self.art.name, (255, 232, 60))


# --------------------------------------------------------------------------- stop 3: the village
class Village(Stop):
    N, NAME, NEXT, COLOUR, KIND, MINE = 3, "THE VILLAGE", "THE NETHER", (255, 214, 120), "LOOT", "Rotten Flesh"
    L = 20.0
    ZERO = ("NOTHING HAPPENS", GREY)
    COUNT = "TEN HEARTS IS THE MOST"
    GIVES = {"Bread": 1, "Golden Carrot": 1, "Golden Apple": 2, "Enchanted Golden Apple": 3, "Totem of Undying": 3,
             "Rotten Flesh": -1}
    CHEST = 104

    @classmethod
    def options(cls) -> list[Option]:
        from . import dungeon

        names = list(dungeon.LOOT)
        out = []
        for name, weight in zip(names, _by_tier([dungeon.LOOT[n] for n in names])):
            gives = cls.GIVES.get(name, 0)
            sub = "YUCK!" if gives < 0 else "SAVES YOUR LIFE" if "Totem" in name else "EAT IT!" if gives else "NOT FOOD"
            out.append(Option(name, dungeon.LOOT[name], sub, gives, weight, name))
        return out

    @classmethod
    def lines(cls, before: int) -> list[tuple[str, str]]:
        return [("title", "Stop three: the village."),
                ("threat", "The blacksmith has a chest, and nobody is watching."),
                ("pause", "Pause now! That is your loot. Food gives hearts back, but ten is the most. "
                          "And some food bites back."),
                ("mine", f"I stopped on rotten flesh. Yuck. I lose {_hearts(cls.mine().delta)}."),
                ("count", "A golden apple gives two hearts. The totem gives three."),
                ("next", "Next stop: the Nether.")]

    def build(self) -> None:
        from PIL import ImageDraw

        self.hills, self.far, _, self.ground = self.art.forest
        self.houses = _blank()
        d = ImageDraw.Draw(self.houses)
        for i, x in enumerate(range(30, STRIP - 60, 138)):
            _house(d, x, 70 + i)
        self.sky_day = _sky((86, 150, 236), (196, 228, 250))
        self.clouds = [(self.rng.randrange(LW + 80), self.rng.randrange(50, 110), self.rng.randrange(26, 44),
                        self.rng.uniform(1.5, 4)) for _ in range(4)]

    def plan(self) -> None:
        self.eat = self.act + 0.6
        if self.pick.delta:
            self.hurts = [(self.eat, self.pick.delta)]

    def backdrop(self, t: float, scroll: float):
        from PIL import ImageDraw

        sky = self.sky_day.copy()
        d = ImageDraw.Draw(sky)
        for grow, alpha in ((12, 40), (6, 80)):
            d.rectangle([130 - grow, 62 - grow, 150 + grow, 82 + grow], fill=(255, 244, 190))
        d.rectangle([130, 62, 150, 82], fill=(255, 250, 220))
        for cx, cy, cw, speed in self.clouds:
            x = int((cx - t * speed - scroll * 0.06) % (LW + 80)) - 60
            d.rectangle([x, cy + 4, x + cw, cy + 9], fill=(255, 255, 255))
            d.rectangle([x + 6, cy, x + cw - 10, cy + 4], fill=(255, 255, 255))
        for strip, speed in ((self.hills, 0.12), (self.far, 0.45), (self.houses, 1.0), (self.ground, 1.0)):
            sky.alpha_composite(_wrap(strip, scroll * speed))
        return sky

    def scene(self, c) -> None:
        t, d = c.t, c.d
        vx = 150 + c.shift  # the villager looks the other way
        figure, _ = _figure(VILLAGER_LOOK, 0.0, 0.1, 0.25 + 0.05 * math.sin(t * 2), 0.0)
        c.sprites.append((_outlined(figure), (int(vx) - FEET[0], GROUND - FEET[1])))
        c.du.ellipse([vx - 12, GROUND - 2, vx + 12, GROUND + 3], fill=(0, 0, 0, 90))
        if self.pause <= t < self.eat:  # the chest glows while it is open
            x = self.CHEST + c.shift
            for i in range(5):
                a = -math.pi / 2 + (i - 2) * 0.42 + 0.1 * math.sin(t * 3)
                d.line([(x, GROUND - 16), (x + math.cos(a) * 42, GROUND - 16 + math.sin(a) * 42)], fill=(255, 236, 130, 110), width=2)
        if 0 <= t - self.eat < 0.6 and self.pick.delta > 0:
            k = (t - self.eat) / 0.6
            for i in range(8):
                a = i * math.pi / 4
                x, y = c.hx + math.cos(a) * 26 * k, GROUND - 40 + math.sin(a) * 30 * k
                d.rectangle([x, y, x + 2, y + 2], fill=(120, 255, 130, int(255 * (1 - k))))

    def sharp(self, c, img, d) -> None:
        t = c.t
        x, y = int((self.CHEST + c.shift) * S), GROUND * S
        img.alpha_composite(self.art.icon("Chest", 132), (max(0, x - 66 + c.shake[0]), y - 122))
        if c.option is not None and t < self.eat:
            k = _ease((t - self.act) / 0.6) if t >= self.act else 0.0
            ix = x + (int(c.hx * S) - x) * k
            iy = y - 300 - int(26 * math.sin(t * 4)) * (1 - k) - 160 * math.sin(math.pi * k) + 110 * k
            size = int(168 * (1 - 0.35 * k))
            img.alpha_composite(self.art.icon(c.option.icon, size), (max(0, int(ix) - size // 2), int(iy) - size // 2))


# --------------------------------------------------------------------------- stop 4: the Nether
class Nether(Stop):
    N, NAME, NEXT, COLOUR, KIND, MINE = 4, "THE NETHER", "THE FORTRESS", (255, 120, 80), "LANDING", "Magma"
    SOLO, HOOK = "Pause To Cross The Lava Lake 🔥", "Ten hearts. Can you cross the lava?"
    DARE, SLUG = "CROSS THE LAVA?", "nether"
    ALONE = {"count": "How many hearts do you have left?"}
    L = 90.0
    LAKE, BLOCK = (74, 128), 93  # the lava's edges and the block's left edge once the hero has stopped
    RISE = 10  # how far the block stands out of the lava

    @classmethod
    def options(cls) -> list[Option]:
        return [Option("Stone", "", "SAFE", 0, 0.5, "tile:stone"), Option("Magma", "", "HOT!", -1, 0.3, "tile:magma"),
                Option("Lava", "", "NO BLOCK AT ALL", -2, 0.2, "tile:lava")]

    @classmethod
    def lines(cls, before: int) -> list[tuple[str, str]]:
        return [("title", "Stop four: the Nether."),
                ("threat", "A lake of lava, and one block to jump on. But the block keeps changing."),
                ("pause", "Pause now! That is where you land. Stone is safe. Magma burns one heart. Lava burns two."),
                ("mine", f"I landed on magma. Ouch. I lose {_hearts(cls.mine().delta)}."),
                ("count", "Count your hearts."),
                ("next", "Next stop: the fortress.")]

    def build(self) -> None:
        from PIL import ImageDraw

        self.far, self.ground = _blank(), _blank()
        d = ImageDraw.Draw(self.far)
        turn = 2 * math.pi / STRIP
        for x in range(0, STRIP, 4):
            h = 4 * int((50 + 22 * math.sin(turn * 4 * x) + 12 * math.sin(turn * 9 * x + 2) + 8 * _noise(x, 0, 81)) / 4)
            d.rectangle([x, GROUND - h, x + 3, GROUND], fill=(74, 22, 26))
            d.rectangle([x, GROUND - h, x + 3, GROUND - h + 3], fill=(104, 36, 36))
            top = 4 * int((14 + 10 * math.sin(turn * 6 * x + 1) + 10 * _noise(x, 1, 83)) / 4)
            d.rectangle([x, 40, x + 3, 44 + top], fill=(58, 16, 22))
        _fill(self.ground, GROUND, LH, (122, 50, 50), 85, specks=((232, 222, 210), (250, 214, 74)))
        self.sky_red = _sky((40, 6, 12), (168, 44, 26))
        self.embers = [(self.rng.randrange(LW), self.rng.random(), self.rng.uniform(0.15, 0.4)) for _ in range(26)]

    def plan(self) -> None:
        self.j1, self.j2 = self.act, self.act + min(1.4, self.window - 0.6)
        if self.pick.delta:
            self.hurts = [(self.j1 + 0.45, self.pick.delta)]
        self.sounds += [(self.j1, "blip"), (self.j2, "blip")]

    def advance(self, t: float) -> float:
        mid = self.BLOCK + 8 - HERO_X
        return mid * min(1.0, max(0.0, (t - self.j1) / 0.45)) + (self.L - mid) * min(1.0, max(0.0, (t - self.j2) / 0.45))

    def jump(self, t: float) -> float:
        for at in (self.j1, self.j2):
            if 0 <= t - at < 0.45:
                k = (t - at) / 0.45
                on = self.RISE * (k if at == self.j1 else 1 - k) if self.pick.name != "Lava" else 0
                return 30 * math.sin(math.pi * k) + on
        return self.RISE if self.j1 <= t < self.j2 and self.pick.name != "Lava" else 0.0

    def walking(self, t: float) -> bool:
        return t < self.halt or t >= self.go

    def backdrop(self, t: float, scroll: float):
        sky = self.sky_red.copy()
        sky.alpha_composite(_wrap(self.far, scroll * 0.4))
        sky.alpha_composite(_wrap(self.ground, scroll))
        return sky

    def scene(self, c) -> None:
        t, d, du = c.t, c.d, c.du
        x0, x1 = int(self.LAKE[0] + c.shift), int(self.LAKE[1] + c.shift)
        for x in range(max(-2, x0), min(LW, x1), 2):
            for y in range(GROUND, GROUND + 14, 2):
                n = _noise((x - int(c.shift)) // 4 + int(t * 3), y // 4, 87)
                du.rectangle([x, y, x + 1, y + 1], fill=(*_mix((230, 84, 14), (255, 204, 66), n), 255))
            du.rectangle([x, GROUND, x + 1, GROUND], fill=(255, 232, 150, 255))
        for i in range(10):  # the heat over the lake
            du.rectangle([x0, GROUND - 3 * (i + 1), x1 - 1, GROUND - 3 * i - 1], fill=(255, 120, 30, 46 - 4 * i))
        kind = c.option.name if c.option else self.pick.name if t >= self.go else "Stone"
        if kind != "Lava":
            c.sprites.append((self.art.extra[f"tile:{kind.lower()}"], (int(self.BLOCK + c.shift), GROUND - self.RISE)))
        for ex, ph, speed in self.embers:
            k = (t * speed + ph) % 1.0
            x = int((ex - c.scroll * 0.8 + 6 * math.sin(t * 2 + ph * 9)) % LW)
            d.rectangle([x, GROUND - 130 * k, x + 1, GROUND - 130 * k + 1], fill=(255, 190, 80, int(230 * (1 - k))))
        for at, _ in self.hurts:
            if 0 <= t - at < 0.4:
                k = (t - at) / 0.4
                for i in range(7):
                    x = c.hx - 12 + i * 4
                    d.rectangle([x, GROUND - 4 - 30 * k * (0.5 + _noise(i, 0, 89)), x + 2, GROUND - 2], fill=(255, 170, 40, int(255 * (1 - k))))


# --------------------------------------------------------------------------- stop 5: the fortress
class Fortress(Stop):
    N, NAME, NEXT, COLOUR, KIND, MINE = 5, "THE FORTRESS", "THE STRONGHOLD", (255, 170, 60), "SHIELD", "Shield Down"
    SOLO, HOOK = "Pause To Block The Blaze 🛡️", "Ten hearts. Can you block a blaze?"
    DARE, SLUG = "BLOCK THE BLAZE?", "fortress"
    BLAZE = 140

    @classmethod
    def options(cls) -> list[Option]:
        return [Option("Shield Up", "", "BLOCKS IT ALL", 0, 0.45, "Shield"),
                Option("Shield Halfway", "", "TOO SLOW", -1, 0.35, "Shield", -45.0),
                Option("Shield Down", "", "OUCH", -2, 0.2, "Shield", -90.0)]

    @classmethod
    def lines(cls, before: int) -> list[tuple[str, str]]:
        after = before + cls.mine().delta
        return [("title", "Stop five: the fortress."),
                ("threat", "A blaze is throwing fire at you, and all you have is a shield."),
                ("pause", "Pause now! That is your shield. Up blocks it all. Halfway costs one heart. Down costs two."),
                ("mine", f"My shield was down. Of course. I lose {_hearts(cls.mine().delta)}."),
                ("count", f"{_hearts(after).capitalize()} left for me. How about you?"),
                ("next", "Next stop: the stronghold.")]

    COUNT = "HOW MANY DO YOU HAVE?"

    def build(self) -> None:
        from PIL import ImageDraw

        self.wall, self.ground = _blank(), _blank()
        _fill(self.wall, 0, GROUND, (58, 26, 34), 91, brick=True, dark=-0.2, lit=False)
        d = ImageDraw.Draw(self.wall)
        for x in range(40, STRIP, 120):  # windows onto the red sky, and pillars between them
            d.rectangle([x - 2, GROUND - 92, x + 19, GROUND - 50], fill=(30, 12, 18))
            for i in range(10):
                d.rectangle([x, GROUND - 90 + i * 4, x + 17, GROUND - 87 + i * 4], fill=_mix((70, 10, 14), (190, 60, 28), i / 9))
            d.rectangle([x + 60, 0, x + 71, GROUND], fill=(36, 16, 22))
            d.rectangle([x + 60, 0, x + 61, GROUND], fill=(66, 32, 40))
        _fill(self.ground, GROUND, LH, (74, 34, 42), 93, brick=True)
        self.rods = [i * math.pi / 3 for i in range(6)]

    def plan(self) -> None:
        self.shot, self.burn = self.act + 0.2, self.act + 0.75
        if self.pick.delta:
            self.hurts = [(self.burn, self.pick.delta)]
        self.quakes = [self.burn]
        self.sounds.append((self.shot, "blip"))
        self.leave = self.burn + 0.8

    def backdrop(self, t: float, scroll: float):
        from PIL import Image

        img = Image.new("RGBA", (LW, LH), (0, 0, 0, 255))
        img.alpha_composite(_wrap(self.wall, scroll * 0.7))
        img.alpha_composite(_wrap(self.ground, scroll))
        return img

    def pose(self, t: float) -> tuple:
        option = self.option(t)
        if option is None:
            return None, 0.0, None, 168
        arm = {0.0: 1.5, -45.0: 0.95, -90.0: 0.3}[option.turn]
        return arm, option.turn, "Shield", 168

    def blaze(self, c) -> tuple[float, float]:
        t = c.t
        rise = max(0.0, t - self.leave)
        come = 1 - _ease((t - self.threat) / 0.7)
        return self.BLAZE + c.shift + 60 * come, GROUND - 50 + 5 * math.sin(t * 3) - 140 * rise * rise - 60 * come

    def scene(self, c) -> None:
        t, d, du = c.t, c.d, c.du
        if t >= self.threat:
            bx, by = self.blaze(c)
            spin_at = t * 4

            def rods(front: bool) -> None:
                for a in self.rods:
                    if (math.sin(a + spin_at) > 0) == front:
                        x, y = bx + 15 * math.cos(a + spin_at), by + 16 + 5 * math.sin(a * 2 + t * 2)
                        du.rectangle([x - 1, y, x + 2, y + 13], fill=(*_shade((214, 150, 30), 1.1 if front else 0.7), 255))
                        du.rectangle([x - 1, y, x, y + 13], fill=(255, 220, 90, 255) if front else (170, 110, 20, 255))

            for grow, alpha in ((22, 16), (14, 28), (8, 44)):
                du.rectangle([bx - 8 - grow, by - 8 - grow, bx + 8 + grow, by + 20 + grow], fill=(255, 150, 40, alpha))
            rods(False)
            du.rectangle([bx - 9, by - 9, bx + 8, by + 8], fill=(20, 12, 10, 255))
            du.rectangle([bx - 8, by - 8, bx + 7, by + 7], fill=(238, 190, 44, 255))
            du.rectangle([bx - 8, by - 8, bx + 7, by - 5], fill=(255, 226, 110, 255))
            du.rectangle([bx - 8, by + 4, bx + 7, by + 7], fill=(196, 130, 26, 255))
            du.rectangle([bx - 6, by - 2, bx - 3, by], fill=(60, 30, 10, 255))
            du.rectangle([bx + 1, by - 2, bx + 4, by], fill=(60, 30, 10, 255))
            rods(True)
            for i in range(5):  # smoke
                k = (t * 0.8 + i / 5) % 1.0
                du.rectangle([bx - 6 + i * 3, by - 12 - 26 * k, bx - 4 + i * 3, by - 10 - 26 * k], fill=(90, 80, 80, int(150 * (1 - k))))
            if self.pause <= t < self.shot:  # it winds up
                k = abs(math.sin(t * 5))
                d.rectangle([bx - 20 - 3 * k, by - 3 - 3 * k, bx - 13 + 3 * k, by + 4 + 3 * k], fill=(255, 170, 40, 255))
                d.rectangle([bx - 18, by - 1, bx - 15, by + 2], fill=(255, 244, 170, 255))
            if self.shot <= t < self.burn:
                k = (t - self.shot) / (self.burn - self.shot)
                fx, fy = bx - 16 + (c.hx + 14 - bx + 16) * k, by + (GROUND - 40 - by) * k
                for i in range(6):
                    d.rectangle([fx + i * 4, fy - 3 + i % 2, fx + i * 4 + max(1, 5 - i), fy + 3 - i % 2], fill=(255, 140 + 16 * i, 30, 255 - 36 * i))
                d.rectangle([fx - 4, fy - 4, fx + 4, fy + 4], fill=(255, 120, 20, 255))
                d.rectangle([fx - 2, fy - 2, fx + 2, fy + 2], fill=(255, 240, 160, 255))
        if 0 <= t - self.burn < 0.45:
            k = (t - self.burn) / 0.45
            for i in range(12):
                a = i * math.pi / 6
                x, y = c.hx + 14 + math.cos(a) * 30 * k, GROUND - 40 + math.sin(a) * 30 * k
                d.rectangle([x, y, x + 3, y + 3], fill=(255, 200 - 10 * i, 50, int(255 * (1 - k))))

    def sharp(self, c, img, d) -> None:
        if self.threat + 0.7 <= c.t < self.leave:
            bx, by = self.blaze(c)
            _label(d, (int(bx * S), int((by - 22) * S)), "BLAZE", self.art.small, stroke=6)
        if self.pick.delta == 0 and 0 <= c.t - self.burn < 0.7:
            _label(d, (int(c.hx * S) + 60, (GROUND - 90) * S), "BLOCKED!", self.art.name, (110, 240, 120))


# --------------------------------------------------------------------------- stop 6: the stronghold
class Stronghold(Stop):
    N, NAME, NEXT, COLOUR, KIND, MINE = 6, "THE STRONGHOLD", "THE END", (190, 200, 210), "DOOR", "Door 1"
    SOLO, HOOK = "Pick A Door To Find The End Portal 🚪", "Ten hearts. Can you find the End portal?"
    DARE, SLUG = "FIND THE PORTAL?", "stronghold"
    TAG = "PICK A DOOR"
    STAYS = True
    DOORS = (84, 116, 148)  # their middles once the hero has stopped
    BEHIND = (("SILVERFISH", -1), ("THE PORTAL", 0), ("LAVA", -2))

    @classmethod
    def options(cls) -> list[Option]:
        return [Option(f"Door {i + 1}", "", name, delta, 1.0) for i, (name, delta) in enumerate(cls.BEHIND)]

    @classmethod
    def lines(cls, before: int) -> list[tuple[str, str]]:
        after = before + cls.mine().delta
        return [("title", "Stop six: the stronghold."),
                ("threat", "Three doors. Only one hides the portal."),
                ("pause", "No pausing here. Pick a door: one, two, or three. Say it out loud. Three. Two. One."),
                ("d1", "Door one: silverfish. Lose one heart."),
                ("d2", "Door two: the portal. You are safe."),
                ("d3", "Door three: lava. Lose two hearts."),
                ("mine", f"I picked door one. {_hearts(after).capitalize()} left."),
                ("next", "Last stop: the End.")]

    def build(self) -> None:
        from PIL import ImageDraw

        self.wall, self.ground = _blank(), _blank()
        _fill(self.wall, 0, GROUND, (92, 94, 102), 101, brick=True, specks=((74, 110, 62),), dark=-0.15, lit=False)
        d = ImageDraw.Draw(self.wall)
        self.torches = list(range(30, STRIP, 90))
        for x in self.torches:
            d.rectangle([x, GROUND - 74, x + 1, GROUND - 66], fill=(120, 84, 50))
        _fill(self.ground, GROUND, LH, (112, 114, 122), 103, brick=True)
        self.opens = [self.sent[r][0].start for r in ("d1", "d2", "d3")]
        self.ticks = [w.start for w in self.sent["pause"][-3:]]

    def plan(self) -> None:
        self.run = self.act
        self.hurts = [(self.run + 0.5, self.pick.delta)]
        self.sounds += [(x, "beep") for x in self.ticks] + [(x, "blip") for x in self.opens]
        self.enter = self.go + 1.2

    def option(self, t: float) -> Option | None:
        return None

    def advance(self, t: float) -> float:
        return (self.DOORS[1] - HERO_X) * _ease((t - self.go) / 1.2)

    def walking(self, t: float) -> bool:
        return t < self.halt or self.go <= t < self.enter

    def hidden(self, t: float) -> bool:
        return t >= self.enter

    def backdrop(self, t: float, scroll: float):
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (LW, LH), (0, 0, 0, 255))
        img.alpha_composite(_wrap(self.wall, scroll))
        glow = _blank((LW, LH))
        d = ImageDraw.Draw(glow)
        for i, tx in enumerate(self.torches):
            x = int((tx - scroll) % STRIP)
            flick = 0.8 + 0.2 * math.sin(t * 11 + i * 2)
            for grow, alpha in ((30, 12), (18, 20), (9, 32)):
                d.rectangle([x - grow, GROUND - 76 - grow, x + 1 + grow, GROUND - 74 + grow], fill=(255, 170, 70, int(alpha * flick)))
            d.rectangle([x - 1, GROUND - 79, x + 2, GROUND - 75], fill=(255, 150, 40, 255))
            d.rectangle([x, GROUND - 78, x + 1, GROUND - 76], fill=(255, 236, 130, 255))
        img.alpha_composite(glow)
        img.alpha_composite(_wrap(self.ground, scroll))
        return img

    def scene(self, c) -> None:
        t, d, du = c.t, c.d, c.du
        for i, mid in enumerate(self.DOORS):
            x = int(mid + c.shift)
            du.rectangle([x - 13, GROUND - 44, x + 12, GROUND - 1], fill=(40, 40, 46, 255))
            box = [x - 10, GROUND - 41, x + 9, GROUND - 1]
            if t < self.opens[i]:
                du.rectangle(box, fill=(126, 88, 50, 255))
                for px in range(x - 10, x + 10, 5):
                    du.line([(px, GROUND - 41), (px, GROUND - 1)], fill=(96, 64, 36, 255))
                du.rectangle([x - 10, GROUND - 34, x + 9, GROUND - 32], fill=(70, 72, 80, 255))
                du.rectangle([x - 10, GROUND - 12, x + 9, GROUND - 10], fill=(70, 72, 80, 255))
                du.rectangle([x + 5, GROUND - 22, x + 6, GROUND - 20], fill=(230, 220, 160, 255))
                continue
            du.rectangle(box, fill=(12, 12, 16, 255))
            if i == 1:  # the portal: the dark with stars in it
                for px in range(x - 10, x + 10, 2):
                    for py in range(GROUND - 41, GROUND - 1, 2):
                        n = _noise(px + int(t * 6), py - int(t * 9), 105)
                        if n > 0.7:
                            du.rectangle([px, py, px + 1, py + 1], fill=(*_mix((20, 90, 80), (170, 255, 220), (n - 0.7) / 0.3), 255))
                for grow, alpha in ((10, 18), (5, 34)):
                    d.rectangle([x - 10 - grow, GROUND - 41 - grow, x + 9 + grow, GROUND - 1], fill=(90, 255, 200, alpha))
            elif i == 2:
                for px in range(x - 10, x + 10, 2):
                    for py in range(GROUND - 41, GROUND - 1, 2):
                        n = _noise(px // 4, (py + int(t * 14)) // 4, 107)
                        du.rectangle([px, py, px + 1, py + 1], fill=(*_mix((230, 84, 14), (255, 204, 66), n), 255))
                for grow, alpha in ((10, 18), (5, 34)):
                    d.rectangle([x - 10 - grow, GROUND - 41 - grow, x + 9 + grow, GROUND - 1], fill=(255, 130, 30, alpha))
        if self.opens[0] <= t < self.run + 0.75:  # the silverfish
            k = min(1.0, max(0.0, (t - self.run) / 0.5))
            x = self.DOORS[0] + c.shift + (c.hx + 12 - self.DOORS[0] - c.shift) * k
            for i in range(5):
                y = GROUND - 5 + (1 if int(t * 14 + i) % 2 else 0)
                d.rectangle([x + i * 3, y, x + i * 3 + 2, GROUND - 1], fill=(*_shade((150, 154, 160), 1.1 - 0.12 * i), 255))
            d.rectangle([x, GROUND - 4, x, GROUND - 4], fill=(10, 10, 14, 255))
        if t >= self.go:  # the hero walks into the portal
            k = (t - self.enter) / 0.5
            if 0 <= k < 1:
                x = int(self.DOORS[1] + c.shift)
                for i in range(10):
                    a = i * math.pi / 5 + t * 3
                    d.rectangle([x + math.cos(a) * 20 * (1 - k), GROUND - 22 + math.sin(a) * 26 * (1 - k),
                                 x + 2 + math.cos(a) * 20 * (1 - k), GROUND - 20 + math.sin(a) * 26 * (1 - k)], fill=(150, 255, 220, 255))

    def sharp(self, c, img, d) -> None:
        for i, mid in enumerate(self.DOORS):
            _label(d, (int((mid + c.shift) * S), (GROUND - 58) * S), str(i + 1), self.art.big, (255, 222, 0), 10)

    def pause_words(self, t: float, d) -> None:
        left = [n for n, at in zip("321", self.ticks) if t >= at]
        if t >= self.opens[0]:
            return
        if left:
            pop = 1 + 0.3 * _since(t, self.ticks, 0.25)
            _label(d, (W // 2, 470), left[-1], self.art.font("Anton-Regular", int(260 * pop)), (255, 222, 0), 14)
        else:
            _label(d, (W // 2, 400), "NO PAUSE!", self.art.big, (255, 255, 255), 10)
            _label(d, (W // 2, 540), "PICK A DOOR", self.art.title, (255, 222, 0), 12)

    def card(self, t: float):
        if t < self.pause:
            return self.before(), 0.0
        shown = sum(t >= x for x in self.opens)
        key = ("doors", shown, t >= self.mine_at)
        cards = self.art._cards
        if key not in cards:
            head = "I PICKED DOOR 1" if key[2] else "WHAT IS BEHIND YOUR DOOR?"
            im, d = self.art.shell((255, 196, 0), head, "", GREY)
            for i, (name, delta) in enumerate(self.BEHIND):
                x0 = 24 + i * 306
                open_ = i < shown
                colour = (GREEN if delta == 0 else ORANGE if delta == -1 else RED) if open_ else (52, 56, 70)
                d.rectangle([x0, 96, x0 + 298, 386], fill=colour)
                if key[2] and i == 0:
                    d.rectangle([x0, 96, x0 + 298, 386], outline=(255, 255, 255), width=8)
                d.text((x0 + 149, 138), f"DOOR {i + 1}", font=self.art.head, fill=(255, 255, 255), anchor="mm")
                if open_:
                    d.text((x0 + 149, 226), name, font=self.art.fit(d, name, 270, "Poppins-Black", 54), fill=(255, 255, 255), anchor="mm")
                    words = "SAFE!" if delta == 0 else f"-{-delta} HEART{'S' if delta < -1 else ''}"
                    d.text((x0 + 149, 320), words, font=self.art.fit(d, words, 270, "Poppins-Black", 54), fill=(255, 255, 255), anchor="mm")
                else:
                    d.text((x0 + 149, 262), "?", font=self.art.title, fill=(150, 156, 170), anchor="mm")
            cards[key] = im
        return cards[key], _since(t, self.opens + [self.mine_at], 0.2)


# --------------------------------------------------------------------------- stop 7: the End
class End(Fight):
    N, NAME, NEXT, COLOUR, MINE = 7, "THE END", "", (200, 130, 255), "Trident"
    SOLO, HOOK = "Pause To Fight The Ender Dragon 🐉", "Ten hearts. Can you beat the Ender Dragon?"
    DARE, SLUG = "BEAT THE DRAGON?", "end"
    FOE, EDGES = "Ender Dragon", (25, 40)
    STAYS = True
    COUNT = ""
    DRAGON = 118

    @classmethod
    def lines(cls, before: int) -> list[tuple[str, str]]:
        hits, lost = cls.mine_hits(), -cls.mine().delta
        after = before - lost
        return [("title", "Stop seven: the End."),
                ("threat", "The Ender Dragon. Two hundred health."),
                ("pause", "Pause one last time! That is your weapon. The faster you win, the fewer hearts you lose."),
                ("mine", f"I stopped on a {cls.MINE.lower()}. {_say(hits).capitalize()} hits, and I lose {_hearts(lost)}."),
                ("count", f"If you still have a heart, you beat Minecraft. I made it with {_say(after)}."),
                ("next", "Comment your hearts.")]

    @classmethod
    def alone(cls) -> list[tuple[str, str]]:
        swap = {"threat": "It has two hundred health.",
                "pause": "Pause the video now! That is your weapon. The faster you win, the fewer hearts you lose.",
                "count": f"The dragon is down. I kept {_hearts(cls.after())}. How about you?"}
        return [(role, swap.get(role, text)) for role, text in super().alone()]

    def build(self) -> None:
        from PIL import ImageDraw

        rng = self.rng
        self.total += 1.2  # the last words stay on screen
        self.far, self.ground = _blank(), _blank()
        d = ImageDraw.Draw(self.far)
        self.crystals = []
        for i, x in enumerate(range(20, STRIP, 120)):
            tall = 70 + int(_noise(i, 0, 111) * 44)
            for px in range(x, x + 24):
                for py in range(GROUND - tall, GROUND):
                    n = _noise(px // 2, py // 3, 113)
                    d.point((px, py), fill=_shade((34, 22, 54), (0.7 if px > x + 17 else 1.0) * (0.8 + 0.5 * n)))
            d.rectangle([x + 4, GROUND - tall - 4, x + 19, GROUND - tall - 1], fill=(60, 60, 70))
            self.crystals.append((x + 12, GROUND - tall - 12))
        _fill(self.ground, GROUND, LH, (220, 222, 164), 115)
        self.sky_end = _sky((10, 4, 20), (46, 22, 70))
        d = ImageDraw.Draw(self.sky_end)
        for _ in range(70):
            x, y = rng.randrange(LW), rng.randrange(40, GROUND - 10)
            d.point((x, y), fill=_shade((150, 110, 200), 0.5 + rng.random() * 0.6))

    def plan(self) -> None:
        super().plan()
        self.quakes = [self.dead + 0.6]
        self.sounds += [(self.dead + 0.6, "win")]
        self.gone = self.dead + 0.9

    def backdrop(self, t: float, scroll: float):
        from PIL import ImageDraw

        sky = self.sky_end.copy()
        sky.alpha_composite(_wrap(self.far, scroll * 0.5))
        d = ImageDraw.Draw(sky)
        for cx, cy in self.crystals:
            x = int((cx - scroll * 0.5) % STRIP)
            if x < LW + 10:
                beat = 1 if math.sin(t * 5 + cx) > 0 else 0
                d.rectangle([x - 5 - beat, cy - 5 - beat, x + 4 + beat, cy + 4 + beat], fill=(250, 150, 240))
                d.rectangle([x - 2, cy - 2, x + 1, cy + 1], fill=(255, 236, 255))
        sky.alpha_composite(_wrap(self.ground, scroll))
        return sky

    def at(self, c) -> tuple[float, float]:
        t = c.t
        come = 1 - _ease((t - self.threat) / 0.9)
        dive = 14 * math.sin(math.pi * c.bite)
        return self.DRAGON + c.shift + 90 * come - dive + 5 * c.hit, GROUND - 66 + 6 * math.sin(t * 2.2) - 30 * come + dive * 0.5

    def scene(self, c) -> None:
        t, d = c.t, c.d
        if t >= self.act:
            c.hx += 4 * c.hit
        if self.threat <= t < self.gone:
            x, y = self.at(c)
            dying = max(0.0, (t - self.dead) / 0.9)
            c.sprites.append((_dragon(t, max(c.hit * 0.7, min(1.0, dying * 1.4))), (int(x) - 64, int(y - 56 - 20 * dying))))
            c.du.ellipse([x - 26, GROUND - 2, x + 26, GROUND + 3], fill=(0, 0, 0, 70))
            if t >= self.dead:  # the light breaks out of it
                for i in range(8):
                    a = i * math.pi / 4 + t
                    d.line([(x, y - 20 * dying), (x + math.cos(a) * 150 * dying, y - 20 * dying + math.sin(a) * 150 * dying)],
                           fill=(240, 200, 255, 200), width=2)
            for at, _ in self.hurts:  # its breath
                k = (t - at + 0.3) / 0.3
                if 0 <= k < 1.4:
                    for i in range(8):
                        px = x - 46 + (c.hx + 8 - x + 46) * min(1.0, k) * (i + 1) / 8
                        py = y - 8 + (GROUND - 40 - y + 8) * min(1.0, k) * (i + 1) / 8
                        d.rectangle([px - 2, py - 2 + i % 2, px + 2, py + 2], fill=(200, 80, 255, int(230 * (1 - max(0.0, k - 1) / 0.4))))
        if self.gone <= t < self.gone + 1.4:
            k = (t - self.gone) / 1.4
            for px, py, pr in self.puff:
                x, y = self.DRAGON + px * 70 * k, GROUND - 80 + py * 50 * k + 60 * k * k
                d.rectangle([x, y, x + 3, y + 3], fill=(*_mix((200, 110, 255), (255, 255, 255), pr), int(255 * (1 - k))))
        if c.hit > 0.3 and t < self.gone:
            d.arc([c.hx + 2, GROUND - 84, c.hx + 62, GROUND - 14], -70, 50, fill=(255, 255, 255, int(255 * c.hit)), width=3)

    def jump(self, t: float) -> float:
        return 7 * abs(math.sin((t - self.gone) * 7)) if t >= self.gone + 0.3 else 0.0

    def sharp(self, c, img, d) -> None:
        t = c.t
        if self.threat + 0.5 <= t < self.gone:
            d.rectangle([140, 292, W - 140, 330], fill=(12, 12, 16, 255))
            health = self.health(t)
            if health > 0:
                d.rectangle([146, 298, 146 + int((W - 292) * health), 324], fill=(190, 60, 230, 255))
            d.text((W // 2, 310), "ENDER DRAGON", font=self.art.tiny, fill=(255, 255, 255), anchor="mm")
        if t < self.gone:
            self.blows(t, d, (self.DRAGON - 20) * S, (GROUND - 104) * S)

    def sky(self, t: float, d) -> None:
        art = self.art
        if t >= self.next:
            _label(d, (W // 2, 420), "COMMENT", art.title, (255, 255, 255), 12)
            _label(d, (W // 2, 590), "YOUR HEARTS", art.title, (255, 222, 0), 12)
        elif t >= self.count:
            _label(d, (W // 2, 420), "DRAGON DOWN!" if self.solo else "STILL ALIVE?", art.title, (255, 255, 255), 12)
            won = "HOW MANY HEARTS?" if self.solo else "YOU BEAT MINECRAFT"
            _label(d, (W // 2, 590), won, art.fit(d, won, 980), (255, 222, 0), 12)
        else:
            super().sky(t, d)

    def card(self, t: float):
        if t >= self.go and not self.solo:
            return self.art.road(8), 0.0
        return super().card(t)


# --------------------------------------------------------------------------- the whole road
STOPS = (Forest, Cave, Village, Nether, Fortress, Stronghold, End)
TITLE = "Can You Beat Minecraft? Pause Edition"
TITLE_CARD = "CAN YOU BEAT MINECRAFT? - PAUSE EDITION"
DESCRIPTION = ("The whole road to the Ender Dragon in one Short: seven stops, ten hearts. Pause when it says so: "
               "what you stop on is what you get. Count your hearts on your fingers and comment how many you have left.")
HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#pausechallenge", "#enderdragon"]
PINNED = "How many hearts did you finish with? 👇\nI made it with {hearts}."
PREFIX = "quest_"  # the videos' file names in output/shorts


def run() -> list[int]:
    """The narrator's hearts before every stop, and after the last one."""
    out = [HEARTS]
    for stop in STOPS:
        out.append(max(0, min(HEARTS, out[-1] + stop.mine().delta)))
    return out


def solos() -> list[type]:
    """The stops that are also a Short of their own (the village is not: at ten hearts its food gives nothing)."""
    return [stop for stop in STOPS if stop.SOLO]


def _stops(only: int = 0) -> tuple:
    """The whole road, or stop number `only` alone."""
    if not only:
        return STOPS
    stop = next((s for s in solos() if s.N == only), None)
    if stop is None:
        raise ValueError(f"Stop {only} is not a Short of its own")
    return (stop,)


def scripts(only: int = 0) -> list[list[tuple[str, str]]]:
    """Every stop's sentences by role, written for the narrator's own run."""
    if only:
        return [_stops(only)[0].alone()]
    return [stop.lines(before) for stop, before in zip(STOPS, run())]


class Quest:
    """The seven stops one after the other, with the HUD, the cards and the captions on top."""

    def __init__(self, settings: Settings, tracks: list[list[Word]], only: int = 0):
        self.art, self.only = Art(settings), only
        self.stops = [cls(self.art, lines, words) for cls, lines, words in zip(_stops(only), scripts(only), tracks)]
        for stop in self.stops:
            stop.solo = bool(only)
        self.starts, at = [], 0.0
        for stop in self.stops:
            self.starts.append(at)
            at += stop.total
        self.total = at
        self.hurts = sorted((t0 + x, dl) for t0, stop in zip(self.starts, self.stops) for x, dl in stop.hurts)
        self.sounds = sorted((t0 + x, kind) for t0, stop in zip(self.starts, self.stops) for x, kind in stop.sounds)
        self.sounds += [(t0, "whoosh") for t0 in self.starts[1:]]
        self.changes, hearts = [], HEARTS  # (time, hearts before, hearts after)
        for x, dl in self.hurts:
            after = max(0, min(HEARTS, hearts + dl))
            self.changes.append((x, hearts, after))
            hearts = after
        self.final = hearts

    def hearts(self, t: float) -> int:
        out = HEARTS
        for x, _, after in self.changes:
            if x <= t:
                out = after
        return out

    def hud(self, t: float, stop: Stop, d) -> None:
        d.rectangle([0, 0, W, 268], fill=(16, 18, 26, 255))
        d.rectangle([0, 268, W, 276], fill=(255, 196, 0, 255))
        head = f"{stop.TAG}  ·  {stop.NAME}" if self.only else f"STOP {stop.N} OF 7  ·  {stop.NAME}"
        d.text((W // 2, 150), head, font=self.art.head, fill=(255, 255, 255), anchor="mm")
        now = self.hearts(t)
        fresh = [(x, a, b) for x, a, b in self.changes if 0 <= t - x < 0.35]
        for i in range(HEARTS):
            colour, shine, dy = ((226, 30, 40), True, 0) if i < now else ((58, 60, 74), False, 0)
            for x, a, b in fresh:
                if min(a, b) <= i < max(a, b):
                    blink = int((t - x) * 16) % 2 == 0
                    colour = (255, 255, 255) if blink else ((226, 30, 40) if b > a else (58, 60, 74))
                    dy = -int(10 * (1 - (t - x) / 0.35))
            _heart(d, 96 + i * 90, 190 + dy, 10, colour, shine)

    def captions(self, t: float, stop: Stop, d) -> None:
        group = None
        for g in stop.groups:
            if g[0].start <= t:
                group = g
        if group is None or t > group[-1].end + 0.5:
            return
        texts = [w.text.upper() for w in group]
        font = self.art.fit(d, " ".join(texts), 1000, "Poppins-Black", 78)
        gap = d.textlength(" ", font=font)
        x = W / 2 - (sum(d.textlength(x, font=font) for x in texts) + gap * (len(texts) - 1)) / 2
        for w, text in zip(group, texts):
            said = w.start <= t
            now = said and (t < w.end or w is [v for v in group if v.start <= t][-1])
            d.text((x, 1640), text, font=font, fill=(255, 222, 0) if now else (255, 255, 255), anchor="lm",
                   stroke_width=9, stroke_fill=(10, 10, 14))
            x += d.textlength(text, font=font) + gap

    def frame(self, at: float):
        from PIL import Image, ImageDraw

        i = max(j for j, t0 in enumerate(self.starts) if t0 <= at)
        stop, t = self.stops[i], at - self.starts[i]
        img = stop.frame(t)
        dark = 0.0
        if i and t < FADE:
            dark = 1 - t / FADE
        elif i < len(self.stops) - 1 and t > stop.total - FADE:
            dark = (t - stop.total + FADE) / FADE
        if dark > 0:
            img.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, int(255 * min(1.0, dark)))))
        d = ImageDraw.Draw(img)
        self.hud(at, stop, d)
        stop.sky(t, d)
        card, slam = stop.card(t)
        if slam > 0:
            k = 1 + 0.08 * slam
            card = card.resize((int(960 * k), int(410 * k)), Image.BILINEAR)
        x, y = (W - card.width) // 2, 1100 + (410 - card.height) // 2
        d.rectangle([x + 12, y + 14, x + card.width + 12, y + card.height + 14], fill=(0, 0, 0, 120))
        img.alpha_composite(card, (x, y))
        self.captions(t, stop, d)
        return img.convert("RGB")


def _effects(sounds: list[tuple[float, str]], total: float, path: Path, rate: int = 22050) -> None:
    """Hits, hurts, heals, booms and beeps, made here (no files needed)."""
    rng = random.Random(4)
    samples = array.array("h", bytes(2 * int(total * rate)))
    tone = lambda f, s: math.sin(2 * math.pi * f * s)  # noqa: E731
    square = lambda f, s: 1.0 if math.sin(2 * math.pi * f * s) > 0 else -1.0  # noqa: E731
    kinds = {
        "hit": (0.12, lambda s, k: (rng.uniform(-1, 1) * 0.4 + tone(110, s) * 0.45) * (1 - k) ** 2),
        "hurt": (0.22, lambda s, k: square(520 - 280 * k, s) * 0.26 * (1 - k)),
        "heal": (0.3, lambda s, k: tone(660 if k < 0.5 else 990, s) * 0.3 * (1 - k)),
        "boom": (0.5, lambda s, k: (rng.uniform(-1, 1) * 0.3 + tone(58, s) * 0.6) * (1 - k) ** 2),
        "blip": (0.09, lambda s, k: tone(880, s) * 0.28 * (1 - k)),
        "beep": (0.16, lambda s, k: square(660, s) * 0.2 * (1 - k)),
        "whoosh": (0.4, lambda s, k: rng.uniform(-1, 1) * 0.16 * math.sin(math.pi * k) ** 2),
        "win": (0.9, lambda s, k: square((523, 659, 784, 1047, 1047, 1047)[min(5, int(k * 6))], s) * 0.2 * (1 - k * 0.7)),
    }
    for at, kind in sounds:
        length, wave_at = kinds[kind]
        start = int(at * rate)
        for i in range(int(length * rate)):
            if 0 <= start + i < len(samples):
                v = samples[start + i] + int(wave_at(i / rate, i / (length * rate)) * 32767)
                samples[start + i] = max(-32767, min(32767, v))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())


# --------------------------------------------------------------------------- the video
_QUEST: Quest | None = None


def _start(settings: Settings, tracks: list[list[Word]], only: int = 0) -> None:
    global _QUEST
    _QUEST = Quest(settings, tracks, only)


def _draw(i: int) -> bytes:
    return _QUEST.frame(i / FPS).tobytes()


def voices(settings: Settings, folder: Path, rate: str = "+5%", only: int = 0) -> tuple[list[Path], list[list[Word]]]:
    """Every stop read on its own, so each stop's cues are its own."""
    from . import voiceover

    folder.mkdir(parents=True, exist_ok=True)
    mp3s, tracks = [], []
    for n, lines in enumerate(scripts(only), 1):
        mp3 = folder / f"stop{n}.mp3"
        tracks.append(voiceover.synthesize(" ".join(text for _, text in lines), mp3, settings.tts_voice, rate))
        mp3s.append(mp3)
    return mp3s, tracks


def needed() -> set[str]:
    """Every picture the road asks for by name (the pause Shorts' libraries hold them)."""
    return {o.icon for stop in STOPS for o in stop.options() if o.icon and not o.icon.startswith("tile:")} | {"Bow", "Chest"}


def missing(settings: Settings) -> list[str]:
    from . import dungeon

    return sorted(needed() - set(dungeon.pictures(settings)))


def _music(settings: Settings):
    """A chill track of this machine's own (track, its file), or (None, None): the video goes out without music."""
    from . import music

    try:
        track = music.pick_by_mood(settings, "chill_lofi")
        path = music.track_path(settings, track)
        return (track, path) if path.is_file() else (None, None)
    except Exception as err:  # noqa: BLE001 - no library, no network: the voice and the effects carry it
        logger.warning("Quest: no music (%s)", err)
        return None, None


def render(settings: Settings, output: Path, workers: int = 6, music: float = 0.1, progress=None, only: int = 0):
    """The whole quest (or stop `only` alone) as an MP4: the narrations, the drawn frames, the effects, quiet
    music. Returns the music's track (None without music)."""
    import multiprocessing

    output.parent.mkdir(parents=True, exist_ok=True)
    work = settings.output_dir / "quest" / output.stem
    mp3s, tracks = voices(settings, work, only=only)
    quest = Quest(settings, tracks, only)
    if quest.total >= 180:
        raise ValueError(f"The quest runs {quest.total:.1f} s: a Short must stay under 180")
    fx = work / "fx.wav"
    _effects(quest.sounds, quest.total, fx)
    chosen, track = _music(settings)
    silent = output.with_suffix(".silent.mp4")  # the picture first, then the sound: one ffmpeg never waits on both
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium",
                             "-crf", "18", "-pix_fmt", "yuv420p", str(silent)], stdin=subprocess.PIPE)
    frames = int(quest.total * FPS)
    try:
        with multiprocessing.get_context("spawn").Pool(workers, _start, (settings, tracks, only)) as pool:
            for i, frame in enumerate(pool.imap(_draw, range(frames), chunksize=4)):
                proc.stdin.write(frame)
                if i % 150 == 0:
                    logger.info("Quest: frame %d of %d", i, frames)
                    if progress:
                        progress(0.05 + 0.9 * i / frames, "Drawing the road")
    except BaseException:
        proc.kill()  # a frame that fails must not leave ffmpeg waiting on the pipe
        raise
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed on the quest's picture")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent)]
    for mp3 in mp3s:
        cmd += ["-i", str(mp3)]
    cmd += ["-i", str(fx)]
    n = len(mp3s)
    parts = [f"[{i + 1}:a]adelay={int(t0 * 1000)}:all=1[v{i}]" for i, t0 in enumerate(quest.starts)]
    parts.append(f"[{n + 1}:a]volume=0.8[fx]")
    mixed = "".join(f"[v{i}]" for i in range(n)) + "[fx]"
    if track:
        cmd += ["-stream_loop", "-1", "-i", str(track)]
        parts.append(f"[{n + 2}:a]volume={music},afade=t=in:d=1,afade=t=out:st={quest.total - 2.5:.2f}:d=2.5[m]")
        mixed += "[m]"
    parts.append(f"{mixed}amix=inputs={n + (2 if track else 1)}:normalize=0[a]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
            "-b:a", "160k", "-t", f"{quest.total:.3f}", "-movflags", "+faststart", str(output)]
    if subprocess.run(cmd).returncode != 0:
        raise RuntimeError("ffmpeg failed on the quest's sound")
    silent.unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    return chosen


def texts(only: int = 0) -> dict:
    """What YouTube shows: the long video's own texts, or a stop's as a Short of its own."""
    if not only:
        return {"title": TITLE, "card": TITLE_CARD, "description": DESCRIPTION,
                "pinned": PINNED.format(hearts=run()[-1])}
    stop = _stops(only)[0]
    name = stop.SOLO.rsplit(" ", 1)[0]  # the title without its emoji
    return {"title": stop.SOLO, "card": name.upper(),
            "description": f"{stop.HOOK} Pause when it says so: what you stop on is what you get. "
                           f"Comment how many hearts you have left.\n\nThe whole road to the Ender Dragon: \"{TITLE}\" "
                           "on the channel.",
            "pinned": f"How many hearts do you have left? 👇\nI kept {stop.after()}. "
                      f"The whole road, seven stops: \"{TITLE}\" on the channel."}


def record(settings: Settings, output: Path, track=None, only: int = 0):
    """The video's entry in output/shorts (the Upload and Plan tabs read it): its texts, the music's credit."""
    from . import shorts
    from .video_assembler import media_duration

    credit, text = track.credit if track else "", texts(only)
    short = shorts.ShortVideo(
        filename=output.name, game="Minecraft", mode="gamer", title=text["title"],
        description=f"{text['description']}\n\n{credit}".strip(), hashtags=HASHTAGS,
        script=" ".join(t for lines in scripts(only) for _, t in lines), voice=settings.tts_voice,
        duration_seconds=round(media_duration(output), 2), background="", credit=credit,
        music_title=track.title if track else "", music_url=track.url if track else "", title_card=text["card"],
        end_card="Comment your hearts", pinned_comment=text["pinned"],
        channel_handle=settings.channel_handle,
    )
    output.with_suffix(".json").write_text(short.model_dump_json(indent=2), encoding="utf-8")
    return short


def videos(settings: Settings) -> list:
    from . import shorts

    return [v for v in shorts.list_shorts(settings) if v.filename.startswith(PREFIX)]


def _make(settings: Settings, output: Path, progress, only: int = 0) -> None:
    """Draw it in a process of its own (the frames are drawn on several cores), reading its progress lines."""
    proc = subprocess.Popen([sys.executable, "-m", "trendclip.quest", str(output), str(only)], cwd=Path(__file__).resolve().parent.parent,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    last = ""
    for line in proc.stdout:
        last = line.strip() or last
        if " frame " in line:
            try:
                i, n = (int(x) for x in line.rsplit(" frame ", 1)[1].split(" of "))
                progress(0.05 + 0.9 * i / n, "Drawing the road")
            except ValueError:
                pass
    if proc.wait() != 0 or not output.with_suffix(".json").is_file():
        raise RuntimeError(f"The quest could not be made: {last[:300] or 'no output'}")


def create(settings: Settings, progress=lambda frac, msg: None, only: int = 0):
    """The quest (or stop `only` alone) as a video in output/shorts, waiting in Upload like any other Short."""
    from . import shorts

    if lost := missing(settings):
        raise ValueError(f"No picture of {', '.join(lost[:6])}: make a pause Short first, it fetches the pictures")
    stops = _stops(only)  # an unknown stop stops here
    progress(0.02, "Reading the stop" if only else "Reading the seven stops")
    name = f"{stops[0].SLUG}_" if only else ""
    output = settings.shorts_dir / f"{PREFIX}{name}{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    _make(settings, output, progress, only)
    return shorts.get_short(settings, output.name)


def main(argv: list[str]) -> int:
    from .config import get_settings

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    settings, output, only = get_settings(), Path(argv[0]), int(argv[1]) if len(argv) > 1 else 0
    workers = max(2, min(6, (os.cpu_count() or 4) - 2))
    record(settings, output, render(settings, output, workers=workers, only=only), only)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
