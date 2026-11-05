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

The countries are the characters (the owner, 2026-11-05, after @HeyHistorically's 15M-view "weird borders of the
USA" Short: a Canadian and an American arguing over the map): a local with a "flag" speaks for that country, its
round flag badge pinned at its shoulder, and "say" is what it says on its react word, 1-5 words in a speech bubble
over its head, in the channel's marker hand. Two sides face each other across a border, one local each.

Side families (the owner, 2026-11-05 night): diplomat, general, merchant, sultan, emperor, chief, fisherman, child,
village_woman and blacksmith came as <name>_blue.gif and <name>_red.gif, one person in the two sides' coats, each
GIF a calm stance, a gesture (a talk, a salute, a wave) and back. Side "a" wears blue, side "b" red (the script's
"color" letters), so the two sides of a quarrel read apart at a glance; the gesture plays on the react word
whatever the mood. Any <name>_blue the owner adds makes <name> a side family.
A side family can also have poses: <name>_pointing_<coat> (one arm out, pointing to the right: the argument pose,
played for a shouted line, mirrored for the side standing on the right so it points at the other side) and
<name>_arguing_<coat> (both hands up, talking: the reaction for the other moods). The first came as "character"
(a uniformed guard, 2026-11-05 night); the script names him "guard".
Looks: <name>_arab_blue / _red (2026-11-06) is the same person in Middle Eastern dress; the script names
"general_arab", "merchant_arab"... for a Middle Eastern or North African side.
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
FAMILY_MOODS = {"shocked": "surprised", "sad": "sad", "grin": "happy", "smile": "happy", "smug": "happy", "wink": "happy",
                "point": "pointing"}  # "point": the argument pose (NAME_pointing.gif, asked for in stickers/borderlinewhy/WANTED.md)
FAMILY_NAMES = {"villager": "citizen", "person": "citizen", "migrant": "citizen", "local": "citizen", "people": "citizen",
                "pioneer": "settler", "settlers": "settler", "colonist": "settler", "homesteader": "settler",
                "soldiers": "soldier", "troops": "soldier", "army": "soldier", "monarch": "king", "farmers": "farmer",
                "diplomats": "diplomat", "ambassador": "diplomat", "envoy": "diplomat", "negotiator": "diplomat", "minister": "diplomat",
                "generals": "general", "commander": "general", "officer": "general", "admiral": "general",
                "merchants": "merchant", "trader": "merchant", "traders": "merchant", "company": "merchant", "banker": "merchant",
                "emir": "sultan", "caliph": "sultan", "sheikh": "sultan", "shah": "sultan", "pasha": "sultan",
                "empress": "emperor", "tsar": "emperor", "pharaoh": "emperor", "shogun": "emperor",
                "chieftain": "chief", "tribe": "chief", "elder": "chief", "fishermen": "fisherman", "sailor": "fisherman",
                "kid": "child", "children": "child", "boy": "child", "girl": "child", "woman": "village_woman", "women": "village_woman",
                "mother": "village_woman", "villager_woman": "village_woman", "smith": "blacksmith", "worker": "blacksmith", "workers": "blacksmith",
                "guard": "character", "guards": "character", "sentry": "character", "border_guard": "character", "policeman": "character"}
LOOKS = ("arab",)  # a family's other looks: <name>_<look>_blue / _red
POSES = ("pointing", "arguing")  # a side family's poses: <name>_<pose>_<coat>
SIDE_COLORS = {"a": "blue", "b": "red"}  # a side family's coat by the script's side letter (any other letter: blue)
_families: dict[tuple[str, int], dict[str, tuple[list[Image.Image], list[float]]]] = {}
POP = 0.4  # seconds of the hop when a mood changes
BADGE_R = 44  # the flag badge at a country's shoulder (radius, at LOCAL_SIZE; scaled with the character)
SAY_WORDS = 5  # the most words a country says in one bubble
SAY_PX = 56  # the bubble's hand lettering
HEADLINE_CLEAR = 380  # a bubble never rises into the headline band (bw_fx.HEADLINE_BOTTOM + a little)
_discs: dict[tuple[str, int], Image.Image] = {}


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
    head, _, look = key.partition("_")  # "merchants_arab": the merchant family in the Arab look
    tries = [key, FAMILY_NAMES.get(key, "")]
    if look in LOOKS:
        tries += [f"{FAMILY_NAMES.get(head, head)}_{look}"]
    tries += [head, FAMILY_NAMES.get(head, "")]
    for c in tries:
        if c and (f"{c}_happy" in files or f"{c}_blue" in files or f"{c}_pointing_blue" in files or f"{c}_arguing_blue" in files):
            return c
    return None


def sided(base: str) -> bool:
    """A side family: the owner drew the person in the two sides' coats (<base>_blue and <base>_red, or posed)."""
    files = bw_fx.custom_icons()
    return f"{base}_blue" in files or any(f"{base}_{pose}_blue" in files for pose in POSES)


def family_frames(base: str, size: int) -> dict[str, tuple[list[Image.Image], list[float]]]:
    """A family's GIFs by mood ("happy", "surprised", "sad"), each scaled so its calm stance is `size` tall and
    cropped around it so the stance sits in the middle (a jump reaches above it); with the frames' end times."""
    key = (base, size)
    if key not in _families:
        files = bw_fx.custom_icons()
        raw: dict[str, tuple[list[Image.Image], list[float]]] = {}
        for mood in ("happy", "surprised", "sad", "pointing", "blue", "red", *[f"{pose}_{coat}" for pose in POSES for coat in ("blue", "red")]):
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


def character(name: str = "", color: str = "") -> str:
    """The character the script names: "@<family>" for a mood family ("@<family>:<coat>" for a side family, the
    coat by the side letter `color`), else the GIF's file name (an icon name or an alias like "farmer"); the citizen
    when the name is empty or unknown."""
    if name and (base := family(name)):
        return f"@{base}:{SIDE_COLORS.get(color, 'blue')}" if sided(base) else f"@{base}"
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
        size = round(size * 1.3)  # as tall as the other stickers read (bw_fx fits those a third bigger)
        base, _, coat = code[1:].partition(":")  # "@diplomat:red": a side family in that side's coat
        fam = family_frames(base, size)
        if coat:  # the coat's clips: the pointing pose for "point", the arguing pose or the plain gesture otherwise
            mine = {m: fam[m] for m in (f"pointing_{coat}", f"arguing_{coat}", coat) if m in fam} or fam
            want = [f"pointing_{coat}"] if mood == "point" else [f"arguing_{coat}", coat]
            pick = next((m for m in want if m in mine), next(iter(mine)))
            fam = {pick: mine[pick]}
            clip = mine[pick] if reacted else None
        else:
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


def flag_disc(flag: str, r: int) -> Image.Image:
    """The country's flag as a round badge with a white rim (the war map's badge, smaller)."""
    key = (flag, r)
    if key not in _discs:
        src = bw_fx.flag_image(flag)
        k = 2 * r / min(src.width, src.height)
        im = src.resize((max(2 * r, int(src.width * k)), max(2 * r, int(src.height * k))), Image.LANCZOS)
        left, top = (im.width - 2 * r) // 2, (im.height - 2 * r) // 2
        _discs[key] = im.crop((left, top, left + 2 * r, top + 2 * r)).convert("RGBA")
    rim = 8
    size = 2 * r + 2 * rim
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse([0, 0, size - 1, size - 1], fill=(255, 255, 255, 255), outline=(30, 32, 40, 255), width=3)
    mask = Image.new("L", (2 * r, 2 * r), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, 2 * r - 1, 2 * r - 1], fill=255)
    disc.paste(_discs[key], (rim, rim), mask)
    return disc


def badge(ov: Image.Image, flag: str, xy: tuple[float, float], size: int, t: float, at: float) -> None:
    """The flag badge pinned at the character's left shoulder (xy: the character's centre; size: its height), popping
    in with it and swaying a little."""
    s = bw_fx.backout((t - at) / 0.45)
    if s <= 0.02:
        return
    r = max(10, round(BADGE_R * size / 250))
    disc = flag_disc(flag, r)
    px = max(4, int(disc.width * s))
    disc = disc.resize((px, px), Image.BILINEAR)
    x = xy[0] - size * 0.36
    y = xy[1] - size * 0.22 + 4 * math.sin(2.4 * t + at)
    sh = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([0, 0, px - 1, px - 1], fill=(0, 0, 0, 100))
    ov.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6)), (int(x - px / 2 + 5), int(y - px / 2 + 7)))
    ov.alpha_composite(disc, (int(x - px / 2), int(y - px / 2)))


def bubble_box(say: str, head: tuple[float, float], width: int = 1080, lean: int | None = None) -> tuple[int, int, int, int]:
    """Where a character's speech bubble sits (x0, y0, x1, y1) for the text and the head's top point: above the
    head, its box leaning to `lean` (-1 left, 1 right: away from the other side it faces), else toward the middle
    of the screen; kept on the screen, the tail still over the head."""
    f = bw_fx.hand_font(SAY_PX)
    pad_x, pad_y = 36, 22
    bw = int(f.getlength(say) + 2 * pad_x)
    bh = int(SAY_PX * 1.25 + 2 * pad_y)
    tail = 46
    side = lean if lean else (1 if head[0] < width / 2 else -1)
    cx = head[0] + side * (bw / 2 - 40 if lean else 90)  # away from the other side: the box clear of its bubble
    x0 = int(min(max(30, cx - bw / 2), width - 30 - bw))
    y1 = int(head[1] - tail - 10)
    if y1 - bh < HEADLINE_CLEAR:  # a character standing high: the bubble sits beside its head, under the headline
        y0 = int(max(HEADLINE_CLEAR, head[1] - bh / 2 + 30))
        x0 = int(head[0] + 70 if side > 0 else head[0] - 70 - bw)
        x0 = int(min(max(30, x0), width - 30 - bw))
        return (x0, y0, x0 + bw, y0 + bh)
    return (x0, y1 - bh, x0 + bw, y1)


def say(ov: Image.Image, text: str, head: tuple[float, float], t: float, since: float, lean: int | None = None) -> None:
    """What the character says, in a white bubble in the marker hand over its head, popping at `since` and staying."""
    s = bw_fx.backout((t - since) / 0.3)
    if s <= 0.02 or not text:
        return
    x0, y0, x1, y1 = bubble_box(text, head, ov.width, lean)
    bw, bh, tail = x1 - x0, y1 - y0, 46
    m = 14 + tail
    shape = Image.new("L", (bw + 2 * m, bh + 2 * m), 0)
    ds = ImageDraw.Draw(shape)
    ds.rounded_rectangle([m, m, m + bw, m + bh], radius=34, fill=255)
    if y1 <= head[1] - 20:  # the box above the head: the tail points down at it
        tx = int(min(max(head[0], x0 + 50), x1 - 50)) - x0 + m
        tip = (tx, m + bh + tail)
        ds.polygon([(tx - 26, m + bh - 4), (tx + 26, m + bh - 4), tip], fill=255)
    else:  # beside the head: the tail points sideways at it from the nearer edge
        ty = int(min(max(head[1] + 20, y0 + 40), y1 - 40)) - y0 + m
        if head[0] < x0:
            tip = (m - tail, ty)
            ds.polygon([(m + 4, ty - 24), (m + 4, ty + 24), tip], fill=255)
        else:
            tip = (m + bw + tail, ty)
            ds.polygon([(m + bw - 4, ty - 24), (m + bw - 4, ty + 24), tip], fill=255)
    layer = Image.new("RGBA", shape.size, (30, 32, 40, 0))
    layer.putalpha(shape.filter(ImageFilter.MaxFilter(9)))  # the ink outline
    layer.paste(Image.new("RGBA", shape.size, (255, 255, 255, 255)), (0, 0), shape)
    ImageDraw.Draw(layer).text((m + bw / 2, m + bh / 2), text, font=bw_fx.hand_font(SAY_PX), fill=(30, 32, 40, 255), anchor="mm")
    k = 0.6 + 0.4 * s
    if abs(k - 1) > 0.01:
        layer = layer.resize((max(1, int(layer.width * k)), max(1, int(layer.height * k))), Image.BILINEAR)
    tip = (tip[0] * k, tip[1] * k)  # it grows from the tail's tip on the head
    anchor = (x0 - m + tip[0] / k, y0 - m + tip[1] / k)  # where the tip lands on the frame at full size
    px, py = int(anchor[0] - tip[0]), int(anchor[1] - tip[1] + 2 * math.sin(2.4 * t))
    sh = layer.getchannel("A").point(lambda v: int(v * 0.35))
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow.putalpha(sh)
    ov.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)), (px + 6, py + 10))
    ov.alpha_composite(layer, (px, py))
