"""Price Tag Why's set pieces (2026-11-08): a whole scene drawn as the story's own thing, not the city street.

The owner asked for variety ("one template for every video and people get bored"): a Monopoly story should show a
board game moving, a McDonald's story the arches. Each set here is drawn in code in the world look (flat colours,
dark outlines, Poppins), recognisable but never a copy of a company's artwork, and reusable by any story of its kind
(the board for any board-game story). A world scene picks one with "set": {"draw": <name>, ...}; the set replaces
the street behind, and the scene's head lines, tags, big figure and captions are drawn on top as usual.

    board   a board game seen from above on a wooden table: a token hops square by square (passing GO pays
            "+$200"), the dice keep rolling, little houses pop onto the streets.
            keys: title (the words on the board, "MONOPOLY"), houses (the cue word the houses pop on),
                  start (the square the token starts on, 0-39; GO is 0)

A set is a class built with (opts, scene, words) giving draw(t) -> the RGB frame behind, and sounds() -> the
(time, sound, 0.0) of its own moments for bw_render's sound effects.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from modules import bw_render
from modules.bw_render import H, W, Scene
from modules.pw_world import GOLD, GREEN, INK, RED, SS, WHITE, big_text, cue_time, ease, font, pill, put, racing, shrink

# --------------------------------------------------------------------------- the board game

BOARD = 880  # on screen, square
BOARD_Y = 925  # its centre: under a head line, above the captions
CREAM = (214, 236, 214)
STREETS = {  # square -> the colour strip of its street group (the classic 40-square loop, GO at 0)
    1: (140, 84, 60), 3: (140, 84, 60), 6: (150, 214, 240), 8: (150, 214, 240), 9: (150, 214, 240),
    11: (220, 80, 170), 13: (220, 80, 170), 14: (220, 80, 170), 16: (246, 150, 40), 18: (246, 150, 40),
    19: (246, 150, 40), 21: RED, 23: RED, 24: RED, 26: (250, 226, 60), 27: (250, 226, 60), 29: (250, 226, 60),
    31: (40, 170, 90), 32: (40, 170, 90), 34: (40, 170, 90), 37: (40, 80, 170), 39: (40, 80, 170),
}
CHANCE = (7, 22, 36)
CHEST = (2, 17, 33)
RAILS = (5, 15, 25, 35)
HOP = 0.36  # seconds per square


def square_box(k: int, size: float) -> tuple[float, float, float, float, str]:
    """(x0, y0, x1, y1, side) of square k on a board `size` wide: corners are 1.6 units, the rest 1 (12.2 units a side)."""
    u = size / 12.2
    c = 1.6 * u
    k %= 40
    side, n = divmod(k, 10)
    if n == 0:
        x0, y0 = ((size - c, size - c), (0, size - c), (0, 0), (size - c, 0))[side]
        return x0, y0, x0 + c, y0 + c, "corner"
    off = c + (n - 1) * u
    if side == 0:  # the bottom row, right to left
        return size - off - u, size - c, size - off, size, "bottom"
    if side == 1:  # the left column, bottom to top
        return 0, size - off - u, c, size - off, "left"
    if side == 2:  # the top row, left to right
        return off, 0, off + u, c, "top"
    return size - c, off, size, off + u, "right"  # the right column, top to bottom


def square_center(k: float, size: float) -> tuple[float, float]:
    """The middle of square k (a fraction slides between two squares)."""
    a = square_box(int(math.floor(k)), size)
    b = square_box(int(math.floor(k)) + 1, size)
    f = k - math.floor(k)
    ca = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
    cb = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    return ca[0] + (cb[0] - ca[0]) * f, ca[1] + (cb[1] - ca[1]) * f


@lru_cache(maxsize=4)
def board_image(title: str) -> Image.Image:
    """The board itself (RGBA, BOARD wide): the loop of squares, the corners, the title on a red banner."""
    s = BOARD * SS
    img = Image.new("RGBA", (s + 40 * SS, s + 40 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    o = 20 * SS
    d.rounded_rectangle([o + 10 * SS, o + 16 * SS, o + s + 10 * SS, o + s + 16 * SS], radius=18 * SS, fill=(0, 0, 0, 90))
    d.rounded_rectangle([o, o, o + s, o + s], radius=18 * SS, fill=CREAM, outline=INK, width=8 * SS)
    lw = 3 * SS
    u = s / 12.2
    for k in range(40):
        x0, y0, x1, y1, side = square_box(k, s)
        x0, y0, x1, y1 = x0 + o, y0 + o, x1 + o, y1 + o
        d.rectangle([x0, y0, x1, y1], outline=INK, width=lw)
        strip = STREETS.get(k)
        band = u * 0.32
        if strip:
            box = {"bottom": [x0, y0, x1, y0 + band], "top": [x0, y1 - band, x1, y1],
                   "left": [x1 - band, y0, x1, y1], "right": [x0, y0, x0 + band, y1]}[side]
            d.rectangle(box, fill=strip, outline=INK, width=lw)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if k in CHANCE:
            d.text((cx, cy), "?", font=font(int(u * 0.62), "Black"), fill=(236, 120, 40), anchor="mm",
                   stroke_width=2 * SS, stroke_fill=INK)
        elif k in CHEST:
            d.rounded_rectangle([cx - u * 0.26, cy - u * 0.18, cx + u * 0.26, cy + u * 0.2], radius=4 * SS,
                                fill=(70, 130, 220), outline=INK, width=lw)
            d.line([(cx - u * 0.26, cy - u * 0.02), (cx + u * 0.26, cy - u * 0.02)], fill=INK, width=lw)
        elif k in RAILS:
            d.rounded_rectangle([cx - u * 0.3, cy - u * 0.14, cx + u * 0.3, cy + u * 0.14], radius=5 * SS, fill=INK)
            for wx in (-0.18, 0.18):
                d.ellipse([cx + u * (wx - 0.08), cy + u * 0.08, cx + u * (wx + 0.08), cy + u * 0.24], fill=(90, 90, 100))
    # the corners: GO (an arrow), JAIL (bars), FREE PARKING (a car), GO TO JAIL (a whistle hand)
    gx0, gy0, gx1, gy1, _ = square_box(0, s)
    gx0, gy0, gx1, gy1 = gx0 + o, gy0 + o, gx1 + o, gy1 + o
    d.text(((gx0 + gx1) / 2, (gy0 + gy1) / 2 - u * 0.15), "GO", font=font(int(u * 0.7), "Black"), fill=RED, anchor="mm",
           stroke_width=2 * SS, stroke_fill=INK)
    ax, ay = (gx0 + gx1) / 2, gy1 - u * 0.3
    d.polygon([(ax + u * 0.5, ay - u * 0.1), (ax - u * 0.25, ay - u * 0.1), (ax - u * 0.25, ay - u * 0.22),
               (ax - u * 0.55, ay), (ax - u * 0.25, ay + u * 0.22), (ax - u * 0.25, ay + u * 0.1), (ax + u * 0.5, ay + u * 0.1)],
              fill=RED, outline=INK, width=lw)
    jx0, jy0, jx1, jy1, _ = square_box(10, s)
    jx0, jy0, jx1, jy1 = jx0 + o, jy0 + o, jx1 + o, jy1 + o
    d.rectangle([jx0 + u * 0.55, jy0 + u * 0.05, jx1 - u * 0.05, jy1 - u * 0.55], fill=(246, 150, 40), outline=INK, width=lw)
    for k in range(4):
        x = jx0 + u * 0.7 + k * u * 0.22
        d.line([(x, jy0 + u * 0.12), (x, jy1 - u * 0.62)], fill=INK, width=4 * SS)
    d.text(((jx0 + jx1) / 2 - u * 0.1, jy1 - u * 0.28), "JAIL", font=font(int(u * 0.3), "Black"), fill=INK, anchor="mm")
    fx0, fy0, fx1, fy1, _ = square_box(20, s)
    fx0, fy0, fx1, fy1 = fx0 + o, fy0 + o, fx1 + o, fy1 + o
    cx, cy = (fx0 + fx1) / 2, (fy0 + fy1) / 2
    d.rounded_rectangle([cx - u * 0.5, cy - u * 0.05, cx + u * 0.5, cy + u * 0.3], radius=8 * SS, fill=RED, outline=INK, width=lw)
    d.rounded_rectangle([cx - u * 0.28, cy - u * 0.3, cx + u * 0.25, cy + u * 0.02], radius=8 * SS, fill=RED, outline=INK, width=lw)
    for wx in (-0.3, 0.3):
        d.ellipse([cx + u * (wx - 0.13), cy + u * 0.18, cx + u * (wx + 0.13), cy + u * 0.44], fill=INK)
    d.text((cx, fy0 + u * 0.28), "FREE", font=font(int(u * 0.3), "Black"), fill=INK, anchor="mm")
    px0, py0, px1, py1, _ = square_box(30, s)
    px0, py0, px1, py1 = px0 + o, py0 + o, px1 + o, py1 + o
    cx, cy = (px0 + px1) / 2, (py0 + py1) / 2
    d.text((cx, cy - u * 0.22), "GO TO", font=font(int(u * 0.28), "Black"), fill=INK, anchor="mm")
    d.text((cx, cy + u * 0.16), "JAIL", font=font(int(u * 0.42), "Black"), fill=RED, anchor="mm", stroke_width=2 * SS,
           stroke_fill=INK)
    # the middle: the two card piles and the title on a slanted red banner
    mid = o + s / 2
    for (dx, dy, color, ang) in ((-0.23, -0.2, (236, 120, 40), 45), (0.23, 0.2, (70, 130, 220), 45)):
        card = Image.new("RGBA", (int(u * 2.4), int(u * 1.5)), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle([lw, lw, card.width - lw, card.height - lw], radius=10 * SS, fill=color, outline=INK, width=lw)
        cd.rounded_rectangle([u * 0.18, u * 0.18, card.width - u * 0.18, card.height - u * 0.18], radius=8 * SS,
                             outline=(255, 255, 255, 160), width=lw)
        card = card.rotate(ang, resample=Image.BICUBIC, expand=True)
        img.paste(card, (int(mid + dx * s - card.width / 2), int(mid + dy * s - card.height / 2)), card)
    if title:
        f = font(int(u * 1.15), "Black")
        l, t, r, b = f.getbbox(title)
        pad = int(u * 0.35)
        ban = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
        bd = ImageDraw.Draw(ban)
        bd.rectangle([0, 0, ban.width - 1, ban.height - 1], fill=RED, outline=INK, width=6 * SS)
        bd.rectangle([lw * 2, lw * 2, ban.width - 1 - lw * 2, ban.height - 1 - lw * 2], outline=WHITE, width=lw)
        bd.text((pad - l, pad - t), title, font=f, fill=WHITE, stroke_width=2 * SS, stroke_fill=INK)
        k = min(1.0, (s * 0.62) / ban.width)
        ban = ban.resize((int(ban.width * k), int(ban.height * k)), Image.LANCZOS).rotate(38, resample=Image.BICUBIC, expand=True)
        img.paste(ban, (int(mid - ban.width / 2), int(mid - ban.height / 2)), ban)
    return shrink(img)


@lru_cache(maxsize=2)
def table(seed: int) -> Image.Image:
    """A warm wooden table top: planks, grain lines and a dark edge (a vignette)."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (W, H), (150, 92, 54))
    d = ImageDraw.Draw(img)
    plank = 150
    for k, x in enumerate(range(-20, W + plank, plank)):
        tone = (156 + (k % 3) * 8, 96 + (k % 2) * 6, 56 + (k % 3) * 3)
        d.rectangle([x, 0, x + plank, H], fill=tone)
        d.line([(x, 0), (x, H)], fill=(110, 62, 34), width=4)
        for _ in range(9):
            gx = x + rng.uniform(14, plank - 14)
            y0 = rng.uniform(-200, H)
            d.line([(gx, y0), (gx + rng.uniform(-6, 6), y0 + rng.uniform(200, 600))], fill=(134, 80, 44), width=2)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    v = 1 - 0.55 * (((xx - W / 2) / (W * 0.9)) ** 2 + ((yy - BOARD_Y) / (H * 0.62)) ** 2)
    arr = np.asarray(img, np.float32) * np.clip(v, 0.35, 1.0)[..., None]
    return Image.fromarray(arr.astype(np.uint8))


@lru_cache(maxsize=1)
def top_hat() -> Image.Image:
    """The player's token: a silver top hat (about 90 wide)."""
    s = SS
    img = Image.new("RGBA", (110 * s, 110 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    silver, dark = (214, 218, 228), (150, 156, 172)
    d.ellipse([8 * s, 70 * s, 102 * s, 100 * s], fill=silver, outline=INK, width=5 * s)
    d.rectangle([28 * s, 14 * s, 82 * s, 84 * s], fill=silver, outline=INK, width=5 * s)
    d.ellipse([28 * s, 4 * s, 82 * s, 26 * s], fill=(236, 238, 244), outline=INK, width=5 * s)
    d.rectangle([31 * s, 60 * s, 79 * s, 72 * s], fill=dark)
    d.ellipse([28 * s, 76 * s, 82 * s, 92 * s], fill=silver)
    d.line([(40 * s, 24 * s), (40 * s, 58 * s)], fill=WHITE, width=5 * s)
    return shrink(img)


@lru_cache(maxsize=64)
def die(face: int, angle: int) -> Image.Image:
    s = SS
    img = Image.new("RGBA", (100 * s, 100 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8 * s, 8 * s, 92 * s, 92 * s], radius=16 * s, fill=WHITE, outline=INK, width=5 * s)
    spots = {1: [(50, 50)], 2: [(30, 30), (70, 70)], 3: [(28, 28), (50, 50), (72, 72)],
             4: [(30, 30), (70, 30), (30, 70), (70, 70)], 5: [(28, 28), (72, 28), (50, 50), (28, 72), (72, 72)],
             6: [(30, 26), (70, 26), (30, 50), (70, 50), (30, 74), (70, 74)]}[face]
    for x, y in spots:
        d.ellipse([(x - 8) * s, (y - 8) * s, (x + 8) * s, (y + 8) * s], fill=RED if face == 1 else INK)
    return shrink(img).rotate(angle, resample=Image.BICUBIC, expand=True)


@lru_cache(maxsize=1)
def house() -> Image.Image:
    s = SS
    img = Image.new("RGBA", (54 * s, 54 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(27 * s, 4 * s), (50 * s, 24 * s), (50 * s, 50 * s), (4 * s, 50 * s), (4 * s, 24 * s)],
              fill=GREEN, outline=INK, width=4 * s)
    return shrink(img)


class Board:
    """A board game on a table: the token hops round the loop, dice roll, houses pop on the streets."""

    def __init__(self, opts: dict[str, Any], scene: Scene, words: list[bw_render.Word]):
        self.o, self.scene = opts, scene
        self.title = str(opts.get("title") or "")[:14].upper()
        self.start = int(opts.get("start", 31)) % 40
        self.houses_at = cue_time(scene, words, opts.get("houses"), 2) if opts.get("houses") else None
        self.streets = sorted(STREETS)[::2][:8]

    def squares(self, t: float) -> float:
        """Where the token is at t (in squares from GO, a fraction mid-hop): two quick hops then a short rest."""
        n = max(0.0, t - self.scene.start) / HOP
        whole = math.floor(n)
        f = n - whole
        return self.start + whole + ease(min(1.0, f / 0.7))

    def passes_go(self) -> list[float]:
        out, k = [], 40 - self.start
        while self.scene.start + k * HOP < self.scene.end:
            out.append(self.scene.start + k * HOP)
            k += 40
        return out

    def sounds(self) -> list[tuple[float, str, float]]:
        found = [(t, "cash", 0.0) for t in self.passes_go()]
        if self.houses_at is not None:
            found += [(self.houses_at + 0.14 * k, "pop", 0.0) for k in range(0, len(self.streets), 2)]
        return found

    def draw(self, t: float) -> Image.Image:
        img = table(3).copy()
        board = board_image(self.title).copy()
        o = 20
        bs = BOARD
        if self.houses_at is not None and t >= self.houses_at:
            for k, sq in enumerate(self.streets):
                dt = t - self.houses_at - 0.14 * k
                if dt <= 0:
                    continue
                x0, y0, x1, y1, side = square_box(sq, bs)
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                cx += {"right": -14, "left": 14}.get(side, 0)
                cy += {"bottom": -14, "top": 14}.get(side, 0)
                sc = 0.35 + 0.65 * min(1.2, 1 + 0.2 * math.sin(min(1.0, dt / 0.3) * math.pi)) * min(1.0, dt / 0.2)
                put(board, house(), (cx + o, cy + o), scale=sc)
        pos = self.squares(t)
        f = pos - math.floor(pos)
        hop = math.sin(math.pi * f) if 0 < f < 1 else 0.0
        cx, cy = square_center(pos, bs)
        shadow = Image.new("RGBA", (70, 26), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse([0, 0, 69, 25], fill=(0, 0, 0, 80))
        put(board, shadow, (cx + o + 6, cy + o + 30), scale=1 - 0.3 * hop)
        put(board, top_hat(), (cx + o, cy + o - 4 - 60 * hop), scale=0.82 + 0.18 * hop)
        tilt = -5 + 7 * ease((t - self.scene.start) / max(0.5, self.scene.end - self.scene.start))
        board = board.rotate(tilt, resample=Image.BICUBIC, expand=True)
        put(img, board, (W / 2, BOARD_Y))
        # the dice: rolling for 0.8 s every 2.4 s, then still
        local = max(0.0, t - self.scene.start)
        roll, phase = divmod(local, 2.4)
        rolling = phase < 0.8
        rng = np.random.default_rng(int(roll) * 7 + 1)
        faces = (int(rng.integers(1, 7)), int(rng.integers(1, 7)))
        for k, (dx, face) in enumerate(zip((-70, 70), faces)):
            if rolling:
                face = 1 + int(local * 14 + k * 3) % 6
                ang = int(local * 900 + k * 40) % 360 // 10 * 10
                bounce = abs(math.sin(phase / 0.8 * math.pi * 2.5)) * (1 - phase / 0.8) * 60
            else:
                ang, bounce = (-12, 16)[k], 0.0
            put(img, die(face, ang), (W / 2 - 190 + dx + (1 - min(1.0, phase / 0.8)) * 40 * (k * 2 - 1), BOARD_Y + 230 - bounce),
                scale=0.85)
        for tg in self.passes_go():
            dt = t - tg
            if 0 <= dt < 1.1:
                put(img, big_text("+$200", 84, GREEN), (W * 0.74, BOARD_Y + BOARD / 2 - 120 - 160 * ease(dt / 1.1)),
                    alpha=1 - max(0.0, (dt - 0.7) / 0.4))
        return img


# --------------------------------------------------------------------------- the factory line

BELT = 1090  # the top of the conveyor belt: things ride on it
SPACING, SPEED = 290, 240  # px between things, px a second
ITEMS = ("box", "bottle", "burger", "shoe", "can", "bag")


@lru_cache(maxsize=32)
def item(name: str, label: str = "") -> Image.Image:
    """A product riding the line (about 200 x 200, standing on its bottom edge)."""
    s = SS
    img = Image.new("RGBA", (220 * s, 230 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = 5 * s

    def B(x0, y0, x1, y1):
        return [x0 * s, y0 * s, x1 * s, y1 * s]

    if name == "bottle":
        d.rounded_rectangle(B(78, 10, 142, 50), radius=8 * s, fill=RED, outline=INK, width=lw)
        d.polygon([(84 * s, 48 * s), (136 * s, 48 * s), (160 * s, 110 * s), (160 * s, 226 * s), (60 * s, 226 * s),
                   (60 * s, 110 * s)], fill=(120, 70, 50), outline=INK, width=lw)
        d.rectangle(B(60, 130, 160, 180), fill=RED, outline=INK, width=4 * s)
        d.line([(80 * s, 70 * s), (70 * s, 120 * s)], fill=(255, 255, 255, 140), width=6 * s)
    elif name == "burger":
        d.chord(B(20, 60, 200, 200), 180, 360, fill=(236, 160, 70), outline=INK, width=lw)
        for k, (y, color) in enumerate(((128, (90, 190, 80)), (148, (250, 210, 60)), (164, (120, 70, 50)))):
            d.rounded_rectangle(B(14, y, 206, y + 22), radius=10 * s, fill=color, outline=INK, width=4 * s)
        d.rounded_rectangle(B(22, 186, 198, 226), radius=16 * s, fill=(236, 160, 70), outline=INK, width=lw)
        for x, y in ((70, 96), (110, 84), (150, 98), (92, 112)):
            d.ellipse(B(x - 5, y - 3, x + 5, y + 3), fill=(255, 240, 200))
    elif name == "shoe":
        d.polygon([(30 * s, 120 * s), (90 * s, 110 * s), (130 * s, 150 * s), (200 * s, 170 * s), (204 * s, 210 * s),
                   (26 * s, 210 * s)], fill=(70, 160, 230), outline=INK, width=lw)
        d.rectangle(B(24, 200, 206, 226), fill=WHITE, outline=INK, width=lw)
        for x in (100, 125, 150):
            d.line([(x * s, 140 * s), ((x + 16) * s, 128 * s)], fill=WHITE, width=5 * s)
    elif name == "can":
        d.rounded_rectangle(B(56, 40, 164, 226), radius=14 * s, fill=(220, 60, 60), outline=INK, width=lw)
        d.ellipse(B(56, 30, 164, 56), fill=(210, 214, 224), outline=INK, width=lw)
        d.rectangle(B(56, 110, 164, 150), fill=WHITE, outline=INK, width=4 * s)
    elif name == "bag":
        d.polygon([(40 * s, 70 * s), (180 * s, 70 * s), (196 * s, 226 * s), (24 * s, 226 * s)], fill=(250, 230, 200),
                  outline=INK, width=lw)
        d.arc(B(76, 20, 144, 110), 180, 360, fill=INK, width=6 * s)
    else:  # box: a game or product box with its name on the lid
        d.rectangle(B(14, 90, 206, 226), fill=(200, 60, 60), outline=INK, width=lw)
        d.polygon([(14 * s, 90 * s), (44 * s, 60 * s), (220 * s - 4 * s, 60 * s), (206 * s, 90 * s)], fill=(230, 90, 80),
                  outline=INK, width=lw)
        d.rectangle(B(30, 116, 190, 200), fill=WHITE, outline=INK, width=4 * s)
    if label and name in ("box", "can", "bottle", "bag"):
        box = {"box": (30, 116, 190, 200), "can": (56, 110, 164, 150), "bottle": (60, 130, 160, 180), "bag": (40, 130, 180, 190)}[name]
        size = 34
        f = font(size * s, "Black")
        while size > 12 and f.getlength(label) > (box[2] - box[0] - 14) * s:
            size -= 2
            f = font(size * s, "Black")
        d.text(((box[0] + box[2]) / 2 * s, (box[1] + box[3]) / 2 * s), label, font=f, fill=INK if name in ("box", "bag") else RED,
               anchor="mm")
    return shrink(img)


@lru_cache(maxsize=2)
def factory_wall() -> Image.Image:
    """The factory behind the line: a blue-grey wall, tall windows with light, hanging lamps, the belt's frame."""
    img = Image.new("RGB", (W, H), (96, 110, 150))
    big = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    for x in (270, 810):  # the lamps' light first: what is drawn later must not be wiped by its see-through fill
        d.polygon([((x - 66) * SS, 212 * SS), ((x + 66) * SS, 212 * SS), ((x + 260) * SS, BELT * SS), ((x - 260) * SS, BELT * SS)],
                  fill=(255, 236, 170, 40))
    for y in range(0, BELT, 90):  # wall panels
        d.line([(0, y * SS), (W * SS, y * SS)], fill=(84, 96, 136, 255), width=3 * SS)
    for x0 in (90, 600):  # windows
        d.rectangle([x0 * SS, 220 * SS, (x0 + 390) * SS, 760 * SS], fill=(196, 218, 240), outline=INK, width=6 * SS)
        for k in (1, 2):
            d.line([((x0 + 130 * k) * SS, 220 * SS), ((x0 + 130 * k) * SS, 760 * SS)], fill=INK, width=5 * SS)
        d.line([(x0 * SS, 490 * SS), ((x0 + 390) * SS, 490 * SS)], fill=INK, width=5 * SS)
    for x in (270, 810):  # lamps and their light
        d.line([(x * SS, 0), (x * SS, 150 * SS)], fill=INK, width=4 * SS)
        d.polygon([((x - 70) * SS, 210 * SS), ((x + 70) * SS, 210 * SS), ((x + 40) * SS, 150 * SS), ((x - 40) * SS, 150 * SS)],
                  fill=(60, 66, 90), outline=INK, width=4 * SS)
    d.rectangle([0, (BELT + 60) * SS, W * SS, H * SS], fill=(62, 66, 92, 255))  # the floor
    for x in range(60, W, 240):  # the line's legs
        d.rectangle([(x - 14) * SS, (BELT + 60) * SS, (x + 14) * SS, (BELT + 330) * SS], fill=(150, 156, 176), outline=INK, width=4 * SS)
    img.paste(shrink(big), (0, 0), shrink(big))
    return img


@lru_cache(maxsize=1)
def coin_sprite() -> Image.Image:
    s = SS
    img = Image.new("RGBA", (64 * s, 64 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4 * s, 4 * s, 60 * s, 60 * s], fill=GOLD, outline=INK, width=5 * s)
    d.text((32 * s, 33 * s), "$", font=font(34 * s, "Black"), fill=INK, anchor="mm")
    return shrink(img)


class Line:
    """A factory line: the product rides the belt out of a press, a counter runs, coins fly to whoever gets a cut."""

    def __init__(self, opts: dict[str, Any], scene: Scene, words: list[bw_render.Word]):
        self.scene = scene
        self.item = str(opts.get("item") or "box") if str(opts.get("item")) in ITEMS else "box"
        self.label = str(opts.get("label") or "")[:12].upper()
        self.count = str(opts.get("count") or "")
        self.count_from = str(opts.get("from") or "0")
        self.count_label = str(opts.get("count_label") or "")[:22].upper()
        self.to = str(opts.get("coins_to") or "")[:16].upper()
        self.coins_at = cue_time(scene, words, opts.get("coins"), 2) if opts.get("coins") else None

    def x_of(self, k: int, t: float) -> float:
        return -160 + k * SPACING + (t - self.scene.start + 3) * SPEED % SPACING

    def sounds(self) -> list[tuple[float, str, float]]:
        return [(self.coins_at, "cash", 0.0)] if self.coins_at is not None else []

    def draw(self, t: float) -> Image.Image:
        img = factory_wall().copy()
        d = ImageDraw.Draw(img)
        local = t - self.scene.start
        shift = local * SPEED
        d.rounded_rectangle([-20, BELT, W + 20, BELT + 60], radius=28, fill=(60, 60, 76), outline=INK, width=5)
        for k in range(-1, W // 120 + 2):  # belt marks and rollers turning with it
            x = k * 120 + shift % 120
            d.line([(x, BELT + 8), (x - 20, BELT + 52)], fill=(90, 90, 110), width=4)
        for x in range(30, W, 120):
            d.ellipse([x - 22, BELT + 8, x + 22, BELT + 52], fill=(170, 174, 190), outline=INK, width=4)
            a = shift / 22
            d.line([(x - 16 * math.cos(a), BELT + 30 - 16 * math.sin(a)), (x + 16 * math.cos(a), BELT + 30 + 16 * math.sin(a))],
                   fill=INK, width=4)
        sprite = item(self.item, self.label)
        n = W // SPACING + 3
        for k in range(n):
            x = -180 + (k * SPACING + shift) % (n * SPACING)
            bump = 4 * abs(math.sin((x / 120) * math.pi))
            put(img, sprite, (x, BELT + 4 - bump), anchor="b", scale=1.3)
            if self.coins_at is not None and t >= self.coins_at:  # every product passing the middle pays a coin
                passed = (x - W * 0.5) / SPEED
                if 0 <= passed < 0.9:
                    u = passed / 0.9
                    tx, ty = W - 190, 640
                    cx = W * 0.5 + (tx - W * 0.5) * u
                    cy = BELT - 300 + (ty - BELT + 300) * u - 260 * math.sin(math.pi * u)
                    put(img, coin_sprite(), (cx, cy), scale=1.6 - 0.5 * u)
        # the press the products come out of (on the left)
        d.rounded_rectangle([-30, BELT - 330, 200, BELT + 20], radius=16, fill=(230, 170, 60), outline=INK, width=6)
        for k in range(5):
            d.polygon([(-30 + k * 50, BELT - 330), (-6 + k * 50, BELT - 330), (-30 + k * 50 + 50, BELT - 290), (-54 + k * 50 + 50, BELT - 290)],
                      fill=INK)
        d.rectangle([150, BELT - 220, 206, BELT - 10], fill=(40, 40, 52))
        stamp_y = BELT - 300 + 40 * abs(math.sin(local * math.pi * SPEED / SPACING))
        d.rectangle([40, stamp_y, 120, stamp_y + 60], fill=(150, 156, 176), outline=INK, width=4)
        if self.to:
            put(img, pill(self.to, "gold", 52), (W - 190, 640))
        if self.count:
            u = min(1.0, local / max(0.8, self.scene.end - self.scene.start - 0.6))
            text = racing(self.count_from, self.count, u) if u < 1 else self.count
            panel_y = 520
            d.rounded_rectangle([110, panel_y - 120, W - 110, panel_y + 110], radius=28, fill=(34, 30, 50), outline=INK, width=6)
            if self.count_label:
                put(img, pill(self.count_label, "light", 34), (W / 2, panel_y - 120))
            sp = big_text(text, 120, GOLD)
            put(img, sp, (W / 2, panel_y + 12), scale=min(1.0, (W - 280) / sp.width))
        return img


# --------------------------------------------------------------------------- the newspaper

PAPER_W, PAPER_H = 860, 1040
PAPER_Y = 820
SPIN = 0.75  # seconds the paper takes to spin in


@lru_cache(maxsize=8)
def newspaper(name: str, date: str, headline: str, picture: str, label: str = "") -> Image.Image:
    """A front page: the masthead, the date line, the headline in big black letters, a photo, columns of text."""
    from modules import pw_world

    s = SS
    w, h = PAPER_W * s, PAPER_H * s
    img = Image.new("RGBA", (w + 30 * s, h + 30 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([16 * s, 18 * s, w + 16 * s, h + 18 * s], fill=(0, 0, 0, 80))
    d.rectangle([0, 0, w, h], fill=(246, 240, 224), outline=INK, width=6 * s)
    m = 40 * s
    d.text((w / 2, 90 * s), name, font=font(74 * s, "Black"), fill=INK, anchor="mm")
    d.line([(m, 150 * s), (w - m, 150 * s)], fill=INK, width=5 * s)
    d.line([(m, 196 * s), (w - m, 196 * s)], fill=INK, width=3 * s)
    d.text((m, 173 * s), date, font=font(26 * s, "Bold"), fill=INK, anchor="lm")
    d.text((w - m, 173 * s), "PRICE 2¢", font=font(26 * s, "Bold"), fill=INK, anchor="rm")
    size = 92
    words = headline.split()
    while True:
        f = font(size * s, "Black")
        lines, cur = [], ""
        for wd in words:
            test = (cur + " " + wd).strip()
            if f.getlength(test) <= w - 2 * m or not cur:
                cur = test
            else:
                lines.append(cur)
                cur = wd
        lines.append(cur)
        if (len(lines) <= 3 and max(f.getlength(x) for x in lines) <= w - 2 * m) or size <= 40:
            break
        size -= 4
    y = 230 * s
    for ln in lines:
        d.text((w / 2, y), ln, font=f, fill=INK, anchor="mt")
        y += size * 1.08 * s
    y += 24 * s
    photo_h = h - y - 70 * s
    px1 = m + (w - 2 * m) * 0.56
    d.rectangle([m, y, px1, y + photo_h], fill=(214, 206, 188), outline=INK, width=4 * s)
    pic = None
    if picture in pw_world.LOOKS:
        full = pw_world.person(picture, "smile", "hang")
        pic = full.crop((int(full.width * 0.12), int(full.height * 0.18), int(full.width * 0.88), int(full.height * 0.8)))
    elif picture in pw_world.PROPS:
        pic = pw_world.prop(picture, label)
    elif picture in ITEMS:
        pic = item(picture, label)
    if pic is not None:
        gray = pic.convert("LA").convert("RGBA")
        gray.putalpha(pic.getchannel("A"))
        pic = gray.resize((pic.width * s, pic.height * s), Image.LANCZOS)
        k = min((px1 - m - 30 * s) / pic.width, (photo_h - 30 * s) / pic.height)
        pic = pic.resize((int(pic.width * k), int(pic.height * k)), Image.LANCZOS)
        img.paste(pic, (int((m + px1) / 2 - pic.width / 2), int(y + photo_h - pic.height - 8 * s)), pic)
    for col_x in (px1 + 24 * s,):
        yy = y + 6 * s
        while yy < y + photo_h:
            d.line([(col_x, yy), (w - m, yy)], fill=(150, 146, 136), width=6 * s)
            yy += 26 * s
    yy = y + photo_h + 22 * s
    while yy < h - 24 * s:
        d.line([(m, yy), (w - m, yy)], fill=(150, 146, 136), width=6 * s)
        yy += 26 * s
    return shrink(img)


@lru_cache(maxsize=4)
def sunburst(colors: tuple[tuple[int, int, int], tuple[int, int, int]], angle: int) -> Image.Image:
    img = Image.new("RGB", (W, H), colors[0])
    d = ImageDraw.Draw(img)
    cx, cy, r = W / 2, PAPER_Y, 2400
    for k in range(24):
        if k % 2:
            continue
        a0 = math.radians(angle + k * 15)
        a1 = math.radians(angle + (k + 1) * 15)
        d.polygon([(cx, cy), (cx + r * math.cos(a0), cy + r * math.sin(a0)), (cx + r * math.cos(a1), cy + r * math.sin(a1))],
                  fill=colors[1])
    return img


class Paper:
    """A front page spinning in (the old film way) over turning rays, then landing with a thud, slowly pushing in."""

    def __init__(self, opts: dict[str, Any], scene: Scene, words: list[bw_render.Word]):
        self.scene = scene
        self.name = str(opts.get("name") or "THE DAILY NEWS")[:18].upper()
        self.date = str(opts.get("date") or scene.opts.get("year") or "")[:24]
        self.headline = str(opts.get("headline") or "")[:40].upper()
        self.picture = str(opts.get("picture") or "")
        self.label = str(opts.get("label") or "")[:12].upper()
        self.mood = "dark" if str(opts.get("mood")) == "bad" else "warm"

    def sounds(self) -> list[tuple[float, str, float]]:
        return [(self.scene.start + SPIN, "stamp", 0.0)]

    def draw(self, t: float) -> Image.Image:
        local = max(0.0, t - self.scene.start)
        colors = ((70, 40, 60), (110, 56, 80)) if self.mood == "dark" else ((236, 140, 50), (250, 180, 70))
        img = sunburst(colors, int(local * 8) % 30).copy()
        paper = newspaper(self.name, self.date, self.headline, self.picture, self.label)
        u = min(1.0, local / SPIN)
        if u < 1:
            angle = -6 + 720 * (1 - ease(u))
            scale = 0.08 + 0.92 * ease(u)
        else:
            angle = -6 + 1.5 * math.sin(local * 1.3)
            scale = 1.0 + 0.04 * min(1.0, (local - SPIN) / 4) + (0.05 * math.sin(math.pi * min(1.0, (local - SPIN) / 0.18)) if local - SPIN < 0.18 else 0)
        put(img, paper, (W / 2, PAPER_Y), scale=scale, angle=angle)
        return img


SETS: dict[str, Any] = {"board": Board, "line": Line, "paper": Paper}
HOW = {
    "board": ('{"draw": "board", "title": the game\'s name on the board (14 characters at most), "houses": a word '
              'the little houses pop on, "start": 0-39 (the token\'s square; it passes GO, "+$200", 40 - start '
              'squares later at 0.36 s a square)} = a board game seen from above on a table, a token hopping round, '
              'dice rolling (ONLY a board-game story)'),
    "line": ('{"draw": "line", "item": ' + "|".join(ITEMS) + ', "label": the name on each product (12 characters at '
             'most), "count": a figure counting up over the scene ("1,900,000,000"), "from": where it starts ("9"), '
             '"count_label": what it counts ("DRINKS A DAY", 22 characters at most), "coins": a word from which every '
             'product pays a coin, "coins_to": who gets the coins ("DARROW")} = a factory line, the product riding '
             'out of a press (making or selling millions of one thing, a cut of every sale)'),
    "paper": ('{"draw": "paper", "headline": the front page\'s headline (40 characters at most, capitals), "name": the '
              'paper\'s name ("THE DAILY NEWS"), "date": the date line ("JUNE 1935"; else the scene\'s year), '
              '"picture": a look or a prop or an item shown as the photo, "label": the words on that prop or item ("IKEA"), "mood": good|bad} = a front page spinning in '
              'over turning rays (the moment the world heard: a sale, a crash, a record, a scandal, a court case)'),
}
SET_MIN, SET_MOST, SET_SCENES = 2, 3, 5  # different sets a story uses; scenes one set may fill; set scenes in all
