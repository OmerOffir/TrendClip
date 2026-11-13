"""A band's life story in under a minute: the sample look for the channel that may replace Price Tag Why (2026-11-12).

The owner pointed at Zip Story (@zipstoryyt: "<Band> in 40 Seconds", chibi cartoon members, a year card per beat,
5.3K subscribers in its first three weeks) and asked whether we can do the niche better. This look keeps what works
there (a year on every beat, the band drawn as recognisable cartoons) and adds what we know holds viewers: a narrator
with word-by-word captions, something new on screen every couple of seconds, members who play in time with the
music, crowds that bounce, and a beat-synced rock bed made here in code (no band recordings: their songs are
Content ID claimed, which costs the video its money).

A script is the usual scenes list (narration per scene) plus:
    band      {"name": "OASIS", "sub": "in 45 seconds", "members": {<name>: <look>}}
              a look: the musician as fans picture them (hair, face, nose, brows, glasses, beard, hat, top, colours,
                       play, guitar, extras): every key and value is in LOOK_VALUES and EXTRAS below
A scene (kind "band") may carry:
    set       garage | club | studio | stadium | dark | street   (sign: a neon word in the club)
    year      the big year under the title;  line: a short card under it ("KING TUT'S, GLASGOW")
    members   [name | {name, expr, play, pose, move, say|think, say_cue, cue}] (expr: smile grin laugh cool angry sad
              shocked; pose: hang up cross point wave fist shrug; move: jump bounce spin shake lean slide; say: a
              speech bubble of a few words, no voice, one on screen at a time, the speaker hops)
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


def set_tempo(bpm: float) -> None:
    """The members play, bob and jump at the music's tempo (the owner's tracks are about 123)."""
    global BEAT
    BEAT = 60.0 / max(60.0, min(200.0, float(bpm)))
FLOOR = 1380  # where the members' feet stand
CAPTION_Y = 1545
FADE = 0.18
POP = 0.32
SKINS = {"pale": (255, 232, 214), "light": (255, 220, 190), "olive": (226, 190, 150), "tan": (232, 188, 150),
         "brown": (176, 120, 84), "dark": (120, 80, 56)}
HAIRS = {"brown": (92, 62, 40), "dark": (44, 34, 30), "black": (24, 22, 26), "blond": (238, 200, 110),
         "platinum": (248, 240, 210), "red": (184, 74, 40), "auburn": (130, 50, 30), "grey": (170, 170, 170),
         "white": (240, 240, 240), "pink": (240, 110, 170), "blue": (60, 110, 220), "green": (70, 180, 90),
         "purple": (130, 70, 180)}
TOPS = {"khaki": (150, 140, 90), "navy": (40, 52, 96), "black": (36, 36, 42), "white": (240, 240, 240),
        "red": (196, 48, 52), "green": (60, 120, 80), "grey": (120, 124, 132), "blue": (64, 110, 190),
        "brown": (120, 84, 56), "denim": (84, 110, 150), "yellow": (236, 196, 64), "pink": (230, 110, 160),
        "purple": (120, 70, 160), "orange": (230, 130, 50), "gold": (212, 175, 55), "silver": (190, 194, 204),
        "maroon": (120, 30, 40), "teal": (40, 140, 140)}
OUTLINE = (30, 26, 36)


SETS = ("garage", "club", "studio", "stadium", "dark", "street")
LOOK_VALUES = {"hair": ("mop", "fringe", "short", "side_part", "slick", "quiff", "long", "curly", "big_curly", "afro",
                        "spiky", "mohawk", "mullet", "ponytail", "dreads", "bald"),
               "hair_color": tuple(HAIRS), "face": ("round", "long", "narrow", "square", "wide"),
               "nose": ("small", "big", "long", "none"), "brows": ("thin", "thick", "mono"),
               "glasses": ("none", "round", "shades", "square", "aviators"),
               "beard": ("none", "stubble", "full", "long", "goatee", "mustache", "sideburns"),
               "hat": ("none", "top_hat", "fedora", "cowboy", "beanie", "cap", "beret", "bandana"), "hat_color": tuple(TOPS),
               "top": ("tee", "tank", "shirt", "jacket", "suit", "leather", "parka", "hoodie", "stripes", "flannel"),
               "play": ("guitar", "bass", "drums", "mic", "none"),
               "guitar": ("flag", "sunburst", "black", "red", "white", "gold", "blue", "green", "wood"),
               "skin": tuple(SKINS), "top_color": tuple(TOPS), "pants": tuple(TOPS)}
EXTRAS = ("mustache", "sideburns", "earring", "chain", "scarf", "bowtie", "armband", "tattoos", "wristband", "headband", "teeth")
EXPRS = ("smile", "grin", "laugh", "cool", "angry", "sad", "shocked")
POSES = ("hang", "up", "cross", "point", "wave", "fist", "shrug")
MOVES = ("jump", "bounce", "spin", "shake", "lean", "slide")  # how a member moves on stage, on top of playing
BUBBLE_TEXT = 18  # characters a speech bubble may have


def rgb(table: dict, name: Any, default: str) -> tuple[int, int, int]:
    if isinstance(name, (list, tuple)):
        return tuple(int(c) for c in name)[:3]  # type: ignore[return-value]
    return table.get(str(name or default), table[default])


# --------------------------------------------------------------------------- a band member

SW, SH = 360, 740  # a member's sprite, in units (drawn at SS); the top 100 is room for big hair and hats
OY = 100


def member(look: dict, t: float, play: str, expr: str = "smile", pose: str = "hang", seed: int = 0) -> Image.Image:
    """One member at time t: a big-headed cartoon of a real musician (their hair, face, hat, beard, glasses, clothes
    and extras), who plays their instrument in time with the beat."""
    img = Image.new("RGBA", (SW * SS, SH * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = SS

    def P(x: float, y: float) -> tuple[float, float]:
        return x * u, (y + OY) * u

    def box(x0, y0, x1, y1):
        return [x0 * u, (y0 + OY) * u, x1 * u, (y1 + OY) * u]

    lw = int(5 * u)

    def glaze(paint) -> None:
        """See-through paint (blush, stubble) laid over what is drawn: ImageDraw would punch holes in the sprite."""
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        paint(ImageDraw.Draw(layer))
        img.alpha_composite(layer)
    skin = rgb(SKINS, look.get("skin"), "light")
    hair = rgb(HAIRS, look.get("hair_color"), "brown")
    top = rgb(TOPS, look.get("top_color"), "navy")
    pants = rgb(TOPS, look.get("pants"), "denim")
    extras = set(look.get("extras") or [])
    dark = tuple(int(c * 0.7) for c in top)
    beat = (t + seed * 0.07) / BEAT
    hit = abs(math.sin(math.pi * beat))  # 0 on the beat, 1 between
    bob = 7 * (1 - hit) if play != "none" else 3 * math.sin(t * 2.2 + seed)
    hy = -bob  # head and body ride the beat
    drums = play == "drums"
    style = str(look.get("top") or "tee")
    sleeve = skin if style == "tank" else top

    # legs and shoes (a drummer sits behind the kit: no legs)
    if not drums:
        for x0 in (138, 190):
            d.rounded_rectangle(box(x0, 500, x0 + 34, 606), radius=12 * u, fill=pants, outline=OUTLINE, width=lw)
            d.ellipse(box(x0 - 10, 590, x0 + 44, 626), fill=(40, 36, 44), outline=OUTLINE, width=lw)
    sh_l, sh_r = (128, 352 + hy), (232, 352 + hy)
    arms: list[tuple[tuple[float, float], tuple[float, float]]] = []
    behind = play == "mic" and pose != "up"
    if style == "hoodie":  # the hood behind the neck
        d.ellipse(box(124, 296 + hy, 236, 360 + hy), fill=dark, outline=OUTLINE, width=lw)
    # the body
    body_bottom = 540 if style in ("parka", "suit", "leather") else 512
    d.rounded_rectangle(box(112, 318 + hy, 248, body_bottom + hy * 0.5), radius=40 * u, fill=top, outline=OUTLINE, width=lw)
    body = box(112, 318 + hy, 248, body_bottom + hy * 0.5)
    if style in ("stripes", "flannel"):  # a pattern clipped to the body
        pat = Image.new("RGBA", img.size, (0, 0, 0, 0))
        q = ImageDraw.Draw(pat)
        light = tuple(min(255, c + 90) for c in top)
        if style == "stripes":
            for y in range(330, body_bottom, 26):
                q.rectangle(box(100, y + hy, 260, y + 12 + hy), fill=WHITE)
        else:
            for y in range(322, body_bottom, 36):
                q.rectangle(box(100, y + hy, 260, y + 10 + hy), fill=(*dark, 255))
            for x in range(118, 250, 36):
                q.rectangle(box(x, 300 + hy, x + 10, body_bottom + hy), fill=(*light, 150))
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(body, radius=40 * u, fill=255)
        img.paste(pat, (0, 0), Image.composite(pat, Image.new("RGBA", img.size, (0, 0, 0, 0)), mask).getchannel("A"))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(body, radius=40 * u, outline=OUTLINE, width=lw)
    if style == "parka":
        fur = tuple(min(255, c + 70) for c in top)
        d.ellipse(box(120, 300 + hy, 240, 350 + hy), fill=fur, outline=OUTLINE, width=lw)
        d.line([P(180, 350 + hy), P(180, 535 + hy * 0.5)], fill=OUTLINE, width=int(3 * u))
        for y in (430, 480):
            d.rectangle(box(126, y + hy, 160, y + 24 + hy), outline=OUTLINE, width=int(3 * u))
            d.rectangle(box(200, y + hy, 234, y + 24 + hy), outline=OUTLINE, width=int(3 * u))
    elif style in ("jacket", "suit"):
        inner = (240, 240, 240)
        d.polygon([P(180, 330 + hy), P(150, 330 + hy), P(180, 430 + hy)], fill=inner)
        d.polygon([P(180, 330 + hy), P(210, 330 + hy), P(180, 430 + hy)], fill=inner)
        d.line([P(150, 330 + hy), P(180, 430 + hy), P(210, 330 + hy)], fill=OUTLINE, width=int(3 * u))
        if style == "suit":  # a thin dark tie
            d.polygon([P(174, 336 + hy), P(186, 336 + hy), P(190, 410 + hy), P(180, 424 + hy), P(170, 410 + hy)], fill=(30, 30, 40))
    elif style == "leather":  # a biker jacket: wide lapels, a slanted zip, a white tee under it
        d.polygon([P(150, 326 + hy), P(200, 326 + hy), P(176, 420 + hy)], fill=(236, 236, 236))
        d.polygon([P(140, 330 + hy), P(176, 330 + hy), P(150, 400 + hy)], fill=dark, outline=OUTLINE)
        d.polygon([P(220, 330 + hy), P(184, 330 + hy), P(214, 400 + hy)], fill=dark, outline=OUTLINE)
        d.line([P(200, 340 + hy), P(170, 530 + hy * 0.5)], fill=(200, 200, 210), width=int(4 * u))
    elif style == "shirt":
        d.polygon([P(160, 322 + hy), P(180, 350 + hy), P(200, 322 + hy)], fill=(250, 250, 250), outline=OUTLINE)
        for y in (380, 420, 460):
            d.ellipse(box(176, y + hy, 184, y + 8 + hy), fill=OUTLINE)
    elif style == "tank":  # a vest top: the shoulders bare
        d.chord(box(146, 300 + hy, 214, 372 + hy), 0, 180, fill=skin, outline=OUTLINE, width=int(3 * u))
    elif style == "hoodie":
        d.line([P(166, 340 + hy), P(164, 400 + hy)], fill=WHITE, width=int(4 * u))
        d.line([P(194, 340 + hy), P(196, 400 + hy)], fill=WHITE, width=int(4 * u))
        d.rounded_rectangle(box(140, 450 + hy, 220, 500 + hy * 0.5), radius=10 * u, outline=OUTLINE, width=int(3 * u))
    if "chain" in extras:
        d.arc(box(150, 290 + hy, 210, 400 + hy), 30, 150, fill=(240, 200, 70), width=int(5 * u))
    if "scarf" in extras:
        sc = (200, 50, 60) if top != TOPS["red"] else (240, 200, 70)
        d.rounded_rectangle(box(140, 318 + hy, 220, 344 + hy), radius=12 * u, fill=sc, outline=OUTLINE, width=int(3 * u))
        d.rounded_rectangle(box(196, 330 + hy, 220, 450 + hy), radius=8 * u, fill=sc, outline=OUTLINE, width=int(3 * u))
    if "bowtie" in extras:
        d.polygon([P(160, 322 + hy), P(180, 334 + hy), P(160, 346 + hy)], fill=(30, 30, 40))
        d.polygon([P(200, 322 + hy), P(180, 334 + hy), P(200, 346 + hy)], fill=(30, 30, 40))

    # the instrument and the arms that play it
    if play in ("guitar", "bass"):
        gx, gy = 168, 450 + hy
        neck_end = (318, 312 + hy) if play == "guitar" else (338, 286 + hy)
        d.line([P(gx + 20, gy - 10), P(*neck_end)], fill=(90, 60, 36), width=int(16 * u))
        d.rounded_rectangle(box(neck_end[0] - 10, neck_end[1] - 22, neck_end[0] + 16, neck_end[1] + 8), radius=5 * u, fill=(60, 40, 28))
        kind = str(look.get("guitar") or ("sunburst" if play == "guitar" else "black"))
        bodyc = {"sunburst": (196, 96, 40), "black": (34, 34, 40), "red": (200, 40, 48), "white": (236, 236, 236),
                 "flag": (20, 50, 140), "gold": (222, 184, 70), "blue": (50, 100, 200), "green": (60, 150, 90),
                 "wood": (210, 160, 100)}.get(kind, (196, 96, 40))
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
        wave = math.sin(t * 9 + seed)
        pump = 60 * (1 - hit)  # the fist punches up on every beat
        arms = {"up": [(sh_l, (96, 210 + hy)), (sh_r, (264, 210 + hy))],
                "point": [(sh_l, (110, 470 + hy)), (sh_r, (300, 300 + hy))],
                "wave": [(sh_l, (104, 486 + hy)), (sh_r, (282 + 34 * wave, 196 + 10 * abs(wave) + hy))],
                "fist": [(sh_l, (104, 486 + hy)), (sh_r, (250, 250 - pump + hy))],
                "shrug": [(sh_l, (70, 330 + hy)), (sh_r, (290, 330 + hy))],
                "cross": [(sh_l, (214, 420 + hy)), (sh_r, (146, 420 + hy))]}.get(pose, [(sh_l, (104, 486 + hy)), (sh_r, (256, 486 + hy))])
    for k, (a, b) in enumerate(arms):
        d.line([P(*a), P(*b)], fill=OUTLINE, width=int(30 * u))
        d.line([P(*a), P(*b)], fill=sleeve, width=int(21 * u))
        mx, my_ = a[0] + (b[0] - a[0]) * 0.3, a[1] + (b[1] - a[1]) * 0.3
        if "armband" in extras and k == 1:
            d.ellipse(box(mx - 14, my_ - 14, mx + 14, my_ + 14), fill=(40, 40, 46))
        if "tattoos" in extras and style == "tank":
            for f_ in (0.35, 0.6):
                tx, ty = a[0] + (b[0] - a[0]) * f_, a[1] + (b[1] - a[1]) * f_
                d.ellipse(box(tx - 6, ty - 6, tx + 6, ty + 6), fill=(60, 80, 120))
        if "wristband" in extras:
            wx, wy = a[0] + (b[0] - a[0]) * 0.86, a[1] + (b[1] - a[1]) * 0.86
            d.ellipse(box(wx - 13, wy - 13, wx + 13, wy + 13), fill=(30, 30, 36))
        d.ellipse(box(b[0] - 17, b[1] - 17, b[0] + 17, b[1] + 17), fill=skin, outline=OUTLINE, width=lw)
        if drums:  # the sticks
            d.line([P(*b), P(b[0] + (40 if b[0] > 180 else -40), b[1] - 50)], fill=(220, 190, 130), width=int(8 * u))

    # the head: its shape, then the hair behind it, the face, the hair in front, the hat
    cx, cy = 180, 188 + hy * 1.3
    rx, ry = {"round": (122, 122), "long": (108, 134), "narrow": (100, 128), "square": (124, 118),
              "wide": (134, 114)}.get(str(look.get("face") or "round"), (122, 122))
    hs = str(look.get("hair") or "short")
    hat = str(look.get("hat") or "none")
    hatc = rgb(TOPS, look.get("hat_color"), "black")

    def curls(points, size):
        for px, py in points:
            d.ellipse(box(px - size, py - size, px + size, py + size), fill=hair, outline=OUTLINE, width=lw)

    if hs == "long":  # long hair falls behind the head
        d.rounded_rectangle(box(cx - rx - 8, cy - 40, cx + rx + 8, cy + 150), radius=50 * u, fill=hair, outline=OUTLINE, width=lw)
    elif hs == "curly":
        curls([(cx + (rx + 4) * math.cos(a), cy + (ry - 6) * math.sin(a) - 6)
               for a in (math.pi * (0.9 + 1.2 * k / 13) for k in range(14))], 34)
    elif hs == "big_curly":  # a huge halo of curls down to the shoulders
        pts = [(cx + (rx + 34) * math.cos(a), cy - 10 + (ry + 30) * math.sin(a)) for a in (math.pi * (0.8 + 1.4 * k / 19) for k in range(20))]
        pts += [(cx + side * (rx + 26 + 6 * (k % 2)), cy + 40 + k * 34) for side in (-1, 1) for k in range(4)]
        curls(pts, 42)
    elif hs == "afro":
        d.ellipse(box(cx - rx - 60, cy - ry - 80, cx + rx + 60, cy + 50), fill=hair, outline=OUTLINE, width=lw)
        curls([(cx + (rx + 52) * math.cos(a), cy - 15 + (ry + 62) * math.sin(a) * 0.8) for a in (math.pi * (1.0 + k / 11) for k in range(12))], 24)
    elif hs == "mullet":  # short on top, long at the back
        d.rounded_rectangle(box(cx - rx + 4, cy - 10, cx + rx - 4, cy + 150), radius=40 * u, fill=hair, outline=OUTLINE, width=lw)
    elif hs == "dreads":
        for k in range(9):
            x = cx - rx - 10 + k * (2 * rx + 20) / 8
            d.rounded_rectangle(box(x - 14, cy - 60, x + 14, cy + 160 - 20 * (k % 2)), radius=14 * u, fill=hair, outline=OUTLINE, width=int(3 * u))
    elif hs == "ponytail":
        d.rounded_rectangle(box(cx + rx - 30, cy - 40, cx + rx + 26, cy + 140), radius=26 * u, fill=hair, outline=OUTLINE, width=lw)

    if str(look.get("face") or "round") == "square":
        d.rounded_rectangle(box(cx - rx, cy - ry, cx + rx, cy + ry), radius=70 * u, fill=skin, outline=OUTLINE, width=lw)
    else:
        d.ellipse(box(cx - rx, cy - ry, cx + rx, cy + ry), fill=skin, outline=OUTLINE, width=lw)
    d.ellipse(box(cx - rx - 14, cy + 2, cx - rx + 18, cy + 44), fill=skin, outline=OUTLINE, width=lw)  # ears
    d.ellipse(box(cx + rx - 18, cy + 2, cx + rx + 14, cy + 44), fill=skin, outline=OUTLINE, width=lw)
    if "earring" in extras:
        d.ellipse(box(cx - rx - 8, cy + 42, cx - rx + 8, cy + 58), fill=(240, 200, 70), outline=OUTLINE, width=int(2 * u))
    beard = str(look.get("beard") or "none")
    if beard in ("stubble", "full", "long"):
        jaw = box(cx - rx + 6, cy - ry + 10, cx + rx - 6, cy + ry - 2)
        if beard == "stubble":
            glaze(lambda g: g.chord(jaw, 20, 160, fill=(*hair, 90)))
        else:
            d.chord(jaw, 20, 160, fill=hair)
        if beard == "long":
            d.polygon([P(cx - rx + 30, cy + 60), P(cx + rx - 30, cy + 60), P(cx + 40, cy + ry + 90), P(cx, cy + ry + 110),
                       P(cx - 40, cy + ry + 90)], fill=hair, outline=OUTLINE)
    elif beard == "goatee":
        d.ellipse(box(cx - 30, cy + ry - 40, cx + 30, cy + ry + 14), fill=hair, outline=OUTLINE, width=int(3 * u))
    if beard == "sideburns" or "sideburns" in extras:
        for s_ in (-1, 1):
            x0 = cx + s_ * (rx - 14)
            d.rounded_rectangle(box(min(x0, x0 - s_ * 24), cy - 20, max(x0, x0 - s_ * 24), cy + 60), radius=8 * u, fill=hair)

    # hair in front
    top_hair = box(cx - rx - 6, cy - ry - 10, cx + rx + 6, cy + ry - 30)
    if hs in ("mop", "fringe", "short", "long", "spiky", "mullet", "dreads", "side_part"):
        d.chord(top_hair, 180, 360, fill=hair, outline=OUTLINE, width=lw)
    if hs in ("mop", "fringe", "long"):  # a fringe down over the brows, with a ragged edge
        low = cy - 30 if hs == "mop" else cy - 48
        pts = [P(cx - rx - 4, cy - 10)]
        for k in range(9):
            pts.append(P(cx - rx + 2 * rx * k / 8, low + (14 if k % 2 else 0)))
        pts += [P(cx + rx + 4, cy - 10), P(cx + rx + 4, cy - 60), P(cx - rx - 4, cy - 60)]
        d.polygon(pts, fill=hair)
        d.line(pts[:-2], fill=OUTLINE, width=lw)
        if hs == "mop":  # sides down past the ears
            d.rounded_rectangle(box(cx - rx - 12, cy - 60, cx - rx + 26, cy + 40), radius=16 * u, fill=hair, outline=OUTLINE, width=lw)
            d.rounded_rectangle(box(cx + rx - 26, cy - 60, cx + rx + 12, cy + 40), radius=16 * u, fill=hair, outline=OUTLINE, width=lw)
    elif hs == "spiky":
        for k in range(6):
            x = cx - rx + 20 + k * (2 * rx - 40) / 5
            d.polygon([P(x - 26, cy - ry + 20), P(x, cy - ry - 46), P(x + 26, cy - ry + 20)], fill=hair, outline=OUTLINE)
    elif hs == "side_part":  # a swoop over one side of the forehead
        d.polygon([P(cx - rx - 2, cy - 30), P(cx - 30, cy - 70), P(cx + rx, cy - 50), P(cx + rx + 4, cy - 90),
                   P(cx - rx - 4, cy - 90)], fill=hair)
        d.line([P(cx - rx - 2, cy - 30), P(cx - 30, cy - 70), P(cx + rx, cy - 50)], fill=OUTLINE, width=lw)
        d.line([P(cx - 50, cy - ry + 4), P(cx - 40, cy - 70)], fill=OUTLINE, width=int(3 * u))
    elif hs in ("slick", "ponytail", "quiff"):  # combed back, close to the head, with a shine
        d.chord(box(cx - rx - 2, cy - ry - 4, cx + rx + 2, cy + ry - 80), 180, 360, fill=hair, outline=OUTLINE, width=lw)
        d.arc(box(cx - 60, cy - ry + 6, cx + 40, cy - 40), 200, 260, fill=tuple(min(255, c + 80) for c in hair), width=int(5 * u))
        if hs == "quiff":  # the wave piled up over the forehead
            d.ellipse(box(cx - 80, cy - ry - 56, cx + 70, cy - ry + 36), fill=hair, outline=OUTLINE, width=lw)
            d.arc(box(cx - 50, cy - ry - 36, cx + 40, cy - ry + 20), 200, 320, fill=tuple(min(255, c + 80) for c in hair), width=int(5 * u))
    elif hs == "mohawk":
        for k in range(5):
            y = cy - ry + 10 + k * 22
            d.polygon([P(cx - 18, y + 30), P(cx, y - 60 + k * 8), P(cx + 18, y + 30)], fill=hair, outline=OUTLINE)
    elif hs == "big_curly":
        curls([(cx - rx + 20 + k * (2 * rx - 40) / 5, cy - ry + 16 + (8 if k % 2 else 0)) for k in range(6)], 30)
    elif hs == "curly":
        curls([(cx - rx + 30 + k * (2 * rx - 60) / 4, cy - ry + 14) for k in range(5)], 26)
    elif hs == "bald" and hat == "none":
        d.arc(box(cx - 70, cy - 104, cx - 10, cy - 60), 200, 300, fill=(255, 255, 255), width=int(7 * u))
    if "headband" in extras:
        d.chord(box(cx - rx, cy - ry + 34, cx + rx, cy + ry), 192, 348, fill=None, outline=(220, 40, 50), width=int(16 * u))

    # the hat
    ht = cy - ry  # the top of the head
    if hat == "top_hat":
        d.rounded_rectangle(box(cx - 78, ht - 120, cx + 78, ht + 30), radius=8 * u, fill=hatc, outline=OUTLINE, width=lw)
        d.rectangle(box(cx - 78, ht - 4, cx + 78, ht + 18), fill=(200, 60, 50))
        d.ellipse(box(cx - rx - 40, ht + 14, cx + rx + 40, ht + 54), fill=hatc, outline=OUTLINE, width=lw)
    elif hat == "fedora":
        d.polygon([P(cx - 84, ht + 30), P(cx - 70, ht - 50), P(cx, ht - 34), P(cx + 70, ht - 50), P(cx + 84, ht + 30)], fill=hatc, outline=OUTLINE)
        d.rectangle(box(cx - 84, ht + 6, cx + 84, ht + 26), fill=(30, 30, 36))
        d.ellipse(box(cx - rx - 30, ht + 16, cx + rx + 30, ht + 50), fill=hatc, outline=OUTLINE, width=lw)
    elif hat == "cowboy":
        d.polygon([P(cx - 80, ht + 30), P(cx - 66, ht - 54), P(cx, ht - 30), P(cx + 66, ht - 54), P(cx + 80, ht + 30)], fill=hatc, outline=OUTLINE)
        d.chord(box(cx - rx - 70, ht - 30, cx + rx + 70, ht + 56), 0, 180, fill=hatc, outline=OUTLINE, width=lw)
        d.line([P(cx - rx - 70, ht + 13), P(cx - rx - 50, ht - 20)], fill=OUTLINE, width=lw)
        d.line([P(cx + rx + 70, ht + 13), P(cx + rx + 50, ht - 20)], fill=OUTLINE, width=lw)
    elif hat in ("beanie", "cap"):
        d.chord(box(cx - rx - 4, ht - 10, cx + rx + 4, cy + ry - 60), 180, 360, fill=hatc, outline=OUTLINE, width=lw)
        if hat == "beanie":
            d.rounded_rectangle(box(cx - rx - 6, cy - 70, cx + rx + 6, cy - 34), radius=14 * u, fill=dark if look.get("hat_color") is None else tuple(int(c * 0.8) for c in hatc), outline=OUTLINE, width=lw)
        else:
            d.chord(box(cx - 30, cy - 70, cx + rx + 70, cy - 30), 180, 360, fill=hatc, outline=OUTLINE, width=lw)
    elif hat == "beret":
        d.ellipse(box(cx - rx - 10, ht - 30, cx + rx - 10, ht + 50), fill=hatc, outline=OUTLINE, width=lw)
    elif hat == "bandana":
        d.chord(box(cx - rx - 4, ht - 6, cx + rx + 4, cy + ry - 64), 180, 360, fill=hatc, outline=OUTLINE, width=lw)
        for k in range(5):
            d.ellipse(box(cx - 80 + k * 38, cy - 92, cx - 72 + k * 38, cy - 84), fill=WHITE)
        d.polygon([P(cx + rx - 6, cy - 60), P(cx + rx + 40, cy - 20), P(cx + rx + 10, cy - 10)], fill=hatc, outline=OUTLINE)

    # the face
    blink = (t + seed * 0.71) % (2.4 + seed % 3 * 0.4) < 0.12
    ey = cy + 14
    eyes = ((cx - 44, ey), (cx + 44, ey))
    glasses = str(look.get("glasses") or "none")
    for ex, yy in eyes:
        if glasses in ("round", "shades", "aviators"):
            continue
        if blink:
            d.line([P(ex - 14, yy), P(ex + 14, yy)], fill=OUTLINE, width=int(6 * u))
        elif expr == "laugh":
            d.arc(box(ex - 16, yy - 12, ex + 16, yy + 14), 200, 340, fill=OUTLINE, width=int(7 * u))
        else:
            rr = 11 if expr == "shocked" else 15
            d.ellipse(box(ex - rr, yy - rr - 3, ex + rr, yy + rr + 3), fill=OUTLINE)
            d.ellipse(box(ex - 6 + 3, yy - 11, ex + 1 + 3, yy - 4), fill=WHITE)
    if glasses in ("round", "shades", "square", "aviators"):
        lens = {"shades": (40, 44, 60), "round": (70, 120, 90), "aviators": (60, 50, 40), "square": None}[glasses]
        for ex, yy in eyes:
            if glasses == "square":
                d.rounded_rectangle(box(ex - 32, yy - 22, ex + 32, yy + 22), radius=6 * u, outline=OUTLINE, width=int(8 * u))
            elif glasses == "aviators":
                d.polygon([P(ex - 34, yy - 20), P(ex + 34, yy - 20), P(ex + 26, yy + 18), P(ex, yy + 30), P(ex - 26, yy + 18)],
                          fill=lens, outline=(200, 170, 80))
            else:
                d.ellipse(box(ex - 30, yy - 24, ex + 30, yy + 24), fill=lens, outline=OUTLINE, width=lw)
            if lens:
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
    nose = str(look.get("nose") or "small")
    ny = cy + 44
    if nose == "small":
        d.arc(box(cx - 10, ny - 10, cx + 10, ny + 8), 20, 160, fill=OUTLINE, width=int(4 * u))
    elif nose == "big":
        d.arc(box(cx - 20, ny - 16, cx + 20, ny + 14), 0, 180, fill=OUTLINE, width=int(5 * u))
        d.line([P(cx - 12, ny - 30), P(cx - 18, ny)], fill=OUTLINE, width=int(4 * u))
        d.line([P(cx + 12, ny - 30), P(cx + 18, ny)], fill=OUTLINE, width=int(4 * u))
    elif nose == "long":
        d.line([P(cx - 2, ny - 34), P(cx + 12, ny + 4), P(cx - 8, ny + 8)], fill=OUTLINE, width=int(4 * u), joint="curve")
    my = cy + 70 + (ry - 122) * 0.5
    singing = play == "mic" and hit > 0.35
    if beard == "mustache" or "mustache" in extras:
        d.chord(box(cx - 44, my - 26, cx + 44, my + 8), 180, 360, fill=hair)
    if singing or expr == "shocked":
        o = 14 + 12 * hit if singing else 18
        d.ellipse(box(cx - 18, my - o * 0.6, cx + 18, my + o), fill=(120, 30, 40), outline=OUTLINE, width=lw)
    elif expr in ("grin", "laugh"):
        d.chord(box(cx - 40, my - 30, cx + 40, my + 30), 0, 180, fill=(120, 30, 40), outline=OUTLINE, width=lw)
        if "teeth" in extras or look.get("teeth") == "big":  # big front teeth (Freddie)
            d.rectangle(box(cx - 26, my, cx + 26, my + 12), fill=WHITE, outline=OUTLINE, width=int(2 * u))
    elif expr == "smile":
        d.arc(box(cx - 32, my - 26, cx + 32, my + 16), 20, 160, fill=OUTLINE, width=int(7 * u))
    elif expr in ("angry", "sad"):
        d.arc(box(cx - 30, my, cx + 30, my + 36), 200, 340, fill=OUTLINE, width=int(7 * u))
    else:  # cool: a flat, slightly crooked line
        d.line([P(cx - 24, my + 6), P(cx + 26, my + 2)], fill=OUTLINE, width=int(7 * u))
    if expr not in ("angry", "sad") and beard not in ("full", "long"):
        glaze(lambda g: [g.ellipse(box(ex - 18, my - 26, ex + 18, my - 10), fill=(255, 120, 130, 110)) for ex in (cx - 76, cx + 76)])
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
        bx, by = wide * 0.8, 420  # the bulb hangs in the top corner, clear of the heads and the cards
        d.line([(bx * S, 0), (bx * S, (by - 30) * S)], fill=(30, 30, 30), width=4 * S)
        for k, a in ((3.2, 40), (2.0, 70), (1.0, 255)):
            r = 22 * k
            d.ellipse([(bx - r) * S, (by - r) * S, (bx + r) * S, (by + r) * S], fill=(255, 236, 160, a))
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
        shot = str(o.get("shot") or "wide")
        if isinstance(o.get("poll"), dict):  # the question's two buttons need the middle: the band stands back, small
            shot = "wide"
        ms = self.members_of(scene)
        ms = ms[:1] if shot == "close" else ms[:2] if shot == "duo" else ms
        n = len(ms)
        plays = [str(m.get("play") or self.looks.get(m["name"], {}).get("play") or "none") for m in ms]
        empty = bool(o.get("empty_mic"))
        if shot == "close":  # one member, big, from the waist up: the head under the cards
            spots = [(W / 2, 1.75, 1800.0)]
        elif shot == "duo":
            spots = [(W * 0.28, 1.25, FLOOR + 150.0), (W * 0.72, 1.25, FLOOR + 150.0)]
        elif empty:  # the members stand aside: the singer's mic alone in the light
            left = (n + 1) // 2
            sides = [W * (0.11 + 0.23 * j / max(1, left - 1)) for j in range(left)] if left > 1 else [W * 0.2]
            sides += [W - x for x in sides][: n - left][::-1]
            spots = [(x, 0.78, float(FLOOR)) for x in sides]
        else:  # one column each; the drummer's column, raised on a riser, is never behind someone
            cols = n
            scale = {1: 1.0, 2: 0.98, 3: 0.86, 4: 0.74}.get(cols, 0.64)
            if isinstance(o.get("news"), dict) or isinstance(o.get("poll"), dict):  # the front page / the question needs the middle
                scale = min(scale, 0.74)
            xs = [W / 2] if cols == 1 else [W * 0.27, W * 0.73] if cols == 2 else [W * 0.15 + W * 0.7 * c / (cols - 1) for c in range(cols)]
            drum_col = cols // 2
            order = [c for c in range(cols) if c != drum_col] if "drums" in plays else list(range(cols))
            spots, j = [], 0
            for p_ in plays:
                if p_ == "drums" and "drums" in plays:
                    spots.append((xs[drum_col], scale * 0.86, FLOOR - 120.0))  # a low riser: the head stays under the middle
                else:
                    spots.append((xs[order[j]] if j < len(order) else W / 2, scale, float(FLOOR)))
                    j += 1
        year = self.year_of(i)
        for k, m in enumerate(ms):
            look = self.look_in(m["name"], year)
            play = plays[k]
            x, s, foot = spots[k]
            shown = -1.0 if (first or (m["name"] in prev_names and not m.get("cue"))) else at(m.get("cue"), k)
            items.append({"type": "member", "at": shown, "name": m["name"], "look": look, "play": play, "x": x, "s": s,
                          "foot": foot, "expr": str(m.get("expr") or "smile"), "pose": str(m.get("pose") or "hang"),
                          "seed": sum(map(ord, m["name"])) % 23, "tag": bool(m.get("tag", m["name"] not in self.seen and not m["name"].startswith("_"))),
                          "move": str(m.get("move") or ""), "move_at": shown if shown >= 0 else scene.start + 0.3,
                          "from_prev": m["name"] in prev_names, "hops": []})
            self.seen.add(m["name"])
            said = str(m.get("say") or m.get("think") or "").strip()[:BUBBLE_TEXT + 4]
            if said:
                kind = "think" if m.get("think") and not m.get("say") else "shout" if items[-1]["expr"] in ("angry", "shocked") or said.endswith("!!") else "say"
                if first and not m.get("say_cue") and not any(it["type"] == "bubble" for it in items):
                    when = -1.0  # on the very first frame: the hook speaks before the voice does
                else:
                    when = at(m.get("say_cue"), k) if m.get("say_cue") else (shown + 0.35 if shown >= 0 else scene.start + 0.35 + 0.9 * k)
                items.append({"type": "bubble", "at": when, "text": said.upper(), "kind": kind, "who": items[-1]})
        if empty:
            items.append({"type": "emptymic", "at": -1.0})
        items.sort(key=lambda it: it.get("play") != "drums")  # the drummer behind
        if o.get("year"):
            same = i and self.spec.scenes[i - 1].opts.get("year") == o.get("year")
            items.append({"type": "year", "at": -1.0 if first or same else scene.start + 0.05, "text": str(o["year"]), "slam": not same})
        if o.get("line"):
            items.append({"type": "line", "at": -1.0 if first else at(o.get("line_cue"), 1), "text": str(o["line"])})
        props = [pr for pr in (o.get("props") or []) if isinstance(pr, dict) and pr.get("kind") in PROPS][:2]
        centre = [pr for pr in props if not any(it["type"] == "member" and it["name"] == pr.get("who") for it in items)]
        big_y = 820.0 if o.get("sign") else 690.0
        big_x = W * 0.36 if centre and shot != "close" else W / 2  # a prop beside it, not under it
        if isinstance(o.get("big"), dict):
            items.append({"type": "big", "at": at(o["big"].get("cue"), 0), "y": big_y, "x": big_x, "text": str(o["big"]["text"]),
                          "color": pw_world.COLORS.get(str(o["big"].get("color") or "gold"), GOLD)})
        for k, pr in enumerate(props):
            who = next((it for it in items if it["type"] == "member" and it["name"] == pr.get("who")), None)
            if who:  # over that member's head
                x, y, sc = min(W - 150, max(150, who["x"])), who["foot"] - 560 * who["s"] - 140, 0.8
            elif shot == "duo":  # above the two big heads, clear of the cards and the studio's sign
                x, y, sc = W * (0.5 if len(centre) < 2 else 0.32 + 0.33 * k), 650.0, 0.58
            elif shot == "close":  # beside the head, on the left (the studio's sign is on the right)
                x, y, sc = W * (0.18 if k == 0 else 0.82), 780.0, 0.72
            elif o.get("big"):
                x, y, sc = W * 0.8, 690.0, 0.85
            else:
                x, y, sc = W * (0.5 if len(centre) < 2 else 0.28 + 0.44 * k), 700.0, 0.85 if len(centre) < 2 else 0.75
            items.append({"type": "prop", "at": at(pr.get("cue"), k), "kind": pr["kind"], "text": str(pr.get("text") or "")[:16],
                          "x": x, "y": y, "sc": sc, "k": k})
        if isinstance(o.get("poll"), dict) and o["poll"].get("a") and o["poll"].get("b"):
            items.append({"type": "poll", "at": at(o["poll"].get("cue"), 3), "a": str(o["poll"]["a"]), "b": str(o["poll"]["b"])})
        if isinstance(o.get("news"), dict):
            items.append({"type": "news", "at": at(o["news"].get("cue"), 0), "text": str(o["news"]["text"])})
        if isinstance(o.get("crack"), dict) and n >= 2:
            items.append({"type": "crack", "at": at(o["crack"].get("cue"), 2)})
        for k, st in enumerate(o.get("stamps") or []):
            items.append({"type": "stamp", "at": at(st.get("cue"), k), "text": str(st.get("text") or "NO"), "k": k})
        bubbles = sorted((it for it in items if it["type"] == "bubble"), key=lambda it: it["at"])
        for b in bubbles:  # said on the scene's last word: a moment earlier, so it is read before the cut
            if b["at"] >= 0:
                b["at"] = max(scene.start + 0.2, min(b["at"], scene.end - 1.1))
        for b0, b1 in zip(bubbles, bubbles[1:]):  # they take turns: one bubble on screen at a time, each read for 0.9 s
            b1["at"] = max(b1["at"], max(b0["at"], scene.start + 0.3) + 0.9)
        papers = [it["at"] for it in items if it["type"] in ("news", "poll") and it["at"] >= 0]
        for k, b in enumerate(bubbles):
            b["end"] = bubbles[k + 1]["at"] - 0.04 if k + 1 < len(bubbles) else scene.end + 0.3
            b["end"] = min([b["end"]] + [p_ for p_ in papers if p_ > b["at"]])  # a front page or the question clears it
            if b["at"] < scene.end - 0.3:
                b["who"]["hops"].append(b["at"])  # whoever speaks hops a little
        for it in items:  # a front page or a stamp lands: the band jumps
            if it["type"] in ("news", "stamp") and it["at"] >= 0:
                for m in items:
                    if m["type"] == "member" and m["play"] != "drums":
                        m["hops"].append(it["at"] + 0.08)
        return items

    def year_of(self, i: int) -> int | None:
        """The year a scene is in: its own year card, else the last one before it (the hook has none)."""
        for sc in self.spec.scenes[i::-1]:
            got = re.search(r"\b(19|20)\d\d\b", str(sc.opts.get("year") or ""))
            if got:
                return int(got.group())
        return None

    def look_in(self, name: str, year: int | None) -> dict:
        """A member as they looked that year: the look is how fans picture them (used with no year, in the hook),
        "eras" changes it for a span ([{"until": 1979, "beard": "none"}]: no moustache before 1980)."""
        look = dict(self.looks.get(name) or {})
        eras = look.pop("eras", None) or []
        if year is not None:
            for era in eras if isinstance(eras, list) else []:
                if not isinstance(era, dict):
                    continue
                if int(era.get("from") or 0) <= year <= int(era.get("until") or 9999):
                    look.update({k: v for k, v in era.items() if k not in ("from", "until")})
        return look

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
        self.items_now, self.set_now = items, name
        shake = 0.0
        for it in items:
            if it["type"] == "crack" and 0 < t - it["at"] < 0.4:
                shake = 10 * (1 - (t - it["at"]) / 0.4) * math.sin(t * 90)
        for it in items:
            if it["type"] == "emptymic":
                put(img, empty_mic(), (W / 2, FLOOR - 170))
        for it in items:
            if it["type"] == "member":
                self.draw_member(img, it, t, shake)
        if name == "stadium" or o.get("crowd"):
            crowd(img, t, (self.spec.seed or 7) + i, dense=name == "stadium")
        elif name == "club":
            crowd(img, t, (self.spec.seed or 7) + i, dense=False)
        self.notes(img, t, items)
        poll = next((it for it in items if it["type"] == "poll" and 0 <= it["at"] <= t), None)
        for it in sorted(items, key=lambda it: it["type"] == "bubble"):  # bubbles on top
            if it["type"] in ("member", "emptymic"):
                continue
            if poll and it["type"] in ("big", "news", "prop", "stamp", "bubble") and not (it["type"] == "prop" and it["sc"] < 0.65):
                fade = 1 - min(1.0, (t - poll["at"]) / 0.25)  # the question takes the middle of the screen
                if fade <= 0:
                    continue
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

    @staticmethod
    def motion(it: dict[str, Any], t: float) -> tuple[float, float, float, float]:
        """How a member moves on top of playing: (dx, dy, angle, width) in screen pixels, degrees and a 1 = normal
        width (below 0: turned round, mid-spin)."""
        k = it["s"]
        dx = dy = ang = 0.0
        sx = 1.0
        for h in it["hops"]:  # a little hop when they speak or a front page lands
            u = (t - h) / 0.34
            if 0 <= u <= 1:
                dy -= 46 * k * 4 * u * (1 - u)
        mv, m0 = it["move"], it["move_at"]
        if mv == "jump":
            ph = (t / (2 * BEAT) + it["seed"] * 0.1) % 1.0
            dy -= 70 * k * 4 * ph * (1 - ph)
        elif mv == "bounce":
            dy -= 22 * k * abs(math.sin(math.pi * t / BEAT))
        elif mv == "lean":
            ang = 8 * math.sin(math.pi * t / BEAT + it["seed"])
        elif mv == "shake":
            dx += 7 * k * math.sin(t * 55)
            ang = 2.5 * math.sin(t * 41)
        elif mv == "spin" and t >= m0:
            u = ((t - m0) % (8 * BEAT)) / 0.6  # a turn on the spot every two bars
            if u < 1:
                sx = math.cos(2 * math.pi * ease(u))
                dy -= 30 * k * math.sin(math.pi * u)
        elif mv == "slide" and not it["from_prev"]:
            u = (t - m0) / 0.5
            if u < 1:  # in from the nearer side of the screen
                dx += (1 - ease(max(0.0, u))) * (-1 if it["x"] < W / 2 else 1) * W * 0.75
        return dx, dy, ang, sx

    def draw_member(self, img: Image.Image, it: dict[str, Any], t: float, shake: float) -> None:
        s, a = self.pop(it, t)
        if it["move"] == "slide" and not it["from_prev"]:
            s, a = (1.0, 1.0) if t >= it["move_at"] else (0.0, 0.0)  # it slides in instead of popping
        if s <= 0:
            return
        k = it["s"] * s
        dx, dy, ang, sx = self.motion(it, t) if it["play"] != "drums" else (0.0, 0.0, 0.0, 1.0)
        if it["play"] == "drums":
            spr = member(it["look"], t, "drums", it["expr"], it["pose"], it["seed"])
            put(img, spr, (it["x"], it["foot"] - SH * k / 2 + 40 * k), scale=k, alpha=a)
            kit = drum_kit(t, str(self.band.get("name") or ""))
            put(img, kit, (it["x"], it["foot"] + 10 - kit.height * k * 0.5), scale=k * 1.1, alpha=a)
        else:
            spr = member(it["look"], t, it["play"], it["expr"], it["pose"], it["seed"])
            if abs(sx) < 0.999:
                spr = spr.resize((max(1, int(spr.width * abs(sx))), spr.height), Image.BILINEAR)
                if sx < 0:
                    spr = spr.transpose(Image.FLIP_LEFT_RIGHT)
            put(img, spr, (it["x"] + shake + dx, it["foot"] - SH * k / 2 + dy), scale=k, alpha=a, angle=ang)
        if it["tag"]:
            name_pill = pill(it["name"].upper(), "dark", 30)
            y = min(it["foot"] + 52, 1440) if it["play"] != "drums" else it["foot"] - 640 * it["s"]
            put(img, name_pill, (it["x"] + dx, y), alpha=a)

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
            parts = pw_world.number_parts(it["text"])
            text = it["text"] if parts is None or parts[1] < 10 else counted(it["text"], ease(u))  # "#1" never shows "#0"
            put(img, big_text(text, 150, it["color"]), (it.get("x", W / 2), it["y"]), scale=s, alpha=a)
        elif kind == "news":
            put(img, news_sprite(it["text"]), (W / 2, 775), scale=0.86 * s, alpha=a)
        elif kind == "prop":
            bob = 8 * math.sin(t * 3 + it["k"])
            put(img, prop_sprite(it["kind"], it["text"], round(t * 12) / 12), (it["x"], it["y"] + bob), scale=it["sc"] * s, alpha=a)
        elif kind == "poll":
            for k, (text, style) in enumerate(((it["a"], "red"), (it["b"], "blue"))):
                beat = 1 + 0.05 * max(0.0, math.sin(math.pi * 2 * (t / BEAT) + k * math.pi))
                put(img, poll_sprite(text, style), (W / 2, 640 + k * 190), scale=s * beat, alpha=a)
            put(img, text_sprite("OR", 64, GOLD, "Black"), (W / 2, 735), scale=s, alpha=a)
        elif kind == "bubble":
            self.draw_bubble(img, it, t, s, a)
        elif kind == "crack":
            put(img, crack_sprite(), (W / 2, 1050), scale=s, alpha=a * (0.75 + 0.25 * math.sin(t * 30)))
        elif kind == "stamp":
            put(img, pw_world.stamp(it["text"]), (W / 2, 700 + it["k"] * 150), scale=1 + 0.8 * (1 - s) if s < 1 else 1.0, alpha=a)

    def busy(self, t: float, skip: dict[str, Any]) -> list[tuple[float, float, float, float]]:
        """Where things already are on screen at t (the title, cards, props, figures, papers, heads): a bubble keeps
        off them."""
        boxes = [(0.0, 0.0, W, 285.0)]
        if self.set_now == "studio":  # the RECORDING light on the wall
            boxes.append((W - 300.0, 650.0, W, 750.0))
        for o in self.items_now:
            if o is skip or (o["at"] >= 0 and o["at"] > t) or o["type"] in ("bubble", "emptymic", "crack", "poll"):
                continue
            ty = o["type"]
            if ty == "year":
                boxes.append((W / 2 - 300, 290, W / 2 + 300, 450))
            elif ty == "line":
                boxes.append((W / 2 - 380, 465, W / 2 + 380, 548))
            elif ty == "prop":
                r = 165 * o["sc"]
                boxes.append((o["x"] - r, o["y"] - r, o["x"] + r, o["y"] + r))
            elif ty == "big":
                hw = 50 * len(o["text"]) + 40
                boxes.append((o["x"] - hw, o["y"] - 90, o["x"] + hw, o["y"] + 90))
            elif ty == "news":
                boxes.append((W / 2 - 360, 775 - 200, W / 2 + 360, 775 + 200))
            elif ty == "stamp":
                y = 700 + o["k"] * 150
                boxes.append((W / 2 - 45 * len(o["text"]) - 40, y - 65, W / 2 + 45 * len(o["text"]) + 40, y + 65))
            elif ty == "member":
                k = o["s"]
                head = o["foot"] - (570 if o["play"] == "drums" else 610) * k
                boxes.append((o["x"] - 135 * k, head + 20 * k, o["x"] + 135 * k, head + 270 * k))
        return boxes

    def draw_bubble(self, img: Image.Image, it: dict[str, Any], t: float, s: float, a: float) -> None:
        """A speech bubble over the member who speaks, its tail at their head; it pops from the tail and goes when
        the next one comes. Of a few places round the head, it takes the one that covers the least."""
        if t >= it["end"]:
            return
        a *= min(1.0, (it["end"] - t) / 0.12)
        m = it["who"]
        k = m["s"]
        dx, dy, _, _ = self.motion(m, t) if m["play"] != "drums" else (0.0, 0.0, 0.0, 1.0)
        if "spot" not in it:  # chosen once, where the member stands still
            head = m["foot"] - (570 if m["play"] == "drums" else 610) * k
            boxes = self.busy(it["end"], m)  # clear of what is there and of what comes while it is read
            mid = 1 if m["x"] <= W / 2 else -1
            best = None
            for size in ((1.3, 1.0, 0.8) if k >= 1.2 else (1.0, 0.8)):  # big faces get big bubbles
                probe = bubble_sprite(it["text"], it["kind"], 0.5)
                w, h = probe.width * size, probe.height * size
                for side in (mid, -mid):
                    for drop in (0, 70, 150):
                        tx, ty = m["x"] + side * 70 * k, head - 6 + drop * k
                        left = min(W - 24 - w, max(24.0, tx - (0.22 if side > 0 else 0.78) * w))
                        r = (left, ty - h, left + w, ty)
                        cover = sum(max(0.0, min(r[2], bx[2]) - max(r[0], bx[0])) * max(0.0, min(r[3], bx[3]) - max(r[1], bx[1]))
                                    for bx in boxes)
                        score = cover + (1 - size) * 30000 + drop * 40 + (side != mid) * 3000
                        if best is None or score < best[0]:
                            best = (score, tx, ty, left, w, size)
            _, tx, ty, left, w, size = best
            it["spot"] = (tx - m["x"], ty, left - m["x"], size, round(min(0.85, max(0.15, (tx - left) / w)) * 20) / 20)
        ox, ty, lx, size, frac = it["spot"]
        spr = bubble_sprite(it["text"], it["kind"], frac)
        tip = (m["x"] + dx + ox, ty + dy)
        w, h = spr.width * size, spr.height * size
        cx, cy = m["x"] + dx + lx + w / 2, tip[1] - h / 2
        sc = s * (1 + 0.025 * math.sin(t * 7))
        put(img, spr, (tip[0] + (cx - tip[0]) * sc, tip[1] + (cy - tip[1]) * sc), scale=sc * size, alpha=a)

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
    """A front page: a long headline goes on two lines, so it stays big."""
    w, h = 680, 380
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w * SS, h * SS], fill=(246, 242, 228), outline=OUTLINE, width=5 * SS)
    d.text((w / 2 * SS, 46 * SS), "THE DAILY NEWS", font=font(int(40 * SS), "Black"), fill=INK, anchor="mm")
    d.line([(20 * SS, 80 * SS), ((w - 20) * SS, 80 * SS)], fill=INK, width=4 * SS)
    words = text.split()
    lines = [text]
    if len(text) > 13 and len(words) > 1:
        cut = min(range(1, len(words)), key=lambda c: abs(len(" ".join(words[:c])) - len(" ".join(words[c:]))))
        lines = [" ".join(words[:cut]), " ".join(words[cut:])]
    size = 96 if len(lines) == 1 else 84
    f = font(int(size * SS), "Black")
    k = min(1.0, (w - 50) * SS / max(1.0, max(f.getlength(ln) for ln in lines)))
    f = font(int(size * SS * k), "Black")
    for j, ln in enumerate(lines):
        y = 165 if len(lines) == 1 else 140 + j * 96 * k
        d.text((w / 2 * SS, y * SS), ln, font=f, fill=RED, anchor="mm")
    for y in range(300 if len(lines) > 1 else 240, h - 20, 20):
        d.line([(30 * SS, y * SS), ((w - 30) * SS, y * SS)], fill=(170, 166, 156), width=6 * SS)
    return shrink(img).rotate(-4, expand=True, resample=Image.BICUBIC)


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


@lru_cache(maxsize=64)
def bubble_sprite(text: str, kind: str = "say", tail: float = 0.25) -> Image.Image:
    """A comic speech bubble: "say" round, "shout" spiky (angry, shocked), "think" a cloud with dots. Its tail's tip is
    at the bottom edge, `tail` of the way across."""
    f = font(int(50 * SS), "Black")
    words, lines = text.split(), []
    for wd in words:  # two lines at most, about 11 characters each
        if lines and len(lines[-1]) + 1 + len(wd) <= 11:
            lines[-1] += " " + wd
        else:
            lines.append(wd)
    if len(lines) > 2:
        lines = [" ".join(words[: len(words) // 2]), " ".join(words[len(words) // 2:])]
    tw = max(f.getlength(ln) for ln in lines)
    lh = 58 * SS
    pad_x, pad_y = (70 if kind == "shout" else 52) * SS, (44 if kind == "shout" else 30) * SS
    bw, bh = int(tw + 2 * pad_x), int(lh * len(lines) + 2 * pad_y)
    tail_h = 56 * SS
    img = Image.new("RGBA", (bw + 16 * SS, bh + tail_h + 16 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    o = 6 * SS
    tx = bw * tail
    lw = int(6 * SS)
    paper = (255, 255, 255) if kind != "shout" else (255, 236, 120)
    for shadow in (True, False):
        off = 8 * SS if shadow else 0
        fill = (0, 0, 0, 90) if shadow else paper
        line = None if shadow else OUTLINE
        if kind == "shout":
            pts = []
            n = 22
            for j in range(n):
                ang = 2 * math.pi * j / n
                r = 1.0 if j % 2 == 0 else 0.8
                pts.append((o + bw / 2 + math.cos(ang) * bw / 2 * r + off, o + bh / 2 + math.sin(ang) * bh / 2 * r + off))
            d.polygon(pts, fill=fill, outline=line, width=lw if line else 0)
            d.polygon([(o + tx - 26 * SS + off, o + bh - 20 * SS + off), (o + tx + 26 * SS + off, o + bh - 20 * SS + off),
                       (o + tx + off, o + bh + tail_h - 6 * SS + off)], fill=fill, outline=line, width=lw if line else 0)
        elif kind == "think":
            for j in range(12):  # a cloud of bumps round an ellipse
                ang = 2 * math.pi * j / 12
                bx, by = o + bw / 2 + math.cos(ang) * bw * 0.42 + off, o + bh / 2 + math.sin(ang) * bh * 0.36 + off
                r = min(bw, bh) * 0.24
                d.ellipse([bx - r, by - r, bx + r, by + r], fill=fill, outline=line, width=lw if line else 0)
            d.ellipse([o + bw * 0.1 + off, o + bh * 0.12 + off, o + bw * 0.9 + off, o + bh * 0.88 + off], fill=fill)
            for j, r in enumerate((16, 11)):
                cy = o + bh + 14 * SS + j * 30 * SS + off
                d.ellipse([o + tx - r * SS + off, cy - r * SS, o + tx + r * SS + off, cy + r * SS], fill=fill, outline=line, width=lw if line else 0)
        else:
            d.rounded_rectangle([o + off, o + off, o + bw + off, o + bh + off], radius=bh // 2, fill=fill, outline=line, width=lw if line else 0)
            d.polygon([(o + tx - 30 * SS + off, o + bh - 4 * SS + off), (o + tx + 24 * SS + off, o + bh - 4 * SS + off),
                       (o + tx - 10 * SS + off, o + bh + tail_h - 6 * SS + off)], fill=fill)
            if line:
                d.line([(o + tx - 30 * SS, o + bh - 1 * SS), (o + tx - 10 * SS, o + bh + tail_h - 6 * SS), (o + tx + 24 * SS, o + bh - 1 * SS)],
                       fill=line, width=lw, joint="curve")
    if kind == "think":  # the cloud's inner bumps covered: one clean fill
        d.ellipse([o + bw * 0.12, o + bh * 0.14, o + bw * 0.88, o + bh * 0.86], fill=paper)
    for j, ln in enumerate(lines):
        d.text((o + bw / 2, o + pad_y + lh * (j + 0.5)), ln, font=f, fill=INK if kind != "shout" else (170, 20, 30), anchor="mm")
    return shrink(img)


PROPS = ("vinyl", "album", "radio", "tv", "globe", "phone", "clock", "trophy", "ticket", "tooth", "telescope", "crown")
PROP_TEXT = 14  # characters a prop's text may have


@lru_cache(maxsize=1)
def empty_mic() -> Image.Image:
    """The singer's microphone alone on its stand."""
    w, h = 160, 380
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = SS
    d.line([(80 * u, 70 * u), (80 * u, 360 * u)], fill=(150, 150, 160), width=int(9 * u))
    d.line([(36 * u, 362 * u), (124 * u, 362 * u)], fill=(150, 150, 160), width=int(12 * u))
    d.rounded_rectangle([62 * u, 10 * u, 98 * u, 74 * u], radius=18 * u, fill=(70, 70, 80), outline=OUTLINE, width=int(4 * u))
    for y in (26, 40, 54):
        d.line([(66 * u, y * u), (94 * u, y * u)], fill=(120, 120, 130), width=int(3 * u))
    return shrink(img)


@lru_cache(maxsize=8)
def poll_sprite(text: str, style: str) -> Image.Image:
    """One answer of the closing question, a big button."""
    fill = {"red": (214, 52, 64), "blue": (52, 104, 214)}.get(style, (214, 52, 64))
    f = font(int(62 * SS), "Black")
    k = min(1.0, (W - 160) * SS / max(1.0, f.getlength(text)))
    f = font(int(62 * SS * k), "Black")
    w, h = int(f.getlength(text)) + 90 * SS, int(120 * SS)
    img = Image.new("RGBA", (w + 10 * SS, h + 14 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6 * SS, 12 * SS, w + 6 * SS, h + 12 * SS], radius=h // 2, fill=(0, 0, 0, 130))
    d.rounded_rectangle([0, 0, w, h], radius=h // 2, fill=fill, outline=WHITE, width=6 * SS)
    d.text((w / 2, h / 2), text, font=f, fill=WHITE, anchor="mm")
    return shrink(img)


@lru_cache(maxsize=64)
def prop_sprite(kind: str, text: str = "", t: float = 0.0) -> Image.Image:
    """A thing that shows what the narrator just said: a record, a radio, a TV, the world, a phone's play count..."""
    w, h = 340, 340
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = SS
    lw = int(6 * u)

    def B(x0, y0, x1, y1):
        return [x0 * u, y0 * u, x1 * u, y1 * u]

    def label(x, y, size, color=INK, max_w=260):
        if not text:
            return
        f = font(int(size * u), "Black")
        k = min(1.0, max_w * u / max(1.0, f.getlength(text)))
        d.text((x * u, y * u), text, font=font(int(size * u * k), "Black"), fill=color, anchor="mm")

    pill_under = False
    if kind == "vinyl":
        rec = Image.new("RGBA", img.size, (0, 0, 0, 0))
        r_ = ImageDraw.Draw(rec)
        r_.ellipse(B(20, 20, 320, 320), fill=(24, 24, 28), outline=OUTLINE, width=lw)
        for r in range(60, 145, 14):
            r_.ellipse(B(170 - r, 170 - r, 170 + r, 170 + r), outline=(60, 60, 70), width=int(2 * u))
        r_.ellipse(B(110, 110, 230, 230), fill=(214, 52, 64))
        r_.arc(B(40, 40, 300, 300), 200, 250, fill=(140, 140, 160), width=int(6 * u))
        r_.ellipse(B(164, 164, 176, 176), fill=OUTLINE)
        img = rec.rotate(-t * 200, resample=Image.BICUBIC)
        d = ImageDraw.Draw(img)
        if text:
            f = font(int(22 * u), "Black")
            k = min(1.0, 100 * u / max(1.0, f.getlength(text)))
            d.text((170 * u, 146 * u), text, font=font(int(22 * u * k), "Black"), fill=WHITE, anchor="mm")
    elif kind == "album":
        d.rectangle(B(30, 30, 310, 310), fill=(236, 196, 64), outline=OUTLINE, width=lw)
        d.ellipse(B(90, 60, 250, 220), fill=(214, 52, 64), outline=OUTLINE, width=lw)
        d.ellipse(B(150, 120, 190, 160), fill=(236, 196, 64))
        d.rectangle(B(30, 236, 310, 310), fill=(34, 30, 50))
        label(170, 273, 38, WHITE)
    elif kind == "radio":
        for k in range(3):  # sound waves going out
            ph = (t * 1.6 + k / 3) % 1.0
            r = 40 + ph * 90
            d.arc(B(250 - r, 90 - r, 250 + r, 90 + r), 290, 340, fill=(255, 255, 255, int(255 * (1 - ph))), width=int(7 * u))
        d.line([(110 * u, 150 * u), (70 * u, 40 * u)], fill=(150, 150, 160), width=int(6 * u))
        d.rounded_rectangle(B(30, 140, 310, 310), radius=30 * u, fill=(176, 64, 52), outline=OUTLINE, width=lw)
        d.ellipse(B(56, 172, 166, 282), fill=(240, 220, 190), outline=OUTLINE, width=lw)
        for y in range(190, 270, 16):
            d.line([(70 * u, y * u), (152 * u, y * u)], fill=(176, 150, 120), width=int(4 * u))
        d.rounded_rectangle(B(186, 176, 290, 226), radius=8 * u, fill=(250, 236, 180), outline=OUTLINE, width=int(4 * u))
        d.line([(210 * u, 180 * u), (210 * u, 222 * u)], fill=(214, 52, 64), width=int(4 * u))
        d.ellipse(B(206, 244, 238, 276), fill=(60, 50, 50))
        d.ellipse(B(250, 244, 282, 276), fill=(60, 50, 50))
        pill_under = True
    elif kind == "tv":
        d.line([(170 * u, 70 * u), (110 * u, 10 * u)], fill=(150, 150, 160), width=int(6 * u))
        d.line([(170 * u, 70 * u), (230 * u, 10 * u)], fill=(150, 150, 160), width=int(6 * u))
        d.rounded_rectangle(B(20, 70, 320, 300), radius=26 * u, fill=(70, 60, 56), outline=OUTLINE, width=lw)
        d.rounded_rectangle(B(44, 92, 256, 278), radius=20 * u, fill=(120, 190, 230), outline=OUTLINE, width=int(4 * u))
        for y in range(98, 274, 8):
            d.line([(48 * u, y * u), (252 * u, y * u)], fill=(100, 170, 214), width=int(2 * u))
        if int(t * 2) % 2 == 0:
            d.ellipse(B(60, 106, 80, 126), fill=(230, 40, 50))
        d.text((90 * u, 116 * u), "LIVE", font=font(int(22 * u), "Black"), fill=WHITE, anchor="lm")
        label(150, 190, 44, WHITE, 190)
        for y in (130, 190):
            d.ellipse(B(270, y, 300, y + 30), fill=(40, 34, 30))
    elif kind == "globe":
        d.ellipse(B(30, 20, 310, 300), fill=(60, 140, 220), outline=OUTLINE, width=lw)
        rot = (t * 30) % 280
        for cx_, cy_, rx_, ry_ in ((90, 110, 50, 60), (200, 90, 60, 40), (230, 200, 40, 60), (120, 230, 50, 30)):
            x = 30 + (cx_ - 30 + rot) % 280
            if 50 < x < 290:
                d.ellipse(B(x - rx_ * 0.8, cy_ - ry_, x + rx_ * 0.8, cy_ + ry_), fill=(90, 190, 110))
        d.arc(B(30, 20, 310, 300), 0, 360, fill=OUTLINE, width=lw)
        d.arc(B(60, 40, 200, 180), 190, 250, fill=(220, 240, 255), width=int(8 * u))
        pill_under = True
    elif kind == "phone":
        d.rounded_rectangle(B(90, 10, 250, 330), radius=26 * u, fill=(30, 30, 36), outline=OUTLINE, width=lw)
        d.rounded_rectangle(B(102, 36, 238, 304), radius=12 * u, fill=(250, 250, 252))
        d.ellipse(B(126, 70, 214, 158), fill=(230, 40, 50))
        d.polygon([(156 * u, 92 * u), (156 * u, 136 * u), (192 * u, 114 * u)], fill=WHITE)
        for k in range(3):
            d.rounded_rectangle(B(114, 236 + k * 18, 226 - k * 30, 246 + k * 18), radius=4 * u, fill=(210, 210, 220))
        label(170, 196, 34, INK, 124)
    elif kind == "clock":
        d.rounded_rectangle(B(150, 10, 190, 50), radius=6 * u, fill=(150, 150, 160), outline=OUTLINE, width=int(4 * u))
        d.ellipse(B(40, 40, 300, 300), fill=WHITE, outline=OUTLINE, width=int(10 * u))
        for k in range(12):
            a = math.pi * 2 * k / 12
            d.line([((170 + 112 * math.sin(a)) * u, (170 - 112 * math.cos(a)) * u), ((170 + 126 * math.sin(a)) * u, (170 - 126 * math.cos(a)) * u)],
                   fill=OUTLINE, width=int(5 * u))
        a = t * 4
        d.line([(170 * u, 170 * u), ((170 + 96 * math.sin(a)) * u, (170 - 96 * math.cos(a)) * u)], fill=(214, 52, 64), width=int(6 * u))
        label(170, 226, 52, INK, 170)
    elif kind == "trophy":
        d.arc(B(30, 50, 130, 170), 90, 270, fill=(200, 160, 40), width=int(16 * u))
        d.arc(B(210, 50, 310, 170), 270, 90, fill=(200, 160, 40), width=int(16 * u))
        d.chord(B(70, -40, 270, 200), 0, 180, fill=(236, 196, 64), outline=OUTLINE, width=lw)
        d.rectangle(B(150, 196, 190, 250), fill=(220, 180, 50), outline=OUTLINE, width=int(4 * u))
        d.rounded_rectangle(B(80, 246, 260, 320), radius=10 * u, fill=(60, 44, 34), outline=OUTLINE, width=lw)
        label(170, 283, 30, (240, 210, 120), 160)
        d.polygon([(110 * u, 70 * u), (122 * u, 60 * u), (126 * u, 120 * u)], fill=(255, 240, 180))
    elif kind == "ticket":
        d.rounded_rectangle(B(10, 90, 330, 250), radius=14 * u, fill=(250, 226, 120), outline=OUTLINE, width=lw)
        for y in (90, 250):
            d.ellipse(B(236, y - 18, 272, y + 18), fill=(0, 0, 0, 0))
        for y in range(104, 240, 18):
            d.line([(254 * u, y * u), (254 * u, (y + 9) * u)], fill=OUTLINE, width=int(4 * u))
        d.text((130 * u, 130 * u), "ADMIT ONE", font=font(int(26 * u), "Black"), fill=(214, 52, 64), anchor="mm")
        label(130, 190, 40, INK, 210)
    elif kind == "tooth":  # one smooth outline: two bumps on top, two roots below
        pts = [(80, 120), (88, 70), (120, 46), (150, 52), (170, 70), (190, 52), (220, 46), (252, 70), (260, 120),
               (250, 200), (236, 290), (204, 300), (188, 230), (170, 214), (152, 230), (136, 300), (104, 290), (90, 200)]
        d.polygon([(x * u, y * u) for x, y in pts], fill=WHITE, outline=OUTLINE, width=lw)
        d.arc(B(108, 70, 160, 130), 200, 260, fill=(200, 220, 235), width=int(6 * u))
        for x, y, r in ((280, 60, 16), (300, 120, 9)):
            d.polygon([((x) * u, (y - r) * u), ((x + r * 0.3) * u, (y - r * 0.3) * u), ((x + r) * u, y * u), ((x + r * 0.3) * u, (y + r * 0.3) * u),
                       (x * u, (y + r) * u), ((x - r * 0.3) * u, (y + r * 0.3) * u), ((x - r) * u, y * u), ((x - r * 0.3) * u, (y - r * 0.3) * u)], fill=(255, 230, 90))
        pill_under = True
    elif kind == "telescope":
        d.line([(150 * u, 220 * u), (90 * u, 330 * u)], fill=(90, 60, 36), width=int(10 * u))
        d.line([(150 * u, 220 * u), (210 * u, 330 * u)], fill=(90, 60, 36), width=int(10 * u))
        d.line([(150 * u, 220 * u), (150 * u, 330 * u)], fill=(90, 60, 36), width=int(10 * u))
        d.polygon([(40 * u, 250 * u), (230 * u, 120 * u), (258 * u, 162 * u), (66 * u, 290 * u)], fill=(64, 110, 190), outline=OUTLINE)
        d.polygon([(220 * u, 110 * u), (260 * u, 84 * u), (296 * u, 140 * u), (256 * u, 168 * u)], fill=(236, 196, 64), outline=OUTLINE)
        x, y, r = 290, 40, 30
        tw = 0.8 + 0.2 * math.sin(t * 8)
        d.polygon([(x * u, (y - r * tw) * u), ((x + 8) * u, (y - 8) * u), ((x + r * tw) * u, y * u), ((x + 8) * u, (y + 8) * u),
                   (x * u, (y + r * tw) * u), ((x - 8) * u, (y + 8) * u), ((x - r * tw) * u, y * u), ((x - 8) * u, (y - 8) * u)], fill=(255, 230, 90))
        pill_under = True
    elif kind == "crown":
        pts = [(40, 270), (40, 110), (100, 190), (170, 70), (240, 190), (300, 110), (300, 270)]
        d.polygon([(x * u, y * u) for x, y in pts], fill=(236, 196, 64), outline=OUTLINE)
        d.line([(x * u, y * u) for x, y in pts + [pts[0]]], fill=OUTLINE, width=lw)
        d.rectangle(B(40, 236, 300, 270), fill=(214, 170, 40), outline=OUTLINE, width=int(4 * u))
        for x, col in ((100, (214, 52, 64)), (170, (52, 104, 214)), (240, (60, 170, 90))):
            d.ellipse(B(x - 14, 238, x + 14, 266), fill=col)
        for x, y in ((40, 110), (170, 70), (300, 110)):
            d.ellipse(B(x - 14, y - 14, x + 14, y + 14), fill=WHITE, outline=OUTLINE, width=int(3 * u))
        pill_under = True
    out = shrink(img)
    if pill_under and text:
        tag = pill(text.upper(), "gold", 40)
        both = Image.new("RGBA", (max(out.width, tag.width), out.height + tag.height - 10), (0, 0, 0, 0))
        both.alpha_composite(out, ((both.width - out.width) // 2, 0))
        both.alpha_composite(tag, ((both.width - tag.width) // 2, out.height - 10))
        out = both
    return out


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
