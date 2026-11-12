"""A band's life story in under a minute: the sample look for the channel that may replace Price Tag Why (2026-11-12).

The owner pointed at Zip Story (@zipstoryyt: "<Band> in 40 Seconds", chibi cartoon members, a year card per beat,
5.3K subscribers in its first three weeks) and asked whether we can do the niche better. This look keeps what works
there (a year on every beat, the band drawn as recognisable cartoons) and adds what we know holds viewers: a narrator
with word-by-word captions, something new on screen every couple of seconds, members who play in time with the
music, crowds that bounce, and a beat-synced rock bed made here in code (no band recordings: their songs are
Content ID claimed, which costs the video its money).

A script is the usual scenes list (narration per scene) plus:
    band      {"name": "OASIS", "sub": "in 45 seconds", "members": {<name>: <look>}}
              a look: {hair: mop|short|bald|long|curly|spiky|fringe, hair_color, skin, brows: thin|thick|mono,
                       glasses: none|round|shades, beard: none|stubble|full|mustache, top: tee|parka|jacket|shirt,
                       top_color, pants, play: guitar|bass|drums|mic|none, guitar: flag|sunburst|black|red|white}
A scene (kind "band") may carry:
    set       garage | club | studio | stadium | dark | street   (sign: a neon word in the club)
    year      the big year under the title;  line: a short card under it ("KING TUT'S, GLASGOW")
    members   [name | {name, expr, play, pose, cue}] (expr: smile grin cool angry sad shocked; pose: hang up cross point)
    big       {text: "250,000", cue, color}: a figure that counts up when it is said
    news      {text: "OASIS SPLIT", cue}: a newspaper front page that spins in
    stamps    [{text, cue}];  crack: {cue}: lightning between the first two members (a fight);  confetti: true
A cue is a word of the scene's narration: the thing appears when the voice says it.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from modules import bw_render, pw_world
from modules.bw_render import FPS, H, W, Scene, Spec
from modules.pw_world import GOLD, INK, RED, SS, WHITE, back, big_text, counted, cue_time, ease, font, pill, put, shrink

BPM = 124
BEAT = 60.0 / BPM
FLOOR = 1380  # where the members' feet stand
CAPTION_Y = 1545
FADE = 0.18
POP = 0.32
SKINS = {"light": (255, 220, 190), "tan": (232, 188, 150), "brown": (176, 120, 84), "dark": (120, 80, 56)}
HAIRS = {"brown": (92, 62, 40), "dark": (44, 34, 30), "black": (24, 22, 26), "blond": (238, 200, 110),
         "red": (184, 74, 40), "grey": (170, 170, 170), "white": (240, 240, 240)}
TOPS = {"khaki": (150, 140, 90), "navy": (40, 52, 96), "black": (36, 36, 42), "white": (240, 240, 240),
        "red": (196, 48, 52), "green": (60, 120, 80), "grey": (120, 124, 132), "blue": (64, 110, 190),
        "brown": (120, 84, 56), "denim": (84, 110, 150), "yellow": (236, 196, 64)}
OUTLINE = (30, 26, 36)


SETS = ("garage", "club", "studio", "stadium", "dark", "street")
LOOK_VALUES = {"hair": ("mop", "fringe", "short", "long", "curly", "spiky", "bald"),
               "hair_color": tuple(HAIRS), "brows": ("thin", "thick", "mono"), "glasses": ("none", "round", "shades"),
               "beard": ("none", "stubble", "full", "mustache"), "top": ("tee", "parka", "jacket", "shirt"),
               "play": ("guitar", "bass", "drums", "mic", "none"), "guitar": ("flag", "sunburst", "black", "red", "white"),
               "skin": tuple(SKINS), "top_color": tuple(TOPS), "pants": tuple(TOPS)}


def rgb(table: dict, name: Any, default: str) -> tuple[int, int, int]:
    if isinstance(name, (list, tuple)):
        return tuple(int(c) for c in name)[:3]  # type: ignore[return-value]
    return table.get(str(name or default), table[default])


# --------------------------------------------------------------------------- a band member

SW, SH = 360, 640  # a member's sprite, in units (drawn at SS)


def member(look: dict, t: float, play: str, expr: str = "smile", pose: str = "hang", seed: int = 0) -> Image.Image:
    """One member at time t: a big-headed cartoon who plays their instrument in time with the beat."""
    img = Image.new("RGBA", (SW * SS, SH * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = SS

    def P(x: float, y: float) -> tuple[float, float]:
        return x * u, y * u

    def box(x0, y0, x1, y1):
        return [x0 * u, y0 * u, x1 * u, y1 * u]

    lw = int(5 * u)
    skin = rgb(SKINS, look.get("skin"), "light")
    hair = rgb(HAIRS, look.get("hair_color"), "brown")
    top = rgb(TOPS, look.get("top_color"), "navy")
    pants = rgb(TOPS, look.get("pants"), "denim")
    beat = (t + seed * 0.07) / BEAT
    hit = abs(math.sin(math.pi * beat))  # 0 on the beat, 1 between
    bob = 7 * (1 - hit) if play != "none" else 3 * math.sin(t * 2.2 + seed)
    hy = -bob  # head and body ride the beat
    drums = play == "drums"

    # legs and shoes (a drummer sits behind the kit: no legs)
    if not drums:
        for x0 in (138, 190):
            d.rounded_rectangle(box(x0, 500, x0 + 34, 606), radius=12 * u, fill=pants, outline=OUTLINE, width=lw)
            d.ellipse(box(x0 - 10, 590, x0 + 44, 626), fill=(40, 36, 44), outline=OUTLINE, width=lw)
    # arms behind the body (the singer's hands behind his back)
    sh_l, sh_r = (128, 352 + hy), (232, 352 + hy)
    arms: list[tuple[tuple[float, float], tuple[float, float]]] = []
    behind = play == "mic" and pose != "up"
    # the body
    style = str(look.get("top") or "tee")
    body_bottom = 540 if style == "parka" else 512
    d.rounded_rectangle(box(112, 318 + hy, 248, body_bottom + hy * 0.5), radius=40 * u, fill=top, outline=OUTLINE, width=lw)
    if style == "parka":
        fur = tuple(min(255, c + 70) for c in top)
        d.ellipse(box(120, 300 + hy, 240, 350 + hy), fill=fur, outline=OUTLINE, width=lw)
        d.line([P(180, 350 + hy), P(180, 535 + hy * 0.5)], fill=OUTLINE, width=int(3 * u))
        for y in (430, 480):
            d.rectangle(box(126, y + hy, 160, y + 24 + hy), outline=OUTLINE, width=int(3 * u))
            d.rectangle(box(200, y + hy, 234, y + 24 + hy), outline=OUTLINE, width=int(3 * u))
    elif style == "jacket":
        d.polygon([P(180, 330 + hy), P(150, 330 + hy), P(180, 420 + hy)], fill=(240, 240, 240))
        d.polygon([P(180, 330 + hy), P(210, 330 + hy), P(180, 420 + hy)], fill=(240, 240, 240))
        d.line([P(150, 330 + hy), P(180, 420 + hy), P(210, 330 + hy)], fill=OUTLINE, width=int(3 * u))
    elif style == "shirt":
        d.polygon([P(160, 322 + hy), P(180, 350 + hy), P(200, 322 + hy)], fill=(250, 250, 250), outline=OUTLINE)
        for y in (380, 420, 460):
            d.ellipse(box(176, y + hy, 184, y + 8 + hy), fill=OUTLINE)

    # the instrument and the arms that play it
    if play in ("guitar", "bass"):
        gx, gy = 168, 450 + hy
        neck_end = (318, 312 + hy) if play == "guitar" else (338, 286 + hy)
        d.line([P(gx + 20, gy - 10), P(*neck_end)], fill=(90, 60, 36), width=int(16 * u))
        d.rounded_rectangle(box(neck_end[0] - 10, neck_end[1] - 22, neck_end[0] + 16, neck_end[1] + 8), radius=5 * u, fill=(60, 40, 28))
        kind = str(look.get("guitar") or ("sunburst" if play == "guitar" else "black"))
        bodyc = {"sunburst": (196, 96, 40), "black": (34, 34, 40), "red": (200, 40, 48), "white": (236, 236, 236),
                 "flag": (20, 50, 140)}.get(kind, (196, 96, 40))
        gb = Image.new("RGBA", img.size, (0, 0, 0, 0))
        g = ImageDraw.Draw(gb)
        g.ellipse(box(gx - 62, gy - 30, gx + 30, gy + 58), fill=bodyc)
        g.ellipse(box(gx - 30, gy - 62, gx + 34, gy + 6), fill=bodyc)
        if kind == "flag":  # a union-flag guitar
            mask = Image.new("L", img.size, 0)
            ImageDraw.Draw(mask).ellipse(box(gx - 62, gy - 30, gx + 30, gy + 58), fill=255)
            ImageDraw.Draw(mask).ellipse(box(gx - 30, gy - 62, gx + 34, gy + 6), fill=255)
            fl = Image.new("RGBA", img.size, (0, 0, 0, 0))
            f = ImageDraw.Draw(fl)
            cx, cy = gx - 14, gy - 2
            for w, col in ((26, WHITE), (11, (210, 30, 45))):
                f.line([P(cx - 80, cy - 70), P(cx + 80, cy + 70)], fill=col, width=int(w * 0.7 * u))
                f.line([P(cx - 80, cy + 70), P(cx + 80, cy - 70)], fill=col, width=int(w * 0.7 * u))
            for w, col in ((34, WHITE), (20, (210, 30, 45))):
                f.line([P(cx - 90, cy), P(cx + 90, cy)], fill=col, width=int(w * u))
                f.line([P(cx, cy - 90), P(cx, cy + 90)], fill=col, width=int(w * u))
            gb.paste(fl, (0, 0), Image.composite(fl, Image.new("RGBA", img.size, (0, 0, 0, 0)), mask).getchannel("A"))
        g.ellipse(box(gx - 62, gy - 30, gx + 30, gy + 58), outline=OUTLINE, width=lw)
        g.ellipse(box(gx - 30, gy - 62, gx + 34, gy + 6), outline=OUTLINE, width=lw)
        g.ellipse(box(gx - 12, gy - 8, gx + 8, gy + 12), fill=(30, 26, 30))
        img.alpha_composite(gb)
        strum = 26 * math.sin(math.pi * 2 * beat)  # down and up on every beat
        arms = [(sh_l, (gx - 22, gy + 6 + strum * 0.5)), (sh_r, (neck_end[0] - 50, neck_end[1] + 30))]
    elif play == "mic":
        d.line([P(250, 620), P(250, 300 + hy)], fill=(70, 70, 80), width=int(7 * u))
        d.line([P(222, 620), P(278, 620)], fill=(70, 70, 80), width=int(9 * u))
        d.line([P(250, 300 + hy), P(214, 276 + hy)], fill=(70, 70, 80), width=int(6 * u))
        d.ellipse(box(200, 262 + hy, 224, 288 + hy), fill=(60, 60, 66), outline=OUTLINE, width=int(3 * u))
        if not behind:
            arms = [(sh_l, (120, 220 + hy)), (sh_r, (240, 220 + hy))]
    elif drums:
        stick = math.sin(math.pi * 2 * beat)
        arms = [(sh_l, (100, 430 - 30 * max(0, stick) + hy)), (sh_r, (262, 430 - 30 * max(0, -stick) + hy))]
    else:
        arms = {"up": [(sh_l, (96, 210 + hy)), (sh_r, (264, 210 + hy))],
                "point": [(sh_l, (110, 470 + hy)), (sh_r, (300, 300 + hy))],
                "cross": [(sh_l, (214, 420 + hy)), (sh_r, (146, 420 + hy))]}.get(pose, [(sh_l, (104, 486 + hy)), (sh_r, (256, 486 + hy))])
    for (a, b) in arms:
        d.line([P(*a), P(*b)], fill=OUTLINE, width=int(30 * u))
        d.line([P(*a), P(*b)], fill=top, width=int(21 * u))
        d.ellipse(box(b[0] - 17, b[1] - 17, b[0] + 17, b[1] + 17), fill=skin, outline=OUTLINE, width=lw)
        if drums:  # the sticks
            d.line([P(*b), P(b[0] + (40 if b[0] > 180 else -40), b[1] - 50)], fill=(220, 190, 130), width=int(8 * u))

    # the head
    cx, cy, r = 180, 188 + hy * 1.3, 122
    hair_style = str(look.get("hair") or "short")
    if hair_style == "long":  # long hair falls behind the head
        d.rounded_rectangle(box(cx - r - 8, cy - 40, cx + r + 8, cy + 150), radius=50 * u, fill=hair, outline=OUTLINE, width=lw)
    if hair_style == "curly":
        for k in range(14):
            a = math.pi * (0.9 + 1.2 * k / 13)
            px, py = cx + (r + 4) * math.cos(a), cy + (r - 6) * math.sin(a) - 6
            d.ellipse(box(px - 34, py - 34, px + 34, py + 34), fill=hair, outline=OUTLINE, width=lw)
    d.ellipse(box(cx - r, cy - r, cx + r, cy + r), fill=skin, outline=OUTLINE, width=lw)
    d.ellipse(box(cx - r - 14, cy + 2, cx - r + 18, cy + 44), fill=skin, outline=OUTLINE, width=lw)  # ears
    d.ellipse(box(cx + r - 18, cy + 2, cx + r + 14, cy + 44), fill=skin, outline=OUTLINE, width=lw)
    if look.get("beard") in ("stubble", "full"):
        shade = tuple(int(c * 0.82) for c in skin) if look["beard"] == "stubble" else hair
        d.chord(box(cx - r + 6, cy - r + 10, cx + r - 6, cy + r - 2), 20, 160, fill=shade)
    if hair_style in ("mop", "fringe", "short", "long", "spiky"):
        d.chord(box(cx - r - 6, cy - r - 10, cx + r + 6, cy + r - 30), 180, 360, fill=hair, outline=OUTLINE, width=lw)
        if hair_style in ("mop", "fringe", "long"):  # a fringe down over the brows, with a ragged edge
            low = cy - 30 if hair_style == "mop" else cy - 48
            pts = [P(cx - r - 4, cy - 10)]
            for k in range(9):
                x = cx - r + 2 * r * k / 8
                pts.append(P(x, low + (14 if k % 2 else 0)))
            pts.append(P(cx + r + 4, cy - 10))
            pts.append(P(cx + r + 4, cy - 60))
            pts.append(P(cx - r - 4, cy - 60))
            d.polygon(pts, fill=hair)
            d.line(pts[:-2], fill=OUTLINE, width=lw)
            if hair_style == "mop":  # sides down past the ears
                d.rounded_rectangle(box(cx - r - 12, cy - 60, cx - r + 26, cy + 40), radius=16 * u, fill=hair, outline=OUTLINE, width=lw)
                d.rounded_rectangle(box(cx + r - 26, cy - 60, cx + r + 12, cy + 40), radius=16 * u, fill=hair, outline=OUTLINE, width=lw)
        if hair_style == "spiky":
            for k in range(6):
                x = cx - r + 20 + k * (2 * r - 40) / 5
                d.polygon([P(x - 26, cy - r + 20), P(x, cy - r - 46), P(x + 26, cy - r + 20)], fill=hair, outline=OUTLINE)
    elif hair_style == "bald":
        d.arc(box(cx - 70, cy - 104, cx - 10, cy - 60), 200, 300, fill=(255, 255, 255), width=int(7 * u))
    # the face
    blink = (t + seed * 0.71) % (2.4 + seed % 3 * 0.4) < 0.12
    ey = cy + 14
    eyes = ((cx - 44, ey), (cx + 44, ey))
    glasses = str(look.get("glasses") or "none")
    for ex, yy in eyes:
        if glasses in ("round", "shades"):
            continue
        if blink:
            d.line([P(ex - 14, yy), P(ex + 14, yy)], fill=OUTLINE, width=int(6 * u))
        elif expr == "grin":
            d.arc(box(ex - 16, yy - 12, ex + 16, yy + 14), 200, 340, fill=OUTLINE, width=int(7 * u))
        else:
            rr = 11 if expr == "shocked" else 15
            d.ellipse(box(ex - rr, yy - rr - 3, ex + rr, yy + rr + 3), fill=OUTLINE)
            d.ellipse(box(ex - 6 + 3, yy - 11, ex + 1 + 3, yy - 4), fill=WHITE)
    if glasses in ("round", "shades"):
        lens = (40, 44, 60) if glasses == "shades" else (70, 120, 90)
        for ex, yy in eyes:
            d.ellipse(box(ex - 30, yy - 24, ex + 30, yy + 24), fill=lens, outline=OUTLINE, width=lw)
            d.arc(box(ex - 18, yy - 16, ex + 6, yy + 4), 190, 260, fill=(200, 220, 230), width=int(4 * u))
        d.line([P(cx - 14, ey - 4), P(cx + 14, ey - 4)], fill=OUTLINE, width=lw)
    brows = str(look.get("brows") or "thin")
    bw_ = int((14 if brows in ("thick", "mono") else 7) * u)
    by = ey - 42
    if expr == "angry":
        d.line([P(cx - 70, by - 8), P(cx - 16, by + 12)], fill=hair, width=bw_)
        d.line([P(cx + 70, by - 8), P(cx + 16, by + 12)], fill=hair, width=bw_)
    elif expr == "sad":
        d.line([P(cx - 70, by + 10), P(cx - 18, by - 6)], fill=hair, width=bw_)
        d.line([P(cx + 70, by + 10), P(cx + 18, by - 6)], fill=hair, width=bw_)
    elif brows == "mono":
        d.line([P(cx - 72, by), P(cx - 20, by + 4), P(cx + 20, by + 4), P(cx + 72, by)], fill=hair, width=bw_, joint="curve")
    else:
        lift = -10 if expr == "shocked" else 0
        d.line([P(cx - 68, by + lift), P(cx - 20, by - 4 + lift)], fill=hair, width=bw_)
        d.line([P(cx + 68, by + lift), P(cx + 20, by - 4 + lift)], fill=hair, width=bw_)
    my = cy + 70
    singing = play == "mic" and hit > 0.35
    if look.get("beard") == "mustache":
        d.chord(box(cx - 44, my - 26, cx + 44, my + 8), 180, 360, fill=hair)
    if singing or expr == "shocked":
        o = 14 + 12 * hit if singing else 18
        d.ellipse(box(cx - 18, my - o * 0.6, cx + 18, my + o), fill=(120, 30, 40), outline=OUTLINE, width=lw)
    elif expr in ("grin", "smile"):
        if expr == "grin":
            d.chord(box(cx - 40, my - 30, cx + 40, my + 30), 0, 180, fill=(120, 30, 40), outline=OUTLINE, width=lw)
        else:
            d.arc(box(cx - 32, my - 26, cx + 32, my + 16), 20, 160, fill=OUTLINE, width=int(7 * u))
    elif expr in ("angry", "sad"):
        d.arc(box(cx - 30, my, cx + 30, my + 36), 200, 340, fill=OUTLINE, width=int(7 * u))
    else:  # cool: a flat, slightly crooked line
        d.line([P(cx - 24, my + 6), P(cx + 26, my + 2)], fill=OUTLINE, width=int(7 * u))
    if expr != "angry" and expr != "sad":
        for ex in (cx - 76, cx + 76):
            d.ellipse(box(ex - 18, my - 26, ex + 18, my - 10), fill=(255, 150, 150, 110))
    return shrink(img)


def drum_kit(t: float, name: str) -> Image.Image:
    """The kit in front of the drummer: a kick drum with the band's name, a snare, two cymbals that shiver on hits."""
    w, h = 420, 300
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = SS
    hit = abs(math.sin(math.pi * t / BEAT))
    for cx, cy in ((70, 70 + 6 * (1 - hit)), (350, 80 + 6 * hit)):  # cymbals
        d.line([(cx * u, cy * u), (cx * u, 290 * u)], fill=(90, 90, 100), width=int(5 * u))
        d.ellipse([(cx - 62) * u, (cy - 12) * u, (cx + 62) * u, (cy + 12) * u], fill=(230, 196, 90), outline=OUTLINE, width=int(4 * u))
    d.rounded_rectangle([60 * u, 150 * u, 150 * u, 210 * u], radius=10 * u, fill=(220, 220, 228), outline=OUTLINE, width=int(4 * u))
    d.rounded_rectangle([270 * u, 150 * u, 360 * u, 210 * u], radius=10 * u, fill=(220, 220, 228), outline=OUTLINE, width=int(4 * u))
    s = 1 + 0.03 * (1 - hit)
    r = 108 * s
    d.ellipse([(210 - r) * u, (190 - r) * u, (210 + r) * u, (190 + r) * u], fill=(200, 40, 50), outline=OUTLINE, width=int(6 * u))
    r2 = r * 0.82
    d.ellipse([(210 - r2) * u, (190 - r2) * u, (210 + r2) * u, (190 + r2) * u], fill=(244, 240, 232), outline=OUTLINE, width=int(3 * u))
    f = font(int(46 * u), "Black")
    d.text((210 * u, 190 * u), name[:8], font=f, fill=OUTLINE, anchor="mm")
    return shrink(img)


def note_sprite(color) -> Image.Image:
    img = Image.new("RGBA", (60 * SS, 90 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4 * SS, 58 * SS, 34 * SS, 84 * SS], fill=color)
    d.line([(32 * SS, 70 * SS), (32 * SS, 8 * SS)], fill=color, width=6 * SS)
    d.line([(32 * SS, 8 * SS), (54 * SS, 24 * SS)], fill=color, width=8 * SS)
    return shrink(img)


# --------------------------------------------------------------------------- the sets


def _floor(d, wide, color, line):
    d.rectangle([0, FLOOR * SS - 20 * SS, wide * SS, H * SS], fill=color)
    d.line([(0, (FLOOR - 20) * SS), (wide * SS, (FLOOR - 20) * SS)], fill=line, width=6 * SS)


@lru_cache(maxsize=16)
def set_image(name: str, seed: int, sign: str = "") -> Image.Image:
    """The still part of a set, wider than the frame so the camera can drift."""
    wide = W + 120
    rng = np.random.default_rng(seed)
    if name == "street":
        return pw_world.backdrop("night", seed)
    big = Image.new("RGBA", (wide * SS, H * SS), (0, 0, 0, 255))
    d = ImageDraw.Draw(big)
    S = SS
    if name == "garage":
        d.rectangle([0, 0, wide * S, H * S], fill=(150, 70, 52))
        for row, y in enumerate(range(0, FLOOR, 46)):  # bricks
            d.line([(0, y * S), (wide * S, y * S)], fill=(118, 52, 40), width=4 * S)
            for x in range(-60 if row % 2 else -20, wide, 96):
                d.line([(x * S, y * S), (x * S, (y + 46) * S)], fill=(118, 52, 40), width=4 * S)
        _floor(d, wide, (120, 118, 116), (90, 88, 86))
        for x in (70, wide - 250):  # amps
            d.rounded_rectangle([x * S, (FLOOR - 230) * S, (x + 180) * S, (FLOOR - 20) * S], radius=10 * S, fill=(40, 38, 42), outline=OUTLINE, width=5 * S)
            d.rectangle([(x + 18) * S, (FLOOR - 200) * S, (x + 162) * S, (FLOOR - 50) * S], fill=(70, 66, 70))
            for gx in range(x + 26, x + 160, 16):
                d.line([(gx * S, (FLOOR - 200) * S), (gx * S, (FLOOR - 50) * S)], fill=(52, 50, 54), width=2 * S)
        d.line([(wide / 2 * S, 0), (wide / 2 * S, 600 * S)], fill=(30, 30, 30), width=4 * S)
        for k, a in ((3.2, 40), (2.0, 70), (1.0, 255)):
            r = 26 * k
            d.ellipse([(wide / 2 - r) * S, (630 - r) * S, (wide / 2 + r) * S, (630 + r) * S], fill=(255, 236, 160, a))
    elif name in ("club", "dark"):
        top, bot = ((40, 18, 56), (90, 30, 70)) if name == "club" else ((10, 10, 16), (18, 18, 26))
        yy = np.linspace(0, 1, H)[:, None, None]
        grad = np.array(top, np.float32) * (1 - yy) + np.array(bot, np.float32) * yy
        big = Image.fromarray(np.repeat(grad, wide, axis=1).clip(0, 255).astype(np.uint8)).convert("RGBA").resize((wide * S, H * S))
        d = ImageDraw.Draw(big)
        if name == "club":
            for x0, x1 in ((0, 150), (wide - 150, wide)):  # curtains
                d.rectangle([x0 * S, 0, x1 * S, FLOOR * S], fill=(150, 20, 40))
                for x in range(x0 + 20, x1, 34):
                    d.line([(x * S, 0), (x * S, FLOOR * S)], fill=(110, 12, 30), width=8 * S)
            d.rectangle([0, (FLOOR - 20) * S, wide * S, (FLOOR + 50) * S], fill=(120, 80, 50))
            d.rectangle([0, (FLOOR + 50) * S, wide * S, H * S], fill=(20, 12, 26))
            if sign:  # a neon sign: a soft pink glow under crisp letters
                from PIL import ImageFilter

                f = font(int(76 * S), "Black")
                glow = Image.new("RGBA", big.size, (0, 0, 0, 0))
                ImageDraw.Draw(glow).text((wide / 2 * S, 660 * S), sign, font=f, fill=(255, 60, 170, 255), anchor="mm",
                                          stroke_width=int(6 * S), stroke_fill=(255, 60, 170, 255))
                big.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18 * S)))
                d = ImageDraw.Draw(big)
                d.text((wide / 2 * S, 660 * S), sign, font=f, fill=(255, 205, 236), anchor="mm",
                       stroke_width=int(3 * S), stroke_fill=(255, 70, 175))
        else:
            _floor(d, wide, (16, 16, 22), (40, 40, 50))
            d.polygon([(wide / 2 * S - 90 * S, 0), (wide / 2 * S + 90 * S, 0), ((wide / 2 + 330) * S, (FLOOR + 60) * S),
                       ((wide / 2 - 330) * S, (FLOOR + 60) * S)], fill=(255, 250, 220, 46))
            d.ellipse([(wide / 2 - 330) * S, (FLOOR - 10) * S, (wide / 2 + 330) * S, (FLOOR + 110) * S], fill=(255, 250, 220, 60))
    elif name == "studio":
        d.rectangle([0, 0, wide * S, H * S], fill=(54, 64, 84))
        for y in range(80, FLOOR - 120, 120):  # foam panels
            for x in range(40, wide - 80, 120):
                d.rectangle([x * S, y * S, (x + 104) * S, (y + 104) * S], fill=(44, 52, 70))
                for k in range(4):
                    d.line([((x + 13 + 26 * k) * S, (y + 8) * S), ((x + 13 + 26 * k) * S, (y + 96) * S)], fill=(38, 46, 62), width=6 * S)
        _floor(d, wide, (90, 70, 56), (70, 52, 40))
        d.rounded_rectangle([(wide - 380) * S, 660 * S, (wide - 120) * S, 740 * S], radius=12 * S, fill=(200, 30, 40))
        d.text(((wide - 250) * S, 700 * S), "RECORDING", font=font(int(40 * S), "Black"), fill=WHITE, anchor="mm")
    elif name == "stadium":
        yy = np.linspace(0, 1, H)[:, None, None]
        grad = np.array((10, 14, 46), np.float32) * (1 - yy) + np.array((60, 20, 80), np.float32) * yy
        big = Image.fromarray(np.repeat(grad, wide, axis=1).clip(0, 255).astype(np.uint8)).convert("RGBA").resize((wide * S, H * S))
        d = ImageDraw.Draw(big)
        for _ in range(60):
            x, y, r = rng.uniform(0, wide), rng.uniform(20, 700), rng.uniform(1.5, 3)
            d.ellipse([(x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S], fill=(255, 255, 240, 160))
        d.rectangle([40 * S, 760 * S, (wide - 40) * S, 790 * S], fill=(60, 60, 70))  # the truss
        for x in range(60, wide - 40, 60):
            d.line([(x * S, 760 * S), ((x + 30) * S, 790 * S)], fill=(90, 90, 100), width=3 * S)
        for x in (40, wide - 70):
            d.rectangle([x * S, 760 * S, (x + 30) * S, FLOOR * S], fill=(60, 60, 70))
        d.rectangle([0, (FLOOR - 20) * S, wide * S, (FLOOR + 40) * S], fill=(30, 30, 38))
        d.rectangle([0, (FLOOR + 40) * S, wide * S, H * S], fill=(16, 10, 30))
    return shrink(big).convert("RGB")


def lights(img: Image.Image, t: float) -> None:
    """Stadium spotlights that sweep on the beat."""
    over = Image.new("RGBA", (W // 2, H // 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    cols = [(255, 80, 180), (80, 200, 255), (255, 220, 90), (140, 255, 140)]
    for k in range(4):
        x0 = (120 + k * 280) / 2
        a = math.sin(t * 0.9 + k * 1.7) * 0.5
        x1 = x0 + math.sin(a) * 900 / 2
        d.polygon([(x0 - 6, 395), (x0 + 6, 395), (x1 + 160, 0), (x1 - 160, 0)], fill=(*cols[k], 40))
    img.paste(over.resize((W, H), Image.BILINEAR), (0, 0), over.resize((W, H), Image.BILINEAR))


def crowd(img: Image.Image, t: float, seed: int, dense: bool) -> None:
    """Heads and raised hands bouncing on the beat along the bottom of the frame."""
    rng = np.random.default_rng(seed)
    d = ImageDraw.Draw(img)
    rows = ((1700, 40, (30, 20, 46)), (1800, 48, (20, 12, 32))) if dense else ((1790, 44, (26, 18, 40)),)
    for y0, r, col in rows:
        x = -20.0
        while x < W + 40:
            ph = rng.uniform(0, 1)
            jump = 18 * max(0.0, math.sin(math.pi * 2 * (t / BEAT + ph)))
            y = y0 - jump
            if rng.random() < 0.45:  # a raised arm
                hx = x + rng.choice([-1, 1]) * 30
                d.line([(x, y + 30), (hx, y - 90 - jump)], fill=col, width=18)
                d.ellipse([hx - 13, y - 104 - jump, hx + 13, y - 78 - jump], fill=col)
                if rng.random() < 0.3:  # a phone light
                    d.rectangle([hx - 8, y - 132 - jump, hx + 8, y - 104 - jump], fill=(250, 250, 220))
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)
            d.rectangle([x - r * 1.2, y + r * 0.6, x + r * 1.2, H], fill=col)
            x += r * 2.1 + rng.uniform(-6, 16)


# --------------------------------------------------------------------------- the renderer


class BandRenderer:
    """frame(t) for bw_render.render: the band's scenes, year cards, a caption pill and cross-fades."""

    def __init__(self, spec: Spec, band: dict[str, Any]):
        self.spec, self.band = spec, band
        pw_world.ensure_fonts()
        self.looks: dict[str, dict] = dict(band.get("members") or {})
        self.seen: set[str] = set()  # a name tag only on a member's first appearance in the video
        self.items = [self.plan(i, s) for i, s in enumerate(spec.scenes)]
        self.groups = pw_world.WorldRenderer.caption_groups(spec.words)
        spec.sfx_extra = self.sounds()

    def plan(self, i: int, scene: Scene) -> list[dict[str, Any]]:
        o, words, first = scene.opts, self.spec.words, i == 0
        items: list[dict[str, Any]] = []

        def at(cue, k):
            return -1.0 if first and not cue else cue_time(scene, words, cue, k)

        prev = self.members_of(self.spec.scenes[i - 1]) if i else []
        prev_names = {m["name"] for m in prev}
        ms = self.members_of(scene)
        n = len(ms)
        front = [m for m in ms if (m.get("play") or self.looks.get(m["name"], {}).get("play")) != "drums"]
        drummer = len(front) < n
        scale = {1: 1.0, 2: 0.98, 3: 0.92, 4: 0.8}.get(len(front), 0.7) * (0.9 if drummer and len(front) > 2 else 1.0)
        for k, m in enumerate(ms):
            look = self.looks.get(m["name"], {})
            play = str(m.get("play") or look.get("play") or "none")
            if play == "drums":
                x, s, foot = W / 2, scale * 0.8, FLOOR - 190  # on a riser behind the front row
            else:
                j = front.index(m)
                x = W * ((j + 0.5) / len(front)) if len(front) > 1 else W / 2
                x = W * 0.12 + (W * 0.76) * (j / (len(front) - 1)) if len(front) > 2 else x
                if drummer and len(front) > 1:  # the front row opens in the middle, so the kit shows
                    half = (len(front) + 1) // 2
                    side = [j, len(front) - 1 - j][j >= half]
                    span = max(1, half)
                    off = 0.14 + 0.22 * (span - 1 - side) / max(1, span - 1) if span > 1 else 0.25
                    x = W * (0.5 - off if j < half else 0.5 + off) if len(front) > 2 else W * (0.5 + (-0.27 if j == 0 else 0.27))
                s, foot = scale, FLOOR
            shown = -1.0 if (first or (m["name"] in prev_names and not m.get("cue"))) else at(m.get("cue"), k)
            items.append({"type": "member", "at": shown, "name": m["name"], "look": look, "play": play, "x": x, "s": s,
                          "foot": foot, "expr": str(m.get("expr") or "smile"), "pose": str(m.get("pose") or "hang"),
                          "seed": sum(map(ord, m["name"])) % 23, "tag": bool(m.get("tag", m["name"] not in self.seen and not m["name"].startswith("_")))})
            self.seen.add(m["name"])
        items.sort(key=lambda it: it["play"] != "drums")  # the drummer behind
        if o.get("year"):
            same = i and self.spec.scenes[i - 1].opts.get("year") == o.get("year")
            items.append({"type": "year", "at": -1.0 if first or same else scene.start + 0.05, "text": str(o["year"]), "slam": not same})
        if o.get("line"):
            items.append({"type": "line", "at": -1.0 if first else at(o.get("line_cue"), 1), "text": str(o["line"])})
        y = 620.0
        if isinstance(o.get("big"), dict):
            items.append({"type": "big", "at": at(o["big"].get("cue"), 0), "y": y + 70, "text": str(o["big"]["text"]),
                          "color": pw_world.COLORS.get(str(o["big"].get("color") or "gold"), GOLD)})
        if isinstance(o.get("news"), dict):
            items.append({"type": "news", "at": at(o["news"].get("cue"), 0), "text": str(o["news"]["text"])})
        if isinstance(o.get("crack"), dict) and n >= 2:
            items.append({"type": "crack", "at": at(o["crack"].get("cue"), 2)})
        for k, st in enumerate(o.get("stamps") or []):
            items.append({"type": "stamp", "at": at(st.get("cue"), k), "text": str(st.get("text") or "NO"), "k": k})
        return items

    def members_of(self, scene: Scene) -> list[dict[str, Any]]:
        return [m if isinstance(m, dict) else {"name": str(m)} for m in (scene.opts.get("members") or [])]

    def sounds(self) -> list[tuple[float, str, float]]:
        found: list[tuple[float, str, float]] = []
        for i, scene in enumerate(self.spec.scenes):
            if i > 0:
                found.append((scene.start, "whoosh", 0.0))
            for it in self.items[i]:
                if it["at"] < 0.05:
                    continue
                if it["type"] == "year" and it["slam"]:
                    found.append((it["at"] + 0.05, "stamp", 0.0))
                elif it["type"] in ("stamp", "crack", "news"):
                    found.append((it["at"], "stamp", 0.0))
                elif it["type"] == "big":
                    found.append((it["at"] + 0.75, "cash", 0.0))
                else:
                    found.append((it["at"], "pop", 0.0))
        return found

    def pop(self, it: dict[str, Any], t: float) -> tuple[float, float]:
        if it["at"] < 0:
            return 1.0, 1.0
        u = (t - it["at"]) / POP
        if u <= 0:
            return 0.0, 0.0
        return back(min(1.0, u)), min(1.0, u * 2)

    def scene_image(self, i: int, t: float) -> Image.Image:
        scene = self.spec.scenes[i]
        o = scene.opts
        dur = max(0.5, scene.end - scene.start)
        u = min(1.0, max(0.0, (t - scene.start) / dur))
        name = str(o.get("set") or "stage")
        name = name if name in ("garage", "club", "studio", "stadium", "dark", "street") else "club"
        base = set_image(name, (self.spec.seed or 7) + i, str(o.get("sign") or ""))
        drift = int(100 * ease(u) * (1 if i % 2 == 0 else -1))
        x0 = 60 + drift // 2
        img = base.crop((x0, 0, x0 + W, H)).copy()
        if name == "stadium":
            lights(img, t)
        items = self.items[i]
        shake = 0.0
        for it in items:
            if it["type"] == "crack" and 0 < t - it["at"] < 0.4:
                shake = 10 * (1 - (t - it["at"]) / 0.4) * math.sin(t * 90)
        for it in items:
            if it["type"] == "member":
                self.draw_member(img, it, t, shake)
        if name == "stadium" or o.get("crowd"):
            crowd(img, t, (self.spec.seed or 7) + i, dense=name == "stadium")
        elif name == "club":
            crowd(img, t, (self.spec.seed or 7) + i, dense=False)
        self.notes(img, t, items)
        for it in items:
            if it["type"] != "member":
                self.draw_item(img, it, t)
        if o.get("confetti"):
            self.confetti(img, t - scene.start)
        self.header(img)
        z = 1 + 0.03 * ease(u)
        if z > 1.002:
            zw, zh = int(W * z), int(H * z)
            img = img.resize((zw, zh), Image.BILINEAR).crop(((zw - W) // 2, (zh - H) // 2, (zw - W) // 2 + W, (zh - H) // 2 + H))
        return img

    def header(self, img: Image.Image) -> None:
        name = str(self.band.get("name") or "")
        if name:
            put(img, title_sprite(name), (W / 2, 150))
        sub = str(self.band.get("sub") or "")
        if sub:
            put(img, text_sprite(sub, 40, (235, 235, 245), "Bold"), (W / 2, 240))

    def draw_member(self, img: Image.Image, it: dict[str, Any], t: float, shake: float) -> None:
        s, a = self.pop(it, t)
        if s <= 0:
            return
        if it["play"] == "drums":
            spr = member(it["look"], t, "drums", it["expr"], it["pose"], it["seed"])
            k = it["s"] * s
            put(img, spr, (it["x"], it["foot"] - SH * k / 2 + 40 * k), scale=k, alpha=a)
            kit = drum_kit(t, str(self.band.get("name") or ""))
            put(img, kit, (it["x"], it["foot"] + 10 - kit.height * k * 0.5), scale=k * 1.1, alpha=a)
        else:
            spr = member(it["look"], t, it["play"], it["expr"], it["pose"], it["seed"])
            k = it["s"] * s
            put(img, spr, (it["x"] + shake, it["foot"] - SH * k / 2), scale=k, alpha=a)
        if it["tag"]:
            name_pill = pill(it["name"].upper(), "dark", 30)
            put(img, name_pill, (it["x"], it["foot"] + 52 if it["play"] != "drums" else it["foot"] - 640 * it["s"]), alpha=a)

    def notes(self, img: Image.Image, t: float, items: list[dict[str, Any]]) -> None:
        """Music notes float up from whoever is playing."""
        for it in items:
            if it["type"] != "member" or it["play"] in ("none",) or self.pop(it, t)[0] < 1:
                continue
            for k in range(2):
                ph = ((t / (BEAT * 4)) + k * 0.5 + it["seed"] * 0.13) % 1.0
                x = it["x"] + (90 if k else -100) * it["s"] + 30 * math.sin(ph * 6 + k)
                y = it["foot"] - 560 * it["s"] - ph * 240
                col = (255, 220, 90) if k else (255, 255, 255)
                put(img, note_sprite(col), (x, y), scale=0.8 * it["s"], alpha=max(0.0, 1 - ph) * 0.9)

    def draw_item(self, img: Image.Image, it: dict[str, Any], t: float) -> None:
        s, a = self.pop(it, t)
        if s <= 0:
            return
        kind = it["type"]
        if kind == "year":
            slam = 1.0 if it["at"] < 0 else 1 + 0.6 * max(0.0, 1 - (t - it["at"]) / 0.22)
            put(img, year_sprite(it["text"]), (W / 2, 370), scale=slam * min(1.0, s + 0.3), alpha=a)
        elif kind == "line":
            put(img, line_sprite(it["text"]), (W / 2, 505), scale=s, alpha=a)
        elif kind == "big":
            u = min(1.0, max(0.0, (t - it["at"]) / 0.75)) if it["at"] >= 0 else 1.0
            put(img, big_text(counted(it["text"], ease(u)), 150, it["color"]), (W / 2, it["y"]), scale=s, alpha=a)
        elif kind == "news":
            put(img, news_sprite(it["text"]), (W / 2, 690), scale=0.72 * s, alpha=a)
            if it["at"] >= 0 and t - it["at"] < POP * 1.6:
                pass
        elif kind == "crack":
            put(img, crack_sprite(), (W / 2, 1050), scale=s, alpha=a * (0.75 + 0.25 * math.sin(t * 30)))
        elif kind == "stamp":
            put(img, pw_world.stamp(it["text"]), (W / 2, 700 + it["k"] * 150), scale=1 + 0.8 * (1 - s) if s < 1 else 1.0, alpha=a)

    def confetti(self, img: Image.Image, t: float) -> None:
        d = ImageDraw.Draw(img)
        rng = np.random.default_rng(5)
        cols = [(255, 80, 120), (255, 220, 90), (90, 200, 255), (140, 255, 140), (255, 255, 255)]
        for k in range(70):
            x0, sp, ph = rng.uniform(0, W), rng.uniform(160, 320), rng.uniform(0, 3)
            y = (t * sp + ph * 500) % (H + 100) - 60
            x = x0 + 30 * math.sin(t * 2 + k)
            a = t * 4 + k
            dx, dy = 12 * math.cos(a), 6 * math.sin(a)
            d.polygon([(x - dx, y - dy), (x + dy, y - dx), (x + dx, y + dy), (x - dy, y + dx)], fill=cols[k % len(cols)])

    def caption(self, img: Image.Image, t: float) -> None:
        words = self.spec.words
        for gi, g in enumerate(self.groups):
            start = words[g[0]].start - 0.05
            end = words[self.groups[gi + 1][0]].start - 0.05 if gi + 1 < len(self.groups) else words[g[-1]].end + 0.4
            if start <= t < end:
                now = max([k for k in g if words[k].start <= t + 0.02], default=g[0])
                put(img, pw_world.caption_pill(tuple(words[k].text for k in g), g.index(now)), (W / 2, CAPTION_Y),
                    scale=0.9 + 0.1 * back((t - start) / 0.16))
                return

    def frame(self, t: float) -> Image.Image:
        scenes = self.spec.scenes
        i = next((k for k, s in enumerate(scenes) if s.start <= t < s.end), len(scenes) - 1)
        img = self.scene_image(i, t)
        if i > 0 and t - scenes[i].start < FADE:
            img = Image.blend(self.scene_image(i - 1, t), img, ease((t - scenes[i].start) / FADE))
        self.caption(img, t)
        return img


@lru_cache(maxsize=32)
def title_sprite(text: str) -> Image.Image:
    f = font(int(118 * SS), "Black")
    w = int(f.getlength(text)) + 60 * SS
    img = Image.new("RGBA", (w, 170 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((w / 2 + 6 * SS, 92 * SS), text, font=f, fill=(0, 0, 0, 150), anchor="mm")
    d.text((w / 2, 85 * SS), text, font=f, fill=WHITE, anchor="mm", stroke_width=7 * SS, stroke_fill=OUTLINE)
    return shrink(img)


@lru_cache(maxsize=32)
def text_sprite(text: str, size: int, color, weight: str = "Bold") -> Image.Image:
    f = font(int(size * SS), weight)
    w = int(f.getlength(text)) + 30 * SS
    img = Image.new("RGBA", (w, int(size * 1.6 * SS)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((w / 2, size * 0.8 * SS), text, font=f, fill=color, anchor="mm", stroke_width=3 * SS, stroke_fill=OUTLINE)
    return shrink(img)


@lru_cache(maxsize=32)
def year_sprite(text: str) -> Image.Image:
    f = font(int(150 * SS), "Black")
    w = int(f.getlength(text)) + 60 * SS
    img = Image.new("RGBA", (w, 200 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((w / 2, 100 * SS), text, font=f, fill=GOLD, anchor="mm", stroke_width=9 * SS, stroke_fill=OUTLINE)
    return shrink(img)


@lru_cache(maxsize=32)
def line_sprite(text: str) -> Image.Image:
    size = 50
    f = font(int(size * SS), "ExtraBold")
    w = int(f.getlength(text)) + 70 * SS
    h = int(size * 1.7 * SS)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w, h], radius=h // 2, fill=(255, 255, 255, 240), outline=OUTLINE, width=5 * SS)
    d.text((w / 2, h / 2), text, font=f, fill=INK, anchor="mm")
    out = shrink(img)
    if out.width > W - 80:
        k = (W - 80) / out.width
        out = out.resize((int(out.width * k), int(out.height * k)), Image.LANCZOS)
    return out


@lru_cache(maxsize=8)
def news_sprite(text: str) -> Image.Image:
    w, h = 640, 330
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w * SS, h * SS], fill=(246, 242, 228), outline=OUTLINE, width=5 * SS)
    d.text((w / 2 * SS, 46 * SS), "THE DAILY NEWS", font=font(int(40 * SS), "Black"), fill=INK, anchor="mm")
    d.line([(20 * SS, 80 * SS), ((w - 20) * SS, 80 * SS)], fill=INK, width=4 * SS)
    f = font(int(96 * SS), "Black")
    k = min(1.0, (w - 50) * SS / max(1.0, f.getlength(text)))
    f = font(int(96 * SS * k), "Black")
    d.text((w / 2 * SS, 160 * SS), text, font=f, fill=RED, anchor="mm")
    for y in range(230, 316, 20):
        d.line([(30 * SS, y * SS), ((w - 30) * SS, y * SS)], fill=(170, 166, 156), width=6 * SS)
    out = shrink(img).rotate(-4, expand=True, resample=Image.BICUBIC)
    return out


@lru_cache(maxsize=2)
def crack_sprite() -> Image.Image:
    w, h = 120, 560
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(60, 0), (30, 120), (84, 200), (26, 320), (90, 420), (50, 560)]
    pts = [(x * SS, y * SS) for x, y in pts]
    d.line(pts, fill=(255, 230, 80), width=26 * SS, joint="curve")
    d.line(pts, fill=WHITE, width=10 * SS, joint="curve")
    return shrink(img)


def thumbnail(script: dict, out: Path, t: float = 0.62) -> Path:
    """A picture made to be clicked, not a frame of the video: the whole band big on the stadium stage, the band's
    name huge on top, a gold "IN 45 SECONDS" ribbon and the hook (script.thumbnail.text, else the first scene's line)."""
    pw_world.ensure_fonts()
    band = dict(script.get("band") or {})
    looks = dict(band.get("members") or {})
    drop = 320  # the stage sits lower than in the video, so the band fills the picture
    base = set_image("stadium", int(script.get("seed") or 7)).crop((60, 0, 60 + W, H))
    img = Image.new("RGBA", (W, H))
    img.paste(base.crop((0, 0, W, 1)).resize((W, drop)), (0, 0))
    img.paste(base.crop((0, 0, W, H - drop)), (0, drop))
    lights(img, t)
    front = [n for n, lk in looks.items() if lk.get("play") != "drums"]
    drummers = [n for n, lk in looks.items() if lk.get("play") == "drums"]
    foot = FLOOR + drop
    k = {1: 1.15, 2: 1.1, 3: 1.0, 4: 0.86}.get(len(front), 0.74)
    for n in drummers[:1]:  # on a high riser behind, so the kit shows over the front row
        lk, kd = looks[n], k * 0.82
        y = foot - 470 * k
        put(img, member(lk, t, "drums", "grin", "hang", 5), (W / 2, y - SH * kd / 2 + 40 * kd), scale=kd)
        kit = drum_kit(t, str(band.get("name") or ""))
        put(img, kit, (W / 2, y + 10 - kit.height * kd * 0.5), scale=kd * 1.1)
    span = W * (0.74 if len(front) > 2 else 0.5)
    for j, n in enumerate(front):
        x = W / 2 if len(front) == 1 else W / 2 - span / 2 + span * j / (len(front) - 1)
        lk = looks[n]
        put(img, member(lk, t, str(lk.get("play") or "none"), "grin", "hang", j), (x, foot - SH * k / 2), scale=k)
    fans = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    crowd(fans, t, int(script.get("seed") or 7), dense=True)
    img.alpha_composite(fans.crop((0, 0, W, H - 130)), (0, 130))
    shade = np.zeros((H, W, 4), np.uint8)  # the top darkens, so the words read over the lights
    shade[..., 3] = (np.clip(1 - np.linspace(0, 1, H) / 0.42, 0, 1) ** 1.4 * 200).astype(np.uint8)[:, None]
    img.alpha_composite(Image.fromarray(shade, "RGBA"))
    name = str(band.get("name") or script.get("title") or "").upper()
    f = font(int(250 * SS), "Black")
    k = min(1.0, (W - 70) * SS / max(1.0, f.getlength(name)))
    f = font(int(250 * SS * k), "Black")
    w, h = int(f.getlength(name)) + 80 * SS, int(330 * SS * k) + 40 * SS
    title = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(title).text((w / 2, h / 2), name, font=f, fill=(255, 196, 40, 255), anchor="mm",
                               stroke_width=int(22 * SS * k), stroke_fill=(255, 150, 20, 255))
    from PIL import ImageFilter

    title = title.filter(ImageFilter.GaussianBlur(16 * SS))  # a gold glow behind the letters
    ImageDraw.Draw(title).text((w / 2, h / 2), name, font=f, fill=WHITE, anchor="mm", stroke_width=int(10 * SS * k),
                               stroke_fill=OUTLINE)
    put(img, shrink(title), (W / 2, 250))
    sub = str(band.get("sub") or "").upper()
    m = re.search(r"in (\d+) seconds", str(script.get("title") or ""), re.I)
    sub = f"IN {m.group(1)} SECONDS" if m else (sub or "IN UNDER A MINUTE")
    put(img, ribbon(sub), (W / 2, 445), angle=-2)
    hook = str((script.get("thumbnail") or {}).get("text") or (script.get("scenes") or [{}])[0].get("line") or "").upper()
    if hook:
        put(img, hook_sprite(hook), (W / 2, 640), angle=3)
    img.convert("RGB").save(out, quality=92)
    return out


def ribbon(text: str) -> Image.Image:
    f = font(int(64 * SS), "Black")
    w, h = int(f.getlength(text)) + 90 * SS, 108 * SS
    img = Image.new("RGBA", (w + 10 * SS, h + 12 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6 * SS, 10 * SS, w + 6 * SS, h + 10 * SS], radius=18 * SS, fill=(0, 0, 0, 120))
    d.rounded_rectangle([0, 0, w, h], radius=18 * SS, fill=GOLD, outline=OUTLINE, width=6 * SS)
    d.text((w / 2, h / 2), text, font=f, fill=INK, anchor="mm")
    return shrink(img)


def hook_sprite(text: str) -> Image.Image:
    f = font(int(70 * SS), "Black")
    k = min(1.0, (W - 150) * SS / max(1.0, f.getlength(text)))
    f = font(int(70 * SS * k), "Black")
    w, h = int(f.getlength(text)) + 70 * SS, int(112 * SS * k) + 10 * SS
    img = Image.new("RGBA", (w + 10 * SS, h + 12 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([6 * SS, 10 * SS, w + 6 * SS, h + 10 * SS], fill=(0, 0, 0, 120))
    d.rectangle([0, 0, w, h], fill=RED, outline=WHITE, width=6 * SS)
    d.text((w / 2, h / 2), text, font=f, fill=WHITE, anchor="mm")
    return shrink(img)


# --------------------------------------------------------------------------- the rock bed (ours, made here)

RATE = 44100
CHORDS = {"E": 82.41, "C": 65.41, "G": 98.0, "D": 73.42, "A": 110.0, "B": 61.74, "F#": 92.5}


PROGRESSIONS = (("E", "C", "G", "D"), ("A", "E", "F#", "D"), ("G", "D", "E", "C"), ("D", "A", "B", "G"), ("E", "G", "A", "C"))


def titled(script: dict, seconds: float) -> dict:
    """The real length in the title and under the band's name ("Oasis in 45 Seconds"), rounded to 5 s."""
    n = max(30, int(round(seconds / 5.0)) * 5)
    band = {**(script.get("band") or {}), "sub": f"in {n} seconds"}
    title = re.sub(r"\bin \d+ seconds\b", f"in {n} Seconds", str(script.get("title") or ""), flags=re.I)
    if not re.search(r"\bin \d+ Seconds\b", title):
        title = f"{str(band.get('name') or '').title()} in {n} Seconds"
    return {**script, "band": band, "title": title}


def rock_bed(seconds: float, out: Path, progression=None, bpm: int = BPM, seed: int = 0) -> Path:
    """A driving rock loop in code: kick, snare, hats, a palm-muted power-chord guitar and a bass, at `bpm`
    (the members play to the same beat). No samples, no recordings: nothing for Content ID to match."""
    progression = progression or PROGRESSIONS[seed % len(PROGRESSIONS)]  # not the same four chords on every video
    beat = 60.0 / bpm
    n = int((seconds + 1) * RATE)
    tt = np.arange(n) / RATE
    mix = np.zeros(n, np.float32)
    rng = np.random.default_rng(3)

    def add(at: float, sig: np.ndarray, gain: float):
        a = int(at * RATE)
        if a >= n:
            return
        b = min(n, a + len(sig))
        mix[a:b] += gain * sig[: b - a]

    k_t = np.arange(int(0.35 * RATE)) / RATE
    kick = np.sin(2 * np.pi * np.cumsum(50 + 90 * np.exp(-k_t * 30)) / RATE) * np.exp(-k_t * 9)
    s_t = np.arange(int(0.25 * RATE)) / RATE
    snare = (rng.uniform(-1, 1, len(s_t)) * 0.8 + 0.4 * np.sin(2 * np.pi * 190 * s_t)) * np.exp(-s_t * 18)
    h_t = np.arange(int(0.06 * RATE)) / RATE
    hat = np.diff(rng.uniform(-1, 1, len(h_t) + 1)) * np.exp(-h_t * 70)
    crash_t = np.arange(int(1.6 * RATE)) / RATE
    crash = np.diff(rng.uniform(-1, 1, len(crash_t) + 1)) * np.exp(-crash_t * 2.5)
    beats = int(seconds / beat) + 2
    for b in range(beats):
        at = b * beat
        if b % 4 in (0, 2) or (b % 8 == 7):
            add(at, kick, 0.9)
        if b % 4 == 2 and b % 8 == 6:
            add(at + beat / 2, kick, 0.6)
        if b % 2 == 1:
            add(at, snare, 0.5)
        add(at, hat, 0.22)
        add(at + beat / 2, hat, 0.14)
        if b % 16 == 0:
            add(at, crash, 0.18)
    # the guitar: eighth-note power chords, a chord per bar, distorted and softened
    g = np.zeros(n, np.float32)
    bass = np.zeros(n, np.float32)
    e_len = int(beat / 2 * RATE)
    e_t = np.arange(e_len) / RATE
    env = np.exp(-e_t * 7)
    for e in range(beats * 2):
        root = CHORDS[progression[(e // 8) % len(progression)]] * 2
        tone = sum(((f * e_t) % 1.0) * 2 - 1 for f in (root, root * 1.498, root * 2.0))
        a = int(e * beat / 2 * RATE)
        b = min(n, a + e_len)
        if a >= n:
            break
        accent = 1.0 if e % 8 in (0, 3, 6) else 0.7
        g[a:b] += (tone * env * accent)[: b - a]
        bass[a:b] += (np.sin(2 * np.pi * root / 2 * e_t) * np.exp(-e_t * 3))[: b - a]
    g = np.tanh(g * 2.2)
    alpha = 0.18  # a one-pole low-pass: takes the fizz off
    kernel = alpha * (1 - alpha) ** np.arange(120)  # its impulse response (numpy only: scipy isn't everywhere)
    g = np.convolve(g, kernel.astype(np.float32))[:n].astype(np.float32)
    mix += 0.55 * g / max(1e-6, np.abs(g).max()) + 0.35 * bass
    mix /= max(1e-6, np.abs(mix).max()) * 1.05
    fade = int(0.02 * RATE)
    mix[:fade] *= np.linspace(0, 1, fade)
    pcm = (mix * 32767).astype("<i2").tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-i", "-",
                    "-c:a", "libmp3lame", "-b:a", "192k", str(out)], input=pcm, check=True)
    return out


# --------------------------------------------------------------------------- making a sample


def render(spec: Spec, band: dict, voice: Path, out: Path, music: Path | None, music_volume: float, progress=lambda f, m: None) -> Path:
    from modules import bw_fx

    bw_render.align(spec)
    original, look = bw_fx.FxRenderer, spec.look
    bw_fx.FxRenderer = lambda s: BandRenderer(s, band)  # bw_render.render builds bw_fx.FxRenderer for the "satellite" look
    spec.look = "satellite"
    try:
        return bw_render.render(spec, voice, out, music, music_volume, progress)
    finally:
        bw_fx.FxRenderer = original
        spec.look = look


def make_sample(script_path: Path, work: Path, voice: str = "am_puck", speed: float = 1.0) -> Path:
    """Draw a band script into `work`: voice.mp3, music.mp3, video.mp4, thumb.jpg."""
    from trendclip import kokoro_voice

    script = json.loads(script_path.read_text(encoding="utf-8"))
    work.mkdir(parents=True, exist_ok=True)
    text = " ".join(" ".join(str(s.get("narration") or "").split()) for s in script["scenes"])
    got = kokoro_voice.synthesize(text, work / "voice.mp3", voice, speed)
    kokoro_voice.unload()
    from modules import pw_channel

    words = pw_channel.tighten(work / "voice.mp3", [bw_render.Word(w.text, float(w.start), float(w.end)) for w in got], 0.2)
    script = {**script, "look": "satellite"}
    spec = bw_render.spec_from_dict(script, words)
    spec.hold = 0.8
    spec.sfx = True
    music = rock_bed(spec.duration + 2, work / "music.mp3")
    render(spec, script["band"], work / "voice.mp3", work / "video.mp4", music, float(script.get("music_volume", 0.16)),
           lambda f, m: print(f"{(f or 0):.0%} {m}", flush=True) if f and int(f * 100) % 10 == 0 else None)
    BandRenderer(spec, script["band"]).frame(max(0.3, spec.scenes[0].end - 0.3)).save(work / "thumb.jpg", quality=92)
    return work / "video.mp4"


if __name__ == "__main__":
    make_sample(Path(sys.argv[1]), Path(sys.argv[2]), *(sys.argv[3:4] or []))
