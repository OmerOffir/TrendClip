"""Mobs that move: Minecraft's own mob models, built from their boxes and skins and animated (the owner, 2026-11-14:
"I want them to move as if we were playing, not only a picture of them").

Every mob in the game is a handful of boxes (head, body, arms, legs) with its skin wrapped around them by a fixed
layout. Here each box is a `Part` (size in texels, where its skin starts, where it sits, the joint it turns on);
a mob is its parts, and a pose is how far each part turns. The renderer is a tiny one: turn the corners, keep the
faces that look at the camera, paint them back to front, each face its piece of the skin stretched over it (pixel
for pixel, no smoothing) and shaded by which way it faces, as the game lights them.

Skins are the game's own textures from the Minecraft Wiki, fetched once into assets/mobs/. Animations follow the
game: walking swings the legs (and arms) opposite each other, a zombie holds its arms out, a hurt mob flashes red,
a dying one tips over onto its side red, then puffs into smoke.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings

SKINS = {  # mob -> its skin on the wiki (the newest Java one)
    "Zombie": "Zombie_(texture).png", "Husk": "Husk_(texture).png",
    "Drowned": "Drowned_(texture).png", "Skeleton": "Skeleton_(texture)_JE2_BE2.png",
    "Stray": "Stray_(texture).png", "Creeper": "Creeper_(texture)_JE2.png",
    "Pig": "Pig_(texture)_JE5_BE3.png", "Cow": "Cow_(texture).png",
    "Wither Skeleton": "Wither_Skeleton_(texture).png", "Enderman": "Enderman_(texture).png",
    "Spider": "Spider_(texture).png", "Chicken": "Chicken_(texture).png", "Iron Golem": "Iron_Golem_(texture).png",
    "Villager": "Villager_(texture).png", "Piglin": "Piglin_(texture).png",
    "Zombified Piglin": "Zombified_Piglin_(texture).png",
}


@dataclass
class Part:
    size: tuple[int, int, int]  # w, h, d in texels
    uv: tuple[int, int]  # where its skin starts
    origin: tuple[float, float, float]  # the box's low corner (x right, y up, z towards the mob's front)
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0)  # the joint it turns on
    mirror: bool = False  # the left limbs use the right one's skin, flipped
    turn: tuple[float, float, float] = (0.0, 0.0, 0.0)  # its own fixed turn, degrees (a pig's body lies down)
    name: str = ""
    tex: tuple[int, int, int] | None = None  # the skin's box when the drawn one is a little bigger (a robe)
    both: bool = False  # see-through with its pattern on the far side (a chicken's thin legs): draw every face


@dataclass
class Model:
    parts: list[Part]
    kind: str = "biped"  # how it walks: biped, quadruped, creeper
    arms_out: bool = False  # zombies hold their arms forward
    height: float = 32.0  # texels, for framing
    extra: dict = field(default_factory=dict)
    grow: float = 1.0  # a wither skeleton is a skeleton 1.2 times as big


def _biped(arm: int = 4, arms_out: bool = False) -> Model:
    """Zombie, husk, drowned (4 wide limbs) and skeleton, stray (2 wide): the player's own shape."""
    a, half = arm, arm / 2
    return Model([
        Part((8, 8, 8), (0, 0), (-4, 24, -4), (0, 24, 0), name="head"),
        Part((8, 12, 4), (16, 16), (-4, 12, -2), name="body"),
        Part((a, 12, a), (40, 16), (-4 - a, 12, -half), (-4 - half, 22, 0), name="right_arm"),
        Part((a, 12, a), (40, 16), (4, 12, -half), (4 + half, 22, 0), mirror=True, name="left_arm"),
        Part((a, 12, a), (0, 16), (-a if a == 4 else -3, 0, -half), (-2, 12, 0), name="right_leg"),
        Part((a, 12, a), (0, 16), (0 if a == 4 else 1, 0, -half), (2, 12, 0), mirror=True, name="left_leg"),
    ], "biped", arms_out, 32)


def _creeper() -> Model:
    return Model([
        Part((8, 8, 8), (0, 0), (-4, 18, -4), (0, 18, 0), name="head"),
        Part((8, 12, 4), (16, 16), (-4, 6, -2), name="body"),
        Part((4, 6, 4), (0, 16), (-4, 0, 2), (-2, 6, 4), name="front_right"),
        Part((4, 6, 4), (0, 16), (0, 0, 2), (2, 6, 4), mirror=True, name="front_left"),
        Part((4, 6, 4), (0, 16), (-4, 0, -6), (-2, 6, -4), name="back_right"),
        Part((4, 6, 4), (0, 16), (0, 0, -6), (2, 6, -4), mirror=True, name="back_left"),
    ], "creeper", height=26)


def _pig() -> Model:
    return Model([
        Part((8, 8, 8), (0, 0), (-4, 8, 6), (0, 12, 6), name="head"),
        Part((4, 3, 1), (16, 16), (-2, 9, 14), (0, 12, 6), name="snout"),
        Part((10, 16, 8), (28, 8), (-5, 2, -4), (0, 10, 0), turn=(90, 0, 0), name="body"),
        Part((4, 6, 4), (0, 16), (-5, 0, 3), (-3, 6, 5), name="front_right"),
        Part((4, 6, 4), (0, 16), (1, 0, 3), (3, 6, 5), mirror=True, name="front_left"),
        Part((4, 6, 4), (0, 16), (-5, 0, -9), (-3, 6, -7), name="back_right"),
        Part((4, 6, 4), (0, 16), (1, 0, -9), (3, 6, -7), mirror=True, name="back_left"),
    ], "quadruped", height=16)


def _cow() -> Model:
    return Model([
        Part((8, 8, 6), (0, 0), (-4, 16, 8), (0, 20, 8), name="head"),
        Part((1, 3, 1), (22, 0), (-5, 22, 10), (0, 20, 8), name="right_horn"),
        Part((1, 3, 1), (22, 0), (4, 22, 10), (0, 20, 8), mirror=True, name="left_horn"),
        Part((12, 18, 10), (18, 4), (-6, 8, -5), (0, 17, 0), turn=(90, 0, 0), name="body"),
        Part((4, 12, 4), (0, 16), (-6, 0, 4), (-4, 12, 6), name="front_right"),
        Part((4, 12, 4), (0, 16), (2, 0, 4), (4, 12, 6), mirror=True, name="front_left"),
        Part((4, 12, 4), (0, 16), (-6, 0, -9), (-4, 12, -7), name="back_right"),
        Part((4, 12, 4), (0, 16), (2, 0, -9), (4, 12, -7), mirror=True, name="back_left"),
    ], "quadruped", height=24)


def _enderman() -> Model:
    return Model([
        Part((8, 8, 8), (0, 0), (-4, 42, -4), (0, 42, 0), name="head"),
        Part((8, 12, 4), (32, 16), (-4, 30, -2), name="body"),
        Part((2, 30, 2), (56, 0), (-6, 10, -1), (-5, 40, 0), name="right_arm"),
        Part((2, 30, 2), (56, 0), (4, 10, -1), (5, 40, 0), mirror=True, name="left_arm"),
        Part((2, 30, 2), (56, 0), (-3, 0, -1), (-2, 30, 0), name="right_leg"),
        Part((2, 30, 2), (56, 0), (1, 0, -1), (2, 30, 0), mirror=True, name="left_leg"),
    ], "biped", height=50)


def _spider() -> Model:
    parts = [
        Part((8, 8, 8), (32, 4), (-4, 5, 3), (0, 9, 3), name="head"),
        Part((6, 6, 6), (0, 0), (-3, 6, -3), name="neck"),
        Part((10, 8, 12), (0, 12), (-5, 5, -15), name="body"),
    ]
    for i, (z, spread) in enumerate(((1, 45), (0, 22.5), (-1, -22.5), (-2, -45))):
        parts.append(Part((16, 2, 2), (18, 0), (-19, 8, z - 1), (-4, 9, z), turn=(0, spread, 40),
                          name=f"right_leg{i}"))
        parts.append(Part((16, 2, 2), (18, 0), (3, 8, z - 1), (4, 9, z), turn=(0, -spread, -40), mirror=True,
                          name=f"left_leg{i}"))
    return Model(parts, "spider", height=22)  # framed by its leg span, not its height


def _chicken() -> Model:
    return Model([
        Part((4, 6, 3), (0, 0), (-2, 9, 3), (0, 9, 4), name="head"),
        Part((4, 2, 2), (14, 0), (-2, 11, 6), (0, 9, 4), name="snout"),
        Part((2, 2, 2), (14, 4), (-1, 9, 5), (0, 9, 4), name="wattle"),
        Part((6, 8, 6), (0, 9), (-3, 4, -3), (0, 8, 0), turn=(90, 0, 0), name="body"),
        Part((1, 4, 6), (24, 13), (-4, 7, -3), (-4, 11, 0), name="right_wing"),
        Part((1, 4, 6), (24, 13), (3, 7, -3), (4, 11, 0), mirror=True, name="left_wing"),
        Part((3, 5, 3), (26, 0), (-3, 0, -1), (-2, 5, 0), both=True, name="right_leg"),
        Part((3, 5, 3), (26, 0), (0, 0, -1), (1, 5, 0), mirror=True, both=True, name="left_leg"),
    ], "biped", height=15)


def _golem() -> Model:
    return Model([
        Part((8, 10, 8), (0, 0), (-4, 33, -0.5), (0, 33, 2), name="head"),
        Part((2, 4, 2), (24, 0), (-1, 32, 7.5), (0, 33, 2), name="snout"),
        Part((18, 12, 11), (0, 40), (-9, 21, -5), name="body"),
        Part((9, 5, 6), (0, 70), (-4.5, 16, -3), name="waist"),
        Part((4, 30, 6), (60, 21), (-13, 3.5, -3), (-11, 31, 0), name="right_arm"),
        Part((4, 30, 6), (60, 58), (9, 3.5, -3), (11, 31, 0), name="left_arm"),
        Part((6, 16, 5), (37, 0), (-7.5, 0, -2), (-4, 13, 0), name="right_leg"),
        Part((6, 16, 5), (60, 0), (1.5, 0, -2), (5, 13, 0), name="left_leg"),
    ], "biped", height=43)


def _villager() -> Model:
    arms = (0, 21, 1)
    return Model([
        Part((8, 10, 8), (0, 0), (-4, 24, -4), (0, 24, 0), name="head"),
        Part((2, 4, 2), (24, 0), (-1, 23, 4), (0, 24, 0), name="snout"),
        Part((8, 12, 6), (16, 20), (-4, 12, -3), name="body"),
        Part((4, 8, 4), (44, 22), (-8, 15, -1), arms, turn=(-43, 0, 0), name="right_arm"),
        Part((4, 8, 4), (44, 22), (4, 15, -1), arms, turn=(-43, 0, 0), mirror=True, name="left_arm"),
        Part((8, 4, 4), (40, 38), (-4, 15, -1), arms, turn=(-43, 0, 0), name="hands"),
        Part((4, 12, 4), (0, 22), (-4, 0, -2), (-2, 12, 0), name="right_leg"),
        Part((4, 12, 4), (0, 22), (0, 0, -2), (2, 12, 0), mirror=True, name="left_leg"),
    ], "biped", height=34, extra={"robe": True})


def _piglin() -> Model:
    return Model([
        Part((10, 8, 8), (0, 0), (-5, 24, -4), (0, 24, 0), name="head"),
        Part((4, 4, 1), (31, 1), (-2, 24, 4), (0, 24, 0), name="snout"),
        Part((1, 2, 1), (2, 4), (3, 24, 4), (0, 24, 0), name="tusk"),
        Part((1, 2, 1), (2, 0), (-4, 24, 4), (0, 24, 0), name="tusk2"),
        Part((1, 5, 4), (51, 6), (4.5, 25, -2), (4.5, 30, 0), turn=(0, 0, 30), name="left_ear"),
        Part((1, 5, 4), (39, 6), (-5.5, 25, -2), (-4.5, 30, 0), turn=(0, 0, -30), name="right_ear"),
        Part((8, 12, 4), (16, 16), (-4, 12, -2), name="body"),
        Part((4, 12, 4), (40, 16), (-8, 12, -2), (-6, 22, 0), name="right_arm"),
        Part((4, 12, 4), (32, 48), (4, 12, -2), (6, 22, 0), name="left_arm"),
        Part((4, 12, 4), (0, 16), (-4, 0, -2), (-2, 12, 0), name="right_leg"),
        Part((4, 12, 4), (16, 48), (0, 0, -2), (2, 12, 0), name="left_leg"),
    ], "biped", height=32)


MODELS = {
    "Zombie": _biped(4, True), "Husk": _biped(4, True), "Drowned": _biped(4, True),
    "Skeleton": _biped(2), "Stray": _biped(2), "Creeper": _creeper(), "Pig": _pig(), "Cow": _cow(),
    "Wither Skeleton": Model(_biped(2).parts, "biped", height=32, grow=1.2), "Enderman": _enderman(),
    "Spider": _spider(), "Chicken": _chicken(), "Iron Golem": _golem(), "Villager": _villager(),
    "Piglin": _piglin(), "Zombified Piglin": Model(_piglin().parts, "biped", True, 32),
}


# --------------------------------------------------------------------------- skins


def skin_dir(settings: Settings) -> Path:
    return settings.assets_dir / "mobs"


def skin_path(settings: Settings, mob: str) -> Path:
    return skin_dir(settings) / (mob.lower().replace(" ", "-") + ".png")


def fetch_skins(settings: Settings, names: list[str] | None = None, session=None) -> list[str]:
    """Download each mob's skin from the wiki (kept once there); returns the ones now on disk."""
    import io

    import requests
    from PIL import Image

    from .reveal import USER_AGENT, WIKI_API

    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    skin_dir(settings).mkdir(parents=True, exist_ok=True)
    have = []
    for mob in names or list(SKINS):
        path = skin_path(settings, mob)
        if not path.is_file():
            r = session.get(WIKI_API, params={"action": "query", "titles": "File:" + SKINS[mob], "prop": "imageinfo",
                                              "iiprop": "url", "format": "json"}, timeout=30).json()
            info = list(r["query"]["pages"].values())[0].get("imageinfo")
            if not info:
                continue
            Image.open(io.BytesIO(session.get(info[0]["url"], timeout=30).content)).convert("RGBA").save(path)
        have.append(mob)
    return have


# --------------------------------------------------------------------------- the renderer


def _rot(p, angles, pivot):
    """Turn point p by (x, y, z) degrees about pivot: x first (a leg swinging), then y, then z."""
    x, y, z = p[0] - pivot[0], p[1] - pivot[1], p[2] - pivot[2]
    ax, ay, az = (math.radians(a) for a in angles)
    if ax:
        y, z = y * math.cos(ax) - z * math.sin(ax), y * math.sin(ax) + z * math.cos(ax)
    if ay:
        x, z = x * math.cos(ay) + z * math.sin(ay), -x * math.sin(ay) + z * math.cos(ay)
    if az:
        x, y = x * math.cos(az) - y * math.sin(az), x * math.sin(az) + y * math.cos(az)
    return x + pivot[0], y + pivot[1], z + pivot[2]


def _faces(part: Part):
    """(skin rect, top-left, top-right, bottom-left corner, outward normal) for the six faces, in the part's own
    space: the game's skin layout (right side, front, left side, back in one row under top and bottom)."""
    w, h, d = part.tex or part.size
    u, v = part.uv
    x0, y0, z0 = part.origin
    x1, y1, z1 = x0 + part.size[0], y0 + part.size[1], z0 + part.size[2]
    rects = {"top": (u + d, v, w, d), "bottom": (u + d + w, v, w, d), "right": (u, v + d, d, h),
             "front": (u + d, v + d, w, h), "left": (u + d + w, v + d, d, h), "back": (u + 2 * d + w, v + d, w, h)}
    if part.mirror:  # the left limb: its sides swap and every face is flipped
        rects["right"], rects["left"] = rects["left"], rects["right"]
    corners = {
        "front": ((x0, y1, z1), (x1, y1, z1), (x0, y0, z1), (0, 0, 1)),
        "back": ((x1, y1, z0), (x0, y1, z0), (x1, y0, z0), (0, 0, -1)),
        "right": ((x0, y1, z0), (x0, y1, z1), (x0, y0, z0), (-1, 0, 0)),
        "left": ((x1, y1, z1), (x1, y1, z0), (x1, y0, z1), (1, 0, 0)),
        "top": ((x0, y1, z0), (x1, y1, z0), (x0, y1, z1), (0, 1, 0)),
        "bottom": ((x0, y0, z1), (x1, y0, z1), (x0, y0, z0), (0, -1, 0)),
    }
    for name, (tl, tr, bl, normal) in corners.items():
        yield name, rects[name], tl, tr, bl, normal


LIGHT = (-0.35, 0.85, 0.4)


def render(settings: Settings, mob: str, pose: dict | None = None, *, scale: float = 10.0, yaw: float = -32.0,
           pitch: float = 18.0, body: tuple = (0.0, 0.0, 0.0), red: float = 0.0, size: tuple[int, int] = (420, 420),
           feet: tuple[int, int] | None = None, cache: dict | None = None):
    """The mob at `scale` screen pixels per texel, turned by `pose` ({part: (x, y, z) degrees}) and as a whole by
    `body` (about the ground under it), seen from `yaw`/`pitch`; `red` (0-1) is the hurt flash. Its feet stand at
    `feet` on a `size` picture (default: the bottom middle)."""
    from PIL import Image, ImageEnhance

    cache = cache if cache is not None else {}
    model = MODELS[mob]
    if ("skin", mob) not in cache:
        cache[("skin", mob)] = Image.open(skin_path(settings, mob)).convert("RGBA")
    skin = cache[("skin", mob)]
    pose = pose or {}
    scale *= model.grow
    feet = feet or (size[0] // 2, size[1] - 20)
    lx, ly, lz = LIGHT
    ln = math.sqrt(lx * lx + ly * ly + lz * lz)
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))

    def place(p):
        x, y, z = _rot(p, body, (0, 0, 0))
        x, z = x * cy + z * sy, -x * sy + z * cy  # the camera turns round the mob
        y, z = y * cp - z * sp, y * sp + z * cp  # and looks down a little
        return feet[0] + x * scale, feet[1] - y * scale, z

    faces = []
    for part in model.parts:
        turn = pose.get(part.name, (0.0, 0.0, 0.0))
        for name, rect, tl, tr, bl, normal in _faces(part):
            pts = []
            for p in (tl, tr, bl):
                if part.turn != (0, 0, 0):  # a part that lies down (a pig's body) turns about its own middle
                    p = _rot(p, part.turn, part.pivot)
                if turn != (0.0, 0.0, 0.0):
                    p = _rot(p, turn, part.pivot)
                pts.append(place(p))
            n = normal
            if part.turn != (0, 0, 0):
                n = _rot(n, part.turn, (0, 0, 0))
            if turn != (0.0, 0.0, 0.0):
                n = _rot(n, turn, (0, 0, 0))
            n_world = _rot(n, body, (0, 0, 0))
            (ax, ay, az), (bx, by, bz), (cx, cy2, cz) = pts
            if (bx - ax) * (cy2 - ay) - (by - ay) * (cx - ax) <= 0 and not part.both:  # facing away
                continue
            light = 0.55 + 0.45 * max(0.0, (n_world[0] * lx + n_world[1] * ly + n_world[2] * lz) / ln)
            depth = (bz + cz) / 2  # the face's middle
            faces.append((depth, rect, pts, light, part.mirror))
    faces.sort(key=lambda f: f[0])
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    for _, (u, v, w, h), pts, light, mirror in faces:
        if w <= 0 or h <= 0:
            continue
        key = ("tile", mob, u, v, w, h, mirror, round(light, 2), round(red, 1))
        if key not in cache:
            tile = skin.crop((u, v, u + w, v + h))
            if mirror:
                tile = tile.transpose(Image.FLIP_LEFT_RIGHT)
            alpha = tile.getchannel("A")
            tile = ImageEnhance.Brightness(tile.convert("RGB")).enhance(light).convert("RGBA")
            if red:
                tint = Image.new("RGBA", tile.size, (255, 40, 40, 255))
                tile = Image.blend(tile, tint, 0.45 * red)
            tile.putalpha(alpha)
            cache[key] = tile
        tile = cache[key]
        (ax, ay, _), (bx, by, _), (cx, cy2, _) = pts
        xs = [ax, bx, cx, bx + cx - ax]
        ys = [ay, by, cy2, by + cy2 - ay]
        x_lo, x_hi = math.floor(min(xs)) - 1, math.ceil(max(xs)) + 1
        y_lo, y_hi = math.floor(min(ys)) - 1, math.ceil(max(ys)) + 1
        m00, m01 = (bx - ax) / w, (cx - ax) / h
        m10, m11 = (by - ay) / w, (cy2 - ay) / h
        det = m00 * m11 - m01 * m10
        if abs(det) < 1e-6:
            continue
        i00, i01, i10, i11 = m11 / det, -m01 / det, -m10 / det, m00 / det
        ox, oy = x_lo - ax, y_lo - ay
        coeffs = (i00, i01, i00 * ox + i01 * oy, i10, i11, i10 * ox + i11 * oy)
        piece = tile.transform((x_hi - x_lo, y_hi - y_lo), Image.AFFINE, coeffs, resample=Image.NEAREST)
        cut_x, cut_y = max(0, -x_lo), max(0, -y_lo)  # a corner off the picture
        if cut_x >= piece.width or cut_y >= piece.height:
            continue
        if cut_x or cut_y:
            piece = piece.crop((cut_x, cut_y, piece.width, piece.height))
        if piece.width > 0 and piece.height > 0 and x_lo + cut_x < size[0] and y_lo + cut_y < size[1]:
            out.alpha_composite(piece, (x_lo + cut_x, y_lo + cut_y))
    return out


# --------------------------------------------------------------------------- animation

STEP = 0.8  # seconds for one full stride (left and right)
DEATH = 1.0  # the game tips a dying mob over in about a second, then it puffs into smoke
SMOKE = 0.45


def pose(mob: str, action: str = "walk", t: float = 0.0) -> dict:
    """{"parts": {part: (x, y, z) degrees}, "body": (x, y, z), "lift": texels up, "red": 0-1, "smoke": 0-1 or
    None} for `action` at `t` seconds: idle, walk, hurt (a hit at t = 0), death (from t = 0)."""
    model = MODELS[mob]
    walking = action in ("walk", "hurt")
    swing = 38 * math.sin(2 * math.pi * t / STEP) if walking else 4 * math.sin(2 * math.pi * t / 2.6)
    parts: dict = {}
    if model.kind == "biped":
        parts["right_leg"], parts["left_leg"] = (swing, 0, 0), (-swing, 0, 0)
        if model.arms_out:
            sway = 4 * math.sin(2 * math.pi * t / STEP)
            parts["right_arm"], parts["left_arm"] = (-90 + sway, 0, 0), (-90 - sway, 0, 0)
        else:
            parts["right_arm"], parts["left_arm"] = (-swing * 0.8, 0, 3), (swing * 0.8, 0, -3)
    elif model.kind == "spider":  # eight legs: each side's legs fan back and forth, alternating
        for i in range(4):
            wave = 22 * math.sin(2 * math.pi * t / (STEP * 0.6) + i * math.pi / 2) if walking else 0
            parts[f"right_leg{i}"], parts[f"left_leg{i}"] = (0, wave, 0), (0, -wave, 0)
    else:  # four legs: diagonal pairs together
        for name in ("front_right", "back_left"):
            parts[name] = (swing, 0, 0)
        for name in ("front_left", "back_right"):
            parts[name] = (-swing, 0, 0)
    head = 10 * math.sin(2 * math.pi * t / 3.1) if action == "idle" else 0
    for name in ("head", "snout", "right_horn", "left_horn"):
        parts[name] = (0, head, 0)
    lift = 0.6 * abs(math.sin(2 * math.pi * t / STEP)) if walking else 0
    out = {"parts": parts, "body": (0, 0, 0), "lift": lift, "red": 0.0, "smoke": None}
    if action == "hurt":
        out["red"] = max(0.0, 1 - t / 0.35)
        out["body"] = (-8 * out["red"], 0, 0)  # knocked back a little
    if action == "death":
        k = min(1.0, t / DEATH)
        out["parts"] = {n: v for n, v in parts.items() if "arm" in n} if model.arms_out else {}
        out["body"] = (0, 0, 90 * math.sqrt(k))  # the game's own fall: fast, then settling on its side
        out["red"] = 1.0
        out["lift"] = 0
        out["smoke"] = (t - DEATH) / SMOKE if t > DEATH else None
    return out


def frame(settings: Settings, mob: str, action: str = "walk", t: float = 0.0, *, scale: float | None = None,
          size: tuple[int, int] = (420, 420), yaw: float = -32.0, cache: dict | None = None):
    """One picture of the mob doing `action` at `t`, standing on a soft shadow; after a death, the smoke."""
    from PIL import Image, ImageDraw

    model = MODELS[mob]
    scale = scale or (size[1] - 80) / max(model.height * model.grow, 20)
    p = pose(mob, action, t)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    feet = (size[0] // 2, size[1] - 30)
    d = ImageDraw.Draw(img, "RGBA")
    if p["smoke"] is None:
        d.ellipse([feet[0] - 9 * scale, feet[1] - 2 * scale, feet[0] + 9 * scale, feet[1] + 2 * scale],
                  fill=(0, 0, 0, 70))
        mob_img = render(settings, mob, p["parts"], scale=scale, yaw=yaw, body=p["body"], red=p["red"], size=size,
                         feet=(feet[0], int(feet[1] - p["lift"] * scale)), cache=cache)
        img.alpha_composite(mob_img)
    elif p["smoke"] < 1:  # white and grey puffs rising from where it fell
        k = p["smoke"]
        for j in range(12):
            a = j * 2.4
            r = (0.3 + 0.7 * k) * 9 * scale * (0.5 + (j % 3) / 3)
            px = feet[0] + r * math.cos(a)
            py = feet[1] - 4 * scale - 6 * scale * k - r * 0.4 * abs(math.sin(a))
            c = 235 if j % 2 else 190
            half = scale * (2.2 - 1.2 * k)
            d.rectangle([px - half, py - half, px + half, py + half], fill=(c, c, c, int(255 * (1 - k))))
    return img


def save_gif(settings: Settings, mob: str, path: Path, action: str = "walk", seconds: float | None = None,
             fps: int = 20, size: tuple[int, int] = (420, 420)) -> Path:
    """The animation as a looping GIF (a walk loops on its stride; a death plays once with its smoke)."""
    cache: dict = {}
    seconds = seconds or (2 * STEP if action != "death" else DEATH + SMOKE + 0.2)
    frames = [frame(settings, mob, action, i / fps, size=size, cache=cache) for i in range(int(seconds * fps))]
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=round(1000 / fps), loop=0,
                   disposal=2, optimize=False)
    return path
