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
from modules.pw_world import GOLD, GREEN, INK, RED, SS, WHITE, big_text, cue_time, ease, font, put, shrink

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


SETS: dict[str, Any] = {"board": Board}
HOW = {"board": ('{"draw": "board", "title": the game\'s name on the board (14 characters at most), "houses": a word '
                 'the little houses pop on, "start": 0-39 (the token\'s square; it passes GO, "+$200", 40 - start '
                 'squares later at 0.36 s a square)} = a board game seen from above on a table, a token hopping round, '
                 'dice rolling (any board-game story)')}
