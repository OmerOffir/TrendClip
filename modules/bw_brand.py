"""Borderline Why's signature look, drawn on top of modules.bw_fx when a script asks for it (brand: true):

- the mascot: the owner's red-panda explorer (stickers/borderlinewhy/explorer_*.gif) stands in a corner of every
  frame, bottom left (it looks to the right, into the picture; on a Short YouTube's buttons cover the right too); it searches the
  map at the start, thinks while the story asks, lights up with its pen when the answer comes and cheers the answer to
  its own question (scene option mascot: searching | thinking | discovery | celebration);
- the mascot talks: a scene's "ask" is a short line it says in its own voice before the scene (the narrator's
  narration then answers it) and "react" one it says after; its words fill a speech bubble instead of the captions;
- the marker hand: the owner's hand GIF draws each map scene's main line (a highlighted border or river, an arrow, a
  treaty line, a sketch, or the traced place) in marker, its pen on the line's tip, then leaves;
- the outro: an "end" scene added after the story (add_outro): the mascot asks the viewer to subscribe in its own
  voice while the hand clicks a Subscribe button on an end card (held longer on a long video, for YouTube's end screen);
- the whiteboard: a scene of kind "board" where the hand writes a title and a few lines and sketches places' real
  outlines, circling the last one (scene option board: {write: [...], draw: [...], circle: true, underline: true}).

All drawn by code over the owner's own GIFs; nothing is AI-generated.
"""

from __future__ import annotations

import bisect
import math
import re
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from modules import bw_fx, bw_geo, bw_render
from modules.bw_render import Element, Scene, curve, ease

MASCOT = {"thinking": "explorer_thinking_smooth", "discovery": "explorer_discovery", "searching": "explorer_searching",
          "celebration": "explorer_celebration", "speaking": "explorer_speaking"}
PANDA = "panda"  # bw_render.Word.who for the mascot's words
PANDA_GAP = 0.3  # seconds of silence on each side of a line the mascot says
HAND = "cartoon_hand_gentle_sway"
RED = (206, 32, 40)  # the signature marker colour: everything the hand draws on a map
INK = (30, 32, 40)
BLUE = (28, 84, 196)
RIVER = (60, 160, 240)
BOARD = (247, 247, 243)
PEN_KINDS = ("highlight", "arrow", "line", "sketch")  # what the hand can draw on a map
THINK_WORDS = re.compile(r"\b(but|why|how|odd|strange|mystery|weird|however|yet|bizarre|puzzle)\b", re.I)
SIZES = {"portrait": dict(mascot=270, hand=330, board_title=124, board_line=104),
         "landscape": dict(mascot=230, hand=280, board_title=110, board_line=84)}
SIZES["portrait"].update(bubble=66, bubble_w=760)
SIZES["landscape"].update(bubble=54, bubble_w=720)

_frames: dict[tuple[str, int], tuple[list[Image.Image], list[float], list[tuple[float, float]]]] = {}
_board: dict[tuple[int, int], Image.Image] = {}


def size(key: str) -> int:
    return SIZES[bw_render.LAYOUT][key]


def frames(name: str, height: int) -> tuple[list[Image.Image], list[float], list[tuple[float, float]]]:
    """A brand GIF's frames cropped to what is drawn and scaled to `height`, the times each frame ends, and each frame's
    pen tip (the darkest drawn pixel nearest the top left; only the hand needs it)."""
    key = (name, height)
    if key not in _frames:
        im = Image.open(bw_fx.CUSTOM_DIR / f"{name}.gif")
        raw, ends, end = [], [], 0.0
        for n in range(getattr(im, "n_frames", 1)):
            im.seek(n)
            f = im.convert("RGBA")
            f.putalpha(f.getchannel("A").point(lambda v: 0 if v < 48 else v))  # the GIFs' faint haze shows as a box
            raw.append(f)
            end += max(0.02, (im.info.get("duration") or 40) / 1000)
            ends.append(end)
        boxes = [b for b in (f.getchannel("A").getbbox() for f in raw) if b] or [(0, 0, im.width, im.height)]
        box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        k = height / (box[3] - box[1])
        wh = (max(1, round((box[2] - box[0]) * k)), height)
        out, tips = [], []
        for f in raw:
            f = f.crop(box)
            a = np.asarray(f)
            dark = (a[..., 3] > 160) & (a[..., :3].astype(int).sum(axis=2) < 150)
            ys, xs = np.nonzero(dark)
            j = int(np.argmin(xs + ys)) if len(xs) else 0
            tips.append(((xs[j] if len(xs) else 0) * k, (ys[j] if len(ys) else 0) * k))
            out.append(f.resize(wh, Image.LANCZOS))
        _frames[key] = (out, ends, tips)
    return _frames[key]


def frame_at(name: str, height: int, t: float) -> tuple[Image.Image, tuple[float, float]]:
    out, ends, tips = frames(name, height)
    k = min(len(out) - 1, bisect.bisect_right(ends, t % ends[-1]))
    return out[k], tips[k]


def shadow(im: Image.Image, blur: int = 8, alpha: float = 0.35) -> Image.Image:
    """A soft shadow of a picture, padded by 2 x blur on every side so the blur never ends in a straight edge."""
    pad = 2 * blur
    sh = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
    sh.paste(Image.new("RGBA", im.size, (0, 0, 0, 255)), (pad, pad), im.getchannel("A").point(lambda v: int(v * alpha)))
    return sh.filter(ImageFilter.GaussianBlur(blur))


# --------------------------------------------------------------------------- lines


def prefix(pts: np.ndarray, u: float) -> tuple[np.ndarray, tuple[float, float]]:
    """The first `u` of a polyline by length, and the point it reaches."""
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 2:
        return pts, (tuple(pts[0]) if len(pts) else (0.0, 0.0))
    cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    goal = cum[-1] * min(1.0, max(0.0, u))
    k = int(np.searchsorted(cum, goal))
    if k <= 0:
        return pts[:1], tuple(pts[0])
    if k >= len(pts):
        return pts, tuple(pts[-1])
    f = (goal - cum[k - 1]) / max(1e-9, cum[k] - cum[k - 1])
    head = pts[k - 1] + (pts[k] - pts[k - 1]) * f
    return np.vstack([pts[:k], head]), (float(head[0]), float(head[1]))


def length(pts) -> float:
    pts = np.asarray(pts, dtype=float)
    return float(np.hypot(*np.diff(pts, axis=0).T).sum()) if len(pts) > 1 else 0.0


def marker(ov: Image.Image, pts, rgb, width: int = 14, a: float = 1.0, shade: bool = True) -> None:
    """A marker stroke: on a map a soft shadow, the ink and a lighter streak along it; on the board just the ink."""
    pts = [(float(x), float(y)) for x, y in pts]
    if len(pts) < 2 or a <= 0:
        return
    d = ImageDraw.Draw(ov)
    al = int(255 * a)
    if shade:
        d.line([(x + 3, y + 4) for x, y in pts], fill=(0, 0, 0, al // 3), width=width + 4, joint="curve")
    d.line(pts, fill=rgb + (al,), width=width, joint="curve")
    if shade:
        light = tuple(min(255, c + 70) for c in rgb)
        d.line([(x - 1, y - 1) for x, y in pts], fill=light + (al // 3,), width=max(2, width // 4), joint="curve")
    r = width / 2 - 0.5
    for x, y in (pts[0], pts[-1]):
        d.ellipse([x - r, y - r, x + r, y + r], fill=rgb + (al,))


def chevron(ov: Image.Image, pts, rgb, width: int = 14, a: float = 1.0) -> None:
    """An arrow head drawn as two marker strokes at the end of a line."""
    (xa, ya), (xb, yb) = pts[max(0, len(pts) - 4)], pts[-1]
    ang = math.atan2(yb - ya, xb - xa)
    for turn in (2.5, -2.5):
        marker(ov, [(xb + 46 * math.cos(ang + turn), yb + 46 * math.sin(ang + turn)), (xb, yb)], rgb, width, a)


# --------------------------------------------------------------------------- the hand


def draw_hand(ov: Image.Image, tip: tuple[float, float], t: float) -> None:
    """The marker hand with its pen tip on `tip`."""
    im, (tx, ty) = frame_at(HAND, size("hand"), t)
    x, y = int(tip[0] - tx), int(tip[1] - ty)
    paste(ov, shadow(im, 10, 0.3), x + 16 - 20, y + 22 - 20)
    paste(ov, im, x, y)


def paste(ov: Image.Image, im: Image.Image, x: int, y: int) -> None:
    """alpha_composite that crops what falls off the frame (it refuses a negative position)."""
    l, t = max(0, -x), max(0, -y)
    if l >= im.width or t >= im.height or x >= ov.width or y >= ov.height:
        return
    ov.alpha_composite(im.crop((l, t, im.width, im.height)) if l or t else im, (x + l, y + t))


def hand_at(t: float, at: float, done: float, head: tuple[float, float], start: tuple[float, float], enter: float) -> tuple[float, float] | None:
    """Where the pen is: sliding in from the bottom right to the line's start, on the line's head while it draws,
    then away again."""
    away = (bw_render.W * 0.92, bw_render.H + 60)
    if t < at:
        u = (t - (at - enter)) / max(0.05, enter)
        if u <= 0:
            return None
        u = ease(u)
        return away[0] + (start[0] - away[0]) * u, away[1] + (start[1] - away[1]) * u
    if t <= done:
        return head
    u = (t - done) / 0.45
    if u >= 1:
        return None
    u = ease(u)
    return head[0] + (away[0] - head[0]) * u, head[1] + (away[1] - head[1]) * u


# --------------------------------------------------------------------------- the mascot


def moods(scenes: list[Scene]) -> list[str]:
    """Each scene's mascot: searching the map at the opening; thinking on questions and twists; cheering the scene
    that answers its last question; lit up with its pen otherwise."""
    out = []
    last_ask = max((i for i, s in enumerate(scenes) if s.opts.get("ask")), default=-1)
    for i, s in enumerate(scenes):
        mood = s.opts.get("mascot")
        if mood not in MASCOT or mood == "speaking":
            if s.kind == "end":
                mood = "celebration"
            elif i == 0:
                mood = "searching"
            elif i == last_ask:  # the answer to its last question: the payoff
                mood = "celebration"
            elif "?" in s.narration or THINK_WORDS.search(s.narration):
                mood = "thinking"
            else:
                mood = "discovery"
        out.append(mood)
    return out


OUTROS = (  # the mascot's last line, picked by the script's seed
    "Subscribe, and I'll find you another weird border!",
    "Hit subscribe for more borders that make no sense!",
    "Subscribe! There are way more weird borders out there.",
    "Subscribe, and let's find the next strange border together!",
)
END_HOLD = {"short": 1.2, "long": 9.0}  # seconds the end card stays after the line (a long video's end screen needs 5-20 s)


def add_outro(script: dict, long: bool = False) -> dict:
    """The script with its outro scene (once; only with the brand on)."""
    scenes = script.get("scenes") or []
    if not script.get("brand") or any(sc.get("kind") == "end" for sc in scenes):
        return script
    line = OUTROS[int(script.get("seed") or 0) % len(OUTROS)]
    end = {"kind": "end", "narration": "", "react": line, "hold": END_HOLD["long" if long else "short"]}
    return {**script, "scenes": [*scenes, end]}


def dialogue(script: dict) -> list[tuple[str, str]]:
    """What is said, in order, as (who, text): the narrator's narration ("") and the mascot's lines (PANDA) where a
    scene asks or reacts. One narrator part when the mascot has nothing to say."""
    scenes = script.get("scenes") or []
    if not script.get("brand") or not any(str(s.get(k) or "").strip() for s in scenes for k in ("ask", "react")):
        return [("", script["narration"])]
    parts: list[tuple[str, str]] = []

    def say(who: str, text: str) -> None:
        text = " ".join(str(text or "").split())
        if not text:
            return
        if parts and parts[-1][0] == who == "":
            parts[-1] = ("", parts[-1][1] + " " + text)
        else:
            parts.append((who, text))

    for s in scenes:
        say(PANDA, s.get("ask"))
        say("", s.get("narration"))
        say(PANDA, s.get("react"))
    return parts


def mascot_mood(spec: bw_render.Spec, mood_of: list[str], i: int, t: float) -> tuple[str, float]:
    """The pose at `t` and when it began: talking while it says a line, else its scene's mood."""
    line = spec.line_at(t, before=0.15, after=0.1)
    if line:
        return "speaking", line[0].start - 0.15
    ends = [ln[-1].end + 0.1 for ln in spec.lines if ln[-1].end + 0.1 <= t]
    start = max([spec.scenes[i].start, *ends])
    return mood_of[i], start


def mascot_spot() -> tuple[float, float]:
    """Where the mascot stands: its centre x and its feet."""
    portrait = bw_render.LAYOUT == "portrait"
    return (165 if portrait else 175), bw_render.CAPTION_Y - (70 if portrait else 62)


def wrap(words: list[str], f, width: float) -> list[list[int]]:
    """Word indexes per row, each row at most `width` wide."""
    rows: list[list[int]] = [[]]
    for k, w in enumerate(words):
        test = " ".join(words[j] for j in rows[-1] + [k])
        if rows[-1] and f.getlength(test) > width:
            rows.append([])
        rows[-1].append(k)
    return rows


def draw_bubble(img: Image.Image, spec: bw_render.Spec, t: float) -> None:
    """The mascot's line in a speech bubble over its head: it pops up as the mascot starts talking, each word darkens
    as it is said (the word being said in red), and it fades a moment after the line ends."""
    line = spec.line_at(t)
    if not line:
        return
    start, end = line[0].start - 0.05, line[-1].end
    pop = ease(min(1.0, (t - start) / 0.22))
    fade = 1.0 - max(0.0, min(1.0, (t - end - 0.1) / 0.25))
    if pop <= 0 or fade <= 0:
        return
    px = size("bubble")
    f = bw_fx.hand_font(px)
    words = [w.text for w in line]
    pad_x, pad_y, lead = 44, 30, int(px * 1.12)
    rows = wrap(words, f, size("bubble_w") - 2 * pad_x)
    bw = int(max(f.getlength(" ".join(words[j] for j in r)) for r in rows) + 2 * pad_x)
    bh = int(len(rows) * lead + 2 * pad_y)
    tail = 54
    cx, feet = mascot_spot()
    head = feet - size("mascot")
    portrait = bw_render.LAYOUT == "portrait"
    shape = Image.new("L", (bw + 24 + (0 if portrait else tail), bh + 24 + (tail if portrait else 0)), 0)
    ds = ImageDraw.Draw(shape)
    ds.rounded_rectangle([12, 12, 12 + bw, 12 + bh], radius=42, fill=255)
    if portrait:  # above the mascot, the tail pointing down at its head
        tip = (12 + 120 - 26, 12 + bh + tail)
        ds.polygon([(12 + 90, 12 + bh - 4), (12 + 150, 12 + bh - 4), tip], fill=255)
        at = (cx - 70, head - 6)
    else:  # beside it, low in the caption band (the captions are off while it talks), so a board's writing stays clear
        mid = 12 + bh / 2  # the box sits right of the tail, which points left at the mascot
        ds.rectangle([0, 0, shape.width, shape.height], fill=0)
        ds.rounded_rectangle([12 + tail, 12, 12 + tail + bw, 12 + bh], radius=42, fill=255)
        tip = (12, mid + 14)
        ds.polygon([(12 + tail + 4, mid - 28), (12 + tail + 4, mid + 28), tip], fill=255)
        at = (cx + 92, head + 95)
    layer = Image.new("RGBA", shape.size, (20, 22, 30, 0))
    layer.putalpha(shape.filter(ImageFilter.MaxFilter(11)))  # the ink outline
    layer.paste(Image.new("RGBA", shape.size, (255, 255, 255, 255)), (0, 0), shape)
    d = ImageDraw.Draw(layer)
    for r, row in enumerate(rows):
        x = 12 + pad_x + (0 if portrait else tail)
        y = 12 + pad_y + r * lead + lead // 2
        for j in row:
            w = line[j]
            said = t >= w.start
            now = w.start <= t < w.end + 0.05
            fill = RED if now else INK if said else (170, 172, 180)
            d.text((x, y), w.text, font=f, fill=fill + (255,), anchor="lm")
            x += f.getlength(w.text + " ")
    if portrait:  # keep the bubble on screen
        at = (max(at[0], 40 + tip[0]), at[1])
    k = 0.55 + 0.45 * pop + 0.06 * math.sin(math.pi * min(1.0, (t - start) / 0.22))
    if abs(k - 1) > 0.01:
        layer = layer.resize((max(1, int(layer.width * k)), max(1, int(layer.height * k))), Image.BILINEAR)
    x, y = int(at[0] - tip[0] * k), int(at[1] - tip[1] * k)  # it grows from the tail's tip
    if fade < 1:
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * fade)))
    paste(img, shadow(layer, 10, 0.35), x - 20 + 6, y - 20 + 10)
    paste(img, layer, x, y)


def draw_mascot(img: Image.Image, spec: bw_render.Spec, mood_of: list[str], scene_at, t: float) -> None:
    """The mascot in its corner; it hops when its pose changes. `scene_at`: the renderer's scene index at a time."""
    mood, since = mascot_mood(spec, mood_of, scene_at(t), t)
    before = mascot_mood(spec, mood_of, scene_at(max(0.0, since - 0.05)), since - 0.05)[0] if since > 0 else mood
    h = size("mascot")
    hop = 0.0
    if before != mood and t - since < 0.4:
        hop = math.sin(math.pi * (t - since) / 0.4)
    im, _ = frame_at(MASCOT[mood], h, t)
    if hop:
        im = im.resize((im.width, max(1, int(im.height * (1 - 0.08 * hop)))), Image.BILINEAR)
    cx, feet = mascot_spot()
    x = int(cx - im.width / 2)
    y = int(feet - im.height - 46 * hop + 3 * math.sin(2.1 * t))
    sh = Image.new("RGBA", (int(im.width * 0.8) + 40, 74), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([20, 20, sh.width - 21, 53], fill=(0, 0, 0, int(120 * (1 - 0.4 * hop))))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(7)), (int(cx - sh.width / 2), int(feet - 38)))
    img.alpha_composite(im, (x, y))


# --------------------------------------------------------------------------- the end card


class EndCard:
    """The outro: the last picture blurred and dimmed, the channel's name, and a Subscribe button the marker hand
    clicks (it turns into Subscribed with a ringing bell). A long video keeps the right half free for YouTube's end
    screen."""

    def __init__(self, scene: Scene, background: Image.Image, name: str = "BORDERLINE WHY", handle: str = "@BorderlineWhy"):
        self.scene, self.name, self.handle = scene, name, handle
        bg = background.convert("RGB").filter(ImageFilter.GaussianBlur(18))
        self.bg = Image.blend(bg, Image.new("RGB", bg.size, (8, 10, 16)), 0.55).convert("RGBA")
        portrait = bw_render.LAYOUT == "portrait"
        W, H = bw_render.W, bw_render.H
        self.cx = W / 2 if portrait else W * 0.3
        self.logo_y = H * 0.16 if portrait else H * 0.2  # high: the mascot's bubble rises from the bottom left
        self.button = (self.cx, H * 0.31 if portrait else H * 0.42)

    def frame(self, t: float) -> Image.Image:
        img = self.bg.copy()
        age = t - self.scene.start
        portrait = bw_render.LAYOUT == "portrait"
        d = ImageDraw.Draw(img)
        u = bw_fx.backout(age / 0.5)
        if u > 0:
            px = 150 if portrait else 128
            while bw_render.font(px).getlength(self.name) > (bw_render.W - 100 if portrait else bw_render.W * 0.55) and px > 60:
                px -= 6
            f = bw_render.font(px)
            y = self.logo_y - 60 * (1 - u)
            d.text((self.cx, y), self.name, font=f, fill=(255, 255, 255, int(255 * min(1, u))), stroke_width=10,
                   stroke_fill=(0, 0, 0), anchor="mm")
            h = bw_fx.hand_font(70 if portrait else 60)
            d.text((self.cx, y + (115 if portrait else 100)), self.handle, font=h, fill=(255, 214, 80, int(255 * min(1, u))), anchor="mm")
        press, done = 1.75, 1.85
        pop = bw_fx.backout((age - 0.5) / 0.45)
        if pop > 0:
            subscribed = age >= done
            squash = 0.93 if press <= age < done + 0.08 else 1.0
            self.draw_button(img, pop * squash, subscribed, age - done)
        bx, by = self.button
        tip_at = (bx - 40, by + 18)
        enter, leave = 1.05, 2.35
        start = (bw_render.W * 0.92, bw_render.H + 60)
        if enter <= age < leave + 0.5:
            if age < press:
                v = ease((age - enter) / (press - enter))
                tip = (start[0] + (tip_at[0] - start[0]) * v, start[1] + (tip_at[1] - start[1]) * v)
            elif age < leave:
                tip = (tip_at[0], tip_at[1] + (10 if age < done + 0.1 else 0))
            else:
                v = ease((age - leave) / 0.5)
                tip = (tip_at[0] + (start[0] - tip_at[0]) * v, tip_at[1] + (start[1] - tip_at[1]) * v)
            draw_hand(img, tip, t)
        return img

    def draw_button(self, img: Image.Image, k: float, subscribed: bool, since: float) -> None:
        portrait = bw_render.LAYOUT == "portrait"
        f = bw_render.font(76 if portrait else 64)
        text = "SUBSCRIBED" if subscribed else "SUBSCRIBE"
        bell = 90 if subscribed else 0
        w, h = int(f.getlength(text) + 110 + bell), 150 if portrait else 128
        layer = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        fill = (62, 62, 66, 255) if subscribed else (214, 22, 30, 255)
        d.rounded_rectangle([20, 20, 20 + w, 20 + h], radius=h // 2, fill=fill)
        d.text((20 + 55 + (w - 110 - bell) / 2, 20 + h / 2), text, font=f, fill=(255, 255, 255, 255), anchor="mm")
        if subscribed:  # a bell that rings for a moment
            bx, by, r = 20 + w - 80, 20 + h / 2, h * 0.24
            swing = 18 * math.sin(since * 22) * max(0.0, 1 - since / 0.9)
            bl = Image.new("RGBA", (int(r * 3), int(r * 3)), (0, 0, 0, 0))
            bd = ImageDraw.Draw(bl)
            c = r * 1.5
            bd.pieslice([c - r, c - r * 1.15, c + r, c + r * 0.85], 180, 360, fill=(255, 255, 255, 255))
            bd.rectangle([c - r, c - r * 0.15, c + r, c + r * 0.55], fill=(255, 255, 255, 255))
            bd.rectangle([c - r * 1.25, c + r * 0.5, c + r * 1.25, c + r * 0.7], fill=(255, 255, 255, 255))
            bd.ellipse([c - r * 0.25, c + r * 0.7, c + r * 0.25, c + r * 1.1], fill=(255, 255, 255, 255))
            bl = bl.rotate(swing, resample=Image.BICUBIC, center=(c, c - r))
            layer.alpha_composite(bl, (int(bx - c), int(by - c)))
        if abs(k - 1) > 0.01:
            layer = layer.resize((max(1, int(layer.width * k)), max(1, int(layer.height * k))), Image.BILINEAR)
        x, y = int(self.button[0] - layer.width / 2), int(self.button[1] - layer.height / 2)
        paste(img, shadow(layer, 12, 0.5), x - 24 + 8, y - 24 + 12)
        paste(img, layer, x, y)


# --------------------------------------------------------------------------- the whiteboard


def board_image() -> Image.Image:
    """A clean whiteboard: faint ghosts of old marker, a light from the top, an aluminium frame."""
    W, H = bw_render.W, bw_render.H
    if (W, H) not in _board:
        rng = np.random.default_rng(11)
        yy = np.linspace(0, 1, H)[:, None]
        base = np.array(BOARD, dtype=np.float32)[None, None, :] * (1.0 - 0.05 * yy[..., None]) * np.ones((1, W, 1))
        img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).convert("RGBA")
        ghosts = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        g = ImageDraw.Draw(ghosts)
        for _ in range(9):
            x, y = rng.uniform(0, W), rng.uniform(0, H)
            w, h = rng.uniform(120, 420), rng.uniform(30, 110)
            g.ellipse([x - w, y - h, x + w, y + h], fill=(150, 160, 175, int(rng.uniform(10, 22))))
        img.alpha_composite(ghosts.filter(ImageFilter.GaussianBlur(40)))
        d = ImageDraw.Draw(img)
        m = 18
        d.rounded_rectangle([0, 0, W - 1, H - 1], radius=0, outline=(168, 172, 178, 255), width=m)
        d.rounded_rectangle([m, m, W - 1 - m, H - 1 - m], radius=10, outline=(120, 124, 132, 255), width=3)
        d.line([m + 4, m + 4, W - m - 4, m + 4], fill=(255, 255, 255, 200), width=2)
        _board[(W, H)] = img
    return _board[(W, H)].copy()


def outline_points(feat: bw_geo.Feature) -> np.ndarray:
    """A place's biggest outline (or its longest line) in lon/lat, closed."""
    part = max(feat.parts, key=lambda p: len(p.rings[0]))
    pts = part.rings[0]
    if len(pts) > 1200:
        pts = pts[:: len(pts) // 1200 + 1]
    return pts if feat.is_line else np.vstack([pts, pts[:1]])


class Board:
    """A board scene's plan: what the hand writes and draws, where, and when."""

    def __init__(self, scene: Scene):
        o = scene.opts.get("board") if isinstance(scene.opts.get("board"), dict) else {}
        W, H = bw_render.W, bw_render.H
        portrait = bw_render.LAYOUT == "portrait"
        writes = [str(s) for s in (o.get("write") or [scene.headline or scene.title]) if str(s).strip()][:4]
        feats = [f for f in (bw_geo.resolve(str(n)) for n in (o.get("draw") or [])[:3]) if f is not None]
        if feats and not feats[0].parts:
            feats = []  # the drawing needs an outline to fit the others into
        self.items: list[dict[str, Any]] = []
        title_size, line_size = size("board_title"), size("board_line")
        top = bw_render.MAP_TOP + (60 if portrait else 70)
        if writes:
            self._write(writes[0], (W / 2, top), title_size, INK)
            if o.get("underline", True):
                f = bw_fx.hand_font(title_size)
                half = min(W / 2 - 80, f.getlength(writes[0]) / 2 + 20)
                y = top + title_size * 0.62
                pts = [(W / 2 - half + k * 2 * half / 30, y + 6 * math.sin(k / 4) + k * 0.4) for k in range(31)]
                self.items.append(dict(kind="line", pts=np.array(pts), rgb=RED, width=11, cost=0.4))
        lines = writes[1:]
        if feats:
            if portrait:
                box = (330, top + 170, W - 110, bw_render.CAPTION_Y - 110 - len(lines) * (line_size + 26))  # right of the mascot
            else:
                box = (420, top + 140, W / 2 + 60 if lines else W - 140, bw_render.CAPTION_Y - 90)  # clear of the mascot's corner
            self._draw(feats, box, bool(o.get("circle", True)))
        if portrait or not feats:
            y0 = (box[3] + 70) if feats else top + 250
            for k, s in enumerate(lines):
                self._write(s, (W / 2, y0 + k * (line_size + 26)), line_size, BLUE if k % 2 == 0 else RED)
        else:
            for k, s in enumerate(lines):
                self._write(s, (W * 0.75, top + 260 + k * (line_size + 40)), line_size, BLUE if k % 2 == 0 else RED)
        self._time(scene)

    def _write(self, s: str, center: tuple[float, float], px: int, rgb) -> None:
        f = bw_fx.hand_font(px)
        while f.getlength(s) > bw_render.W - 140 and px > 40:
            px -= 6
            f = bw_fx.hand_font(px)
        width = f.getlength(s)
        x0 = center[0] - width / 2
        self.items.append(dict(kind="write", text=s, x0=x0, y=center[1], width=width, px=px, rgb=rgb, cost=0.18 + 0.05 * len(s)))

    def _draw(self, feats: list[bw_geo.Feature], box: tuple[float, float, float, float], circle: bool) -> None:
        """The places' real outlines fitted into the box together, the first in black and the others in red; a place
        that is only a point (a town, a strip Natural Earth has no outline for) is circled in red instead."""
        shapes = [outline_points(f) if f.parts else None for f in feats]
        lines = [p for p in shapes if p is not None]
        if not lines:
            return
        lat0 = float(np.mean([f.label_point()[1] for f in feats]))
        k = max(0.2, math.cos(math.radians(lat0)))
        allp = np.vstack(lines)
        x0, x1 = (allp[:, 0] * k).min(), (allp[:, 0] * k).max()
        y0, y1 = allp[:, 1].min(), allp[:, 1].max()
        s = min((box[2] - box[0]) / max(1e-6, x1 - x0), (box[3] - box[1]) / max(1e-6, y1 - y0))
        ox = (box[0] + box[2]) / 2 - s * (x0 + x1) / 2
        oy = (box[1] + box[3]) / 2 + s * (y0 + y1) / 2
        last = None
        for n, (f, p) in enumerate(zip(feats, shapes)):
            if p is None:
                lon, lat = f.label_point()
                x, y = ox + s * lon * k, oy - s * lat
                last = np.array([[x - 40, y - 40], [x + 40, y + 40]])
                continue
            xy = np.column_stack([ox + s * p[:, 0] * k, oy - s * p[:, 1]])
            last = xy
            self.items.append(dict(kind="line", pts=xy, rgb=INK if n == 0 else RED, width=12 if n == 0 else 14,
                                   cost=min(2.2, max(0.7, length(xy) / 1300))))
        if circle and len(feats) > 1 and last is not None:
            (ax, ay), (bx, by) = last.min(axis=0), last.max(axis=0)
            rx, ry = max(70, (bx - ax) * 0.75), max(60, (by - ay) * 0.75)
            loop = [((ax + bx) / 2 + rx * math.cos(th) * (1 + 0.04 * math.sin(3 * th)),
                     (ay + by) / 2 + ry * math.sin(th) * (1 + 0.04 * math.cos(2 * th)))
                    for th in np.linspace(math.radians(200), math.radians(200 + 380), 70)]
            self.items.append(dict(kind="line", pts=np.array(loop), rgb=RED, width=13, cost=0.6))

    def _time(self, scene: Scene) -> None:
        """Spread the items over the scene: natural speed when there is room, faster when there is not."""
        gap = 0.14
        need = sum(it["cost"] for it in self.items) + gap * max(0, len(self.items) - 1)
        room = max(0.6, scene.end - scene.start - 0.9)
        k = min(1.15, room / max(0.1, need))
        t = scene.start + 0.35
        for it in self.items:
            it["at"], it["dur"] = t, it["cost"] * k
            t += it["dur"] + gap * k
        self.done = t - gap * k

    def frame(self, t: float) -> Image.Image:
        img = board_image()
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        pen, last = None, None
        for it in self.items:
            u = (t - it["at"]) / max(0.05, it["dur"])
            if u <= 0:
                if pen is None and last is not None:  # between two items: the pen travels to the next start
                    v = ease((t - last[2]) / max(0.05, it["at"] - last[2]))
                    s = self.start_of(it)
                    pen = (last[0] + (s[0] - last[0]) * v, last[1] + (s[1] - last[1]) * v)
                break
            if it["kind"] == "write":
                head = self.write(ov, it, u, t)
            else:
                drawn, head = prefix(it["pts"], u)
                marker(ov, drawn, it["rgb"], it["width"], shade=False)
            last = (head[0], head[1], it["at"] + it["dur"])
            if u < 1:
                pen = head
        if pen is None and last is not None:
            v = (t - self.done) / 0.45
            if v < 1:
                v = ease(max(0.0, v))
                away = (bw_render.W * 0.92, bw_render.H + 60)
                pen = (last[0] + (away[0] - last[0]) * v, last[1] + (away[1] - last[1]) * v)
        elif pen is None and self.items:
            first = self.items[0]
            v = (t - (first["at"] - 0.35)) / 0.35
            if v > 0:
                away, s = (bw_render.W * 0.92, bw_render.H + 60), self.start_of(first)
                v = ease(v)
                pen = (away[0] + (s[0] - away[0]) * v, away[1] + (s[1] - away[1]) * v)
        if pen is not None:
            draw_hand(ov, pen, t)
        img.alpha_composite(ov)
        return img

    @staticmethod
    def start_of(it: dict[str, Any]) -> tuple[float, float]:
        return (it["x0"], it["y"]) if it["kind"] == "write" else tuple(it["pts"][0])

    @staticmethod
    def write(ov: Image.Image, it: dict[str, Any], u: float, t: float) -> tuple[float, float]:
        """The words written left to right; the pen bobs up and down the letters at the writing edge."""
        f = bw_fx.hand_font(it["px"])
        x0, y = it["x0"], it["y"]
        reveal = x0 + it["width"] * min(1.0, u)
        layer = Image.new("RGBA", ov.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((x0, y), it["text"], font=f, fill=it["rgb"] + (255,), anchor="lm", stroke_width=2,
                                   stroke_fill=it["rgb"] + (255,))
        if u < 1:
            right = int(min(ov.width, reveal + 2))
            if right <= 0:
                return x0, y
            layer = layer.crop((0, 0, right, ov.height))
        ov.alpha_composite(layer, (0, 0))
        return reveal, y + it["px"] * (0.05 + 0.18 * math.sin(t * 31))


# --------------------------------------------------------------------------- the map's pen


def pen_target(scene: Scene) -> Element | None:
    """The one line the hand draws in a map scene: the first highlight (not a flag), arrow, line or sketch."""
    for el in sorted(scene.elements, key=lambda e: e.at):
        if el.kind not in PEN_KINDS or el.opts.get("flag"):
            continue
        if el.kind == "highlight" and (el.feature is None or not el.feature.parts):
            continue
        if el.kind in ("arrow", "line") and (el.feature is None or el.feature_to is None):
            continue
        if el.kind == "sketch" and el.feature is None:
            continue
        return el
    return None
