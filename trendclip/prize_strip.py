"""The drawn strip under a prize Short's card (the owner, 2026-11-13: "combine our animation with the fast cards").

The card over the parkour stays exactly as in the Block winner; under the captions a window into the quest world
shows the hero running with whatever the card shows, frame for frame: a pet runs beside him, armor or food is held
up and glows in its rarity's colour. A paused viewer sees the card and the hero with their prize; nothing new to
read. The window's frame takes the card's rarity colour too.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

SIZE = (1080, 300)
SCALE = 4  # one world pixel on screen
LW, LH = SIZE[0] // SCALE, SIZE[1] // SCALE
GROUND = 70  # the grass line, in world pixels
HERO_X = 64
RUN = 34.0  # world pixels a second
CENTER = (540, 1565)  # just under the captions (they end at 1400), above most of YouTube's own title
BORDER = 12


def _world(t: float, rng_seed: int = 7):
    """Sky, sun, clouds and a scrolling grass-and-dirt ground, at world resolution."""
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (LW, LH), (0, 0, 0, 255))
    d = ImageDraw.Draw(im)
    for y in range(GROUND):  # a bright day: deep blue to pale
        k = y / GROUND
        d.line([(0, y), (LW, y)], fill=(int(70 + 110 * k), int(150 + 70 * k), 255))
    d.rectangle([LW - 44, 8, LW - 30, 22], fill=(255, 238, 120))
    d.rectangle([LW - 42, 10, LW - 32, 20], fill=(255, 250, 190))
    scroll = t * RUN
    for i, (y, w) in enumerate(((14, 34), (30, 26), (8, 22))):
        x = (i * 110 + 40 - scroll * 0.25) % (LW + 60) - 40
        d.rectangle([x, y, x + w, y + 7], fill=(255, 255, 255))
        d.rectangle([x + 6, y - 4, x + w - 8, y], fill=(255, 255, 255))
    rng = random.Random(rng_seed)
    hills = [(rng.randint(0, 400), rng.randint(14, 26), rng.randint(40, 80)) for _ in range(6)]
    for hx, hh, hw in hills:  # far hills, slower
        x = (hx - scroll * 0.5) % 400 - 60
        d.rectangle([x, GROUND - hh, x + hw, GROUND], fill=(96, 178, 84))
        d.rectangle([x + 6, GROUND - hh - 5, x + hw - 6, GROUND - hh], fill=(96, 178, 84))
    off = int(scroll)
    for x in range(LW):
        wx = x + off
        d.line([(x, GROUND), (x, GROUND + 3)], fill=(92, 196, 60) if (wx // 3) % 5 else (70, 168, 46))
        for y in range(GROUND + 4, LH):
            n = ((wx // 2) * 73856093 ^ (y // 2) * 19349663) & 7
            d.point((x, y), fill=(134, 96, 60) if n > 1 else (112, 78, 48))
    for k in range(0, 2000, 47):  # flowers and tufts on the grass
        x = k - off % 2000
        if 0 <= x < LW:
            colour = ((255, 220, 40), (240, 60, 60), (250, 250, 250))[(k // 47) % 3]
            d.rectangle([x, GROUND - 3, x, GROUND - 1], fill=(60, 150, 40))
            d.rectangle([x - 1, GROUND - 4, x + 1, GROUND - 3], fill=colour)
    return im


def _hero(t: float, holding: bool):
    """The quest's hero mid-run; the front arm up when he holds something. Returns (picture, its corner, hand)."""
    from . import quest

    swing = 0.55 * math.sin(t * 11)
    arm = 2.5 if holding else -swing * 0.9  # 2.5: up and forward
    figure, hand = quest._figure(quest.HERO_LOOK, swing, swing * 0.9, arm, 0.0)
    figure = quest._outlined(figure)
    bob = 1 if abs(math.sin(t * 11)) > 0.75 else 0
    corner = (HERO_X - quest.FEET[0], GROUND - quest.FEET[1] - bob)
    return figure, corner, (corner[0] + hand[0], corner[1] + hand[1])


def _glow(sprite, colour: tuple, radius: int):
    from PIL import Image, ImageFilter

    pad = radius * 2
    halo = Image.new("RGBA", (sprite.width + 2 * pad, sprite.height + 2 * pad), (*colour, 0))
    alpha = Image.new("L", halo.size, 0)
    alpha.paste(sprite.getchannel("A"), (pad, pad))
    halo.putalpha(alpha.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(radius)))
    return halo, pad


def frame(entry: dict, noun: str, colour: tuple, t: float, cache: dict):
    """One strip picture: the world, the hero and this card's prize, framed in the card's rarity colour."""
    from PIL import Image, ImageDraw

    world = _world(t)
    figure, corner, hand = _hero(t, holding=noun != "pet")
    world.alpha_composite(figure, (int(corner[0]), int(corner[1])))
    img = world.resize(SIZE, Image.NEAREST)
    key = (entry["name"], noun)
    if key not in cache:
        pic = Image.open(entry["path"]).convert("RGBA")
        box = (250, 180) if noun == "pet" else (112, 112)
        k = min(box[0] / pic.width, box[1] / pic.height)
        cache[key] = pic.resize((max(1, round(pic.width * k)), max(1, round(pic.height * k))),
                                Image.NEAREST if max(pic.size) < 160 else Image.LANCZOS)
    pic = cache[key]
    if noun == "pet":  # it runs beside the hero, a little ahead, bobbing out of step
        x = HERO_X * SCALE + 130
        y = GROUND * SCALE - pic.height + 6 - int(8 * abs(math.sin(t * 11 + 1.3)))
    else:  # held up over his head
        x = hand[0] * SCALE - pic.width // 2 + 24
        y = max(18, hand[1] * SCALE - pic.height + 36 - int(5 * math.sin(t * 6)))
    x, y = int(x), int(y)
    halo, pad = _glow(pic, colour, 18)
    img.alpha_composite(halo, (max(0, x - pad), max(0, y - pad)))
    img.alpha_composite(pic, (max(0, x), max(0, y)))
    mask = Image.new("L", SIZE, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE[0] - 1, SIZE[1] - 1], radius=36, fill=255)
    out = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    d = ImageDraw.Draw(out)
    d.rounded_rectangle([0, 0, SIZE[0] - 1, SIZE[1] - 1], radius=36, outline=colour, width=BORDER)
    d.rounded_rectangle([BORDER, BORDER, SIZE[0] - 1 - BORDER, SIZE[1] - 1 - BORDER], radius=26,
                        outline=(20, 20, 26), width=4)
    return out


def save(entries: list[dict], noun: str, colours: list[tuple], hold: int, fps: int, path: Path) -> Path:
    """The strip as an animated WebP, one picture per card, in step with the card's own WebP."""
    cache: dict = {}
    frames = [frame(e, noun, c, i * hold / fps, cache) for i, (e, c) in enumerate(zip(entries, colours))]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=round(1000 * hold / fps), loop=0,
                   lossless=False, quality=88)
    return path
