"""GTA 6 pause Shorts: the third channel (Encore Story's YouTube account) runs on GTA 6 from 2026-11-14, the owner's
call five days before the game's release (2026-11-19, PS5 / Xbox only). It is the shape SideQuestLogic proved with its
Block Short (cards from frame 0, one prize readable at a glance, a one-word comment), with GTA 6's own cars and cast:

  ride    Pause to Get Your GTA 6 Ride: a card flicks through the game's vehicles, a new one every two frames; the one a
          viewer pauses on is theirs, its rarity on the frame (grey, blue, purple, gold).
  crew    Pause to Get Your GTA 6 Partner in Crime: the same with the game's characters.
  land-X  Bet You Can't Land The Banshee (or Lucia, Jason): one target among the rest, a taunt on every other card.

The pictures are the GTA Wiki's (gta.fandom.com) GTA VI screenshots and portraits, hand-picked for being clear
(2026-11-14: half of the trailer frames there are blurred or show no car), fetched once into assets/gta6/<set>/.
The gameplay under the card is GTA VI footage from the no-copyright channels SideQuestLogic already uses, once they post
some; until then a code-drawn Vice City sunset (backdrop()). GTA V footage is never used: the title says GTA 6.
The game's sound is never used either (the radio is Content ID music): the voice and our music only.

pw_channel.make() hands a script {"format": "gta", "set": "ride"} here; the SideQuestLogic renderer
(trendclip.shorts.render_short) draws it with its own voice, captions and music, in a work folder of its own, so
nothing lands in SideQuestLogic's library.
"""

from __future__ import annotations

import json
import logging
import random
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GAME = "GTA 6"  # how the narration says it ("GTA VI" reads as "G T A vee eye")
LIBRARY_GAME = "GTA VI"  # the clip library's name for the game (trendclip.games)
KIND = "gta-"
MARK = "GTA 6 fans only."
WIKI_API = "https://gta.fandom.com/api.php"
USER_AGENT = "TrendClip/1.0 (pause Shorts; contact via the channel)"

TIERS = {"common": (150, 150, 150), "rare": (60, 140, 255), "epic": (176, 70, 255), "legendary": (255, 196, 0)}
SHARES = {"common": 0.50, "rare": 0.27, "epic": 0.16, "legendary": 0.07}

# name -> (tier, the GTA VI picture on the wiki: a File: name, or "page:<title>" for the page's main picture)
RIDES = {
    "Bison": ("common", "Bison-GTAVI-OfficialScreenshot-Grassrivers04.png"),
    "Boxville": ("common", "Boxville-GTAVI-OfficialScreenshot-Ambrosia02.png"),
    "Burrito": ("common", "Burrito-GTAVI-Trailer1.png"),
    "Caracara 4x4": ("common", "Caracara4x4-GTAVI-Trailer2.png"),
    "Chino": ("common", "Chino-GTAVI-Trailer1.png"),
    "Granger": ("common", "Granger-GTAVI-Trailer2.png"),
    "Granger 3600LX": ("common", "Granger3600LX-GTAVI-Trailer1.png"),
    "Landstalker XL": ("common", "LandstalkerXL-GTAVI-Trailer2.png"),
    "Jubilee": ("common", "Jubilee-GTAVI-Trailer1.png"),
    "Rancher": ("common", "Rancher-GTAVI-Trailer2.png"),
    "Blista Compact": ("common", "BlistaCompact-GTAVI-OfficialScreenshot-PortGellhornPostcard.png"),
    "Primo Custom": ("common", "PrimoCustom-GTAVI-Trailer1.png"),
    "Tulip": ("common", "Tulip-GTAVI-Trailer1.png"),
    "Seashark": ("common", "Seashark-GTAVI-Trailer1.png"),
    "Police Cruiser": ("common", "PoliceCruiser3-GTAVI-Trailer1.png"),
    "Blazer": ("rare", "Blazer-GTAVI-Trailer1.png"),
    "Manchez": ("rare", "Manchez-GTAVI-OfficialScreenshot-MountKalaga01.png"),
    "Sanchez": ("rare", "Sanchez-GTAVI-Trailer1.png"),
    "Avarus": ("rare", "Avarus-GTAVI-OfficialScreenshot-Ambrosia01.png"),
    "Buccaneer Custom": ("rare", "BuccaneerCustom-GTAVI-Trailer1.png"),
    "Phoenix": ("rare", "Phoenix-GTAVI-Trailer2.png"),
    "Gauntlet Hellfire": ("rare", "GauntletHellfire-GTAVI-Trailer1.png"),
    "Gauntlet Interceptor": ("rare", "GauntletInterceptor-GTAVI-Trailer1.png"),
    "Vigero ZX Convertible": ("rare", "VigeroZXConvertible-GTAVI-OfficialScreenshot-ViceCity09.png"),
    "Buffalo STX Pursuit": ("rare", "BuffaloSTXPursuit-GTAVI-Trailer1.png"),
    "Sultan": ("rare", "Sultan-GTAVI-OfficialScreenshot-ViceCity03.png"),
    "Squalo": ("rare", "GTAVI-Shitzu-Squalo-Ultimate-Edition.jpg"),
    "Banshee": ("epic", "Banshee-GTAVI-Trailer2.png"),
    "Comet Retro Custom": ("epic", "CometRetroCustom-GTAVI-Trailer1.png"),
    "Comet S2 Cabrio": ("epic", "CometS2Cabrio-GTAVI-Trailer1.png"),
    "Coquette": ("epic", "Coquette-GTAVI-Trailer2.png"),
    "Growler": ("epic", "Growler-GTAVI-Trailer2.png"),
    "Maverick": ("epic", "Maverick-GTAVI-Trailer1.png"),
    "Police Maverick": ("epic", "PoliceMaverick-GTAVI-OfficialScreenshot-Grassrivers04.png"),
    "SuperVolito": ("epic", "SuperVolito-GTAVI-Trailer1.png"),
    "Swift": ("epic", "Swift-GTAVI-OfficialScreenshot-ViceCity03.png"),
    "Dodo": ("epic", "Dodo-GTAVI-OfficialScreenshot-LeonidaKeys01.png"),
    "Buzzard Attack Chopper": ("legendary", "BuzzardAttackChopper-GTAVI-Trailer2.png"),
    "Shamal": ("legendary", "Shamal-GTAVI-OfficialScreenshot-ViceCity01.png"),
    "Jet": ("legendary", "Jet-GTAVI-Trailer2.png"),
    "Carbonizzare": ("legendary", "Carbonizzare-GTAVI-Trailer1.png"),
}
CREW = {  # the wiki's portraits; the card shows the name fans use
    "Jason": ("legendary", "page:Jason Duval"),
    "Lucia": ("legendary", "page:Lucia Caminos"),
    "Boobie Ike": ("epic", "page:Boobie Ike"),
    "Raul Bautista": ("epic", "page:Raul Bautista"),
    "Cal Hampton": ("epic", "page:Cal Hampton"),
    "Dre'Quan Priest": ("epic", "page:Dre'Quan Priest"),
    "Real Dimez": ("epic", "page:Real Dimez"),
    "Brian Heder": ("rare", "page:Brian Heder"),
    "Andrés de León": ("rare", "page:Andrés de León"),
    "Petra Navarro": ("rare", "page:Petra Navarro"),
    "Roxy": ("rare", "page:Roxy"),
    "Mendez": ("rare", "page:Mendez"),
    "Macca the Gator": ("rare", "page:Macca the Gator"),
    "Phil": ("common", "page:Phil (GTA VI)"),
    "Stefanie": ("common", "page:Stefanie"),
    "Aunt Tee": ("common", "page:Aunt Tee"),
    "Raymond": ("common", "page:Raymond (GTA VI)"),
    "The Hammer Lady": ("common", "page:Unnamed GTA VI Hamlet woman"),
    "Marina Worker": ("common", "page:Unnamed Marina worker"),
    "Prison Guard": ("common", "page:Unnamed GTA VI corrections officer"),
    "The Interrogator": ("common", "page:Unnamed Interrogator"),
    "The Businessman": ("common", "page:Unnamed Businessman (VI)"),
}
SETS = {
    "ride": {"items": RIDES, "noun": "ride", "Noun": "Ride", "emoji": "🚁", "pic": (620, 349),
             "line": "The ride you stop on is yours.", "comment": "Comment your ride.", "end": "Comment your ride"},
    "crew": {"items": CREW, "noun": "partner", "Noun": "Partner In Crime", "emoji": "😈", "pic": (520, 520),
             "line": "Whoever you stop on is your partner in crime.", "comment": "Comment your partner.",
             "end": "Comment your partner"},
}
LANDS = {  # name -> the set it deals from, the target, how the narration names it, the word to comment
    "banshee": {"set": "ride", "target": "Banshee", "said": "the Banshee", "word": "banshee"},
    "lucia": {"set": "crew", "target": "Lucia", "said": "Lucia", "word": "lucia"},
    "jason": {"set": "crew", "target": "Jason", "said": "Jason", "word": "jason"},
    "jet": {"set": "ride", "target": "Jet", "said": "the jet", "word": "jet"},
}
ROTATION = ["ride", "land-banshee", "crew", "land-lucia", "ride", "land-jason", "crew", "land-jet"]

PRIZE_SCRIPT = (MARK + " Pause the video right now. {line} Grey is common. Blue is rare. Purple is epic. Gold is "
                "legendary. Is yours gold? {comment} Then send this to a friend, and see who gets the better one.")
LAND_SCRIPT = (MARK + " Pause this video on {said}. Only one in a hundred can land it. If you land it, comment "
               "{word}. Bet you can't.")
PRIZE_TITLE = "Pause To Get Your GTA 6 {Noun} {emoji}"
LAND_TITLES = ["GTA 6: Bet You Can't Land {Said} 😤🔥", "GTA 6: Only 1% Can Land {Said} 😤🔥",
               "GTA 6: Can You Land {Said}? 😤🔥", "GTA 6: You Won't Land {Said}... 😤🔥",
               "GTA 6: Land {Said} If You Can 😤🔥"]  # the game in every title: it is what people search
PRIZE_PINNED = "What {noun} did you get? 👇 Gold = legendary 🏆\nSend this to a friend and see who gets the better one."
LAND_PINNED = "Did you land {said}? 👇 Comment {word} if you did.\nSend this to a friend: bet they can't."
HASHTAGS = ["GTA6", "GTAVI", "pausechallenge", "gaming", "shorts"]
CARD = "PAUSE NOW!"
CATEGORY = "20"  # Gaming
WIDTH, HEIGHT = 1080, 1920
CARD_W = 680
CENTER = (WIDTH // 2, 800)
FRAME = 18
HOLD = 2  # video frames per card, like SideQuestLogic's


def is_gta(script: dict) -> bool:
    return str(script.get("format") or "") == "gta"


def mode_on() -> bool:
    """The channel makes GTA 6 Shorts (PW_FORMAT=gta): an empty queue is filled with the next set of the rotation."""
    from modules.pw_channel import setting

    return setting("PW_FORMAT", "").strip().lower() == "gta"


# --------------------------------------------------------------------------- the pictures


def assets_dir() -> Path:
    from trendclip.config import get_settings

    return get_settings().assets_dir / "gta6"


def set_dir(name: str) -> Path:
    return assets_dir() / name


def fetch(name: str, session=None) -> list[dict]:
    """Download the set's pictures from the GTA Wiki into assets/gta6/<set>/ (catalog.json). Pictures already there are
    kept."""
    import io

    import requests
    from PIL import Image

    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    folder = set_dir(name)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        found = {e["name"]: e for e in json.loads((folder / "catalog.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        found = {}
    items = SETS[name]["items"]
    for item, (_, where) in items.items():
        if item in found and (folder / found[item]["file"]).is_file():
            continue
        if where.startswith("page:"):
            params = {"titles": where[5:], "prop": "pageimages", "piprop": "original"}
        else:
            params = {"titles": "File:" + where, "prop": "imageinfo", "iiprop": "url"}
        try:
            r = session.get(WIKI_API, params={"action": "query", "redirects": 1, "format": "json", **params}, timeout=30)
            page = next(iter(r.json()["query"]["pages"].values()))
            src = (page.get("original") or {}).get("source") or ((page.get("imageinfo") or [{}])[0]).get("url")
            if not src:
                raise ValueError("no picture on the page")
            img = Image.open(io.BytesIO(session.get(src, timeout=30).content)).convert("RGB")
        except Exception as err:  # noqa: BLE001 - one missing picture leaves the rest
            logger.warning("No GTA Wiki picture for %s (%s)", item, err)
            continue
        file = re.sub(r"[^A-Za-z0-9]+", "-", item).strip("-") + ".jpg"
        img.save(folder / file, quality=92)
        found[item] = {"name": item, "file": file, "source": src.split("/revision/")[0]}
    (folder / "catalog.json").write_text(json.dumps(list(found.values()), indent=1, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
    return catalog(name)


def catalog(name: str) -> list[dict]:
    """The set's pictures on this machine with their tier, empty unless every tier has some."""
    folder = set_dir(name)
    try:
        entries = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = SETS[name]["items"]
    out = [{**e, "path": str(folder / e["file"]), "tier": items[e["name"]][0]}
           for e in entries if e["name"] in items and (folder / e["file"]).is_file()]
    return out if len({e["tier"] for e in out}) == len(TIERS) else []


# --------------------------------------------------------------------------- the scripts


def words_for(kind: str, rng: random.Random | None = None) -> dict[str, Any]:
    """Narration, title, pinned comment, description and end card of a kind ("ride", "crew", "land-banshee")."""
    rng = rng or random.Random()
    if kind.startswith("land-"):
        land = LANDS[kind.removeprefix("land-")]
        said = land["said"]
        fill = {"said": said, "Said": " ".join(w[:1].upper() + w[1:] for w in said.split()), "word": land["word"]}
        return {"script": LAND_SCRIPT.format(**fill), "title": rng.choice(LAND_TITLES).format(**fill),
                "pinned": LAND_PINNED.format(**fill), "end_card": f"Comment {land['word']}",
                "description": f"Pause the video on {said} from GTA 6. Only 1% land it. Comment {land['word']} if you "
                               "did. #GTA6"}
    s = SETS[kind]
    return {"script": PRIZE_SCRIPT.format(**s), "title": PRIZE_TITLE.format(**s), "pinned": PRIZE_PINNED.format(**s),
            "end_card": s["end"],
            "description": f"Pause the video: the GTA 6 {s['noun']} you stop on is yours. Comment what you got and "
                           "challenge a friend. #GTA6"}


def kind_of(script_text: str) -> str:
    """The cards a GTA 6 Short deals, read off its fixed narration ("gta-ride", "gta-land-banshee"); "" for any
    other Short (spin.kind_of asks here first)."""
    text = script_text or ""
    if MARK not in text:
        return ""
    for name, land in LANDS.items():
        if f"Pause this video on {land['said']}." in text:
            return KIND + "land-" + name
    return next((KIND + n for n, s in SETS.items() if s["line"] in text), "")


def new_script(queue: Path, kind: str | None = None, now: datetime | None = None) -> Path:
    """A script for the next slot, the rotation's next kind (or `kind`), into the channel's queue."""
    queue.mkdir(parents=True, exist_ok=True)
    made = sorted(queue.glob("*-gta-*.json"))
    kind = kind or ROTATION[len(made) % len(ROTATION)]
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = queue / f"{stamp}-gta-{kind}.json"
    words = words_for(kind)
    path.write_text(json.dumps({"format": "gta", "set": kind, "title": words["title"]}, indent=2,
                               ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- the cards


def _font(size: int):
    from trendclip.reveal import _font as font

    return font(size)


def _cover(img, box: tuple[int, int], top_bias: float = 0.5):
    """The picture cropped to fill `box` (portraits keep their top: the face)."""
    from PIL import Image

    bw, bh = box
    scale = max(bw / img.width, bh / img.height)
    img = img.resize((max(bw, round(img.width * scale)), max(bh, round(img.height * scale))), Image.LANCZOS)
    x = (img.width - bw) // 2
    y = round((img.height - bh) * top_bias)
    return img.crop((x, y, x + bw, y + bh))


def card(entry: dict, pic: tuple[int, int], cache: dict, label: str = "", colour: tuple | None = None,
         notes: tuple[str, ...] = ()):
    """The picture on a dark card framed in its tier's colour: the tier (or the taunt) on top, the name under it."""
    from PIL import Image, ImageDraw

    tier = entry["tier"]
    colour = colour or TIERS[tier]
    pw, ph = pic
    key = (entry["name"], pic)
    if key not in cache:
        src = Image.open(entry["path"]).convert("RGB")
        cache[key] = _cover(src, pic, 0.15 if pw == ph else 0.5)
    h = FRAME + 110 + ph + 30 + 2 * 54 + 100
    img = Image.new("RGBA", (CARD_W, h), colour + (255,))
    d = ImageDraw.Draw(img)
    d.rectangle([FRAME, FRAME, CARD_W - FRAME, h - FRAME], fill=(22, 14, 34))
    d.rectangle([FRAME, FRAME, CARD_W - FRAME, FRAME + 92], fill=colour)
    text = label or tier.upper()
    size = 64
    while size > 36 and d.textlength(text, font=_font(size)) > CARD_W - 2 * FRAME - 24:
        size -= 4
    dark = sum(colour) > 480 or colour in (TIERS["common"], TIERS["legendary"], (60, 200, 90))
    d.text((CARD_W / 2, FRAME + 46), text, font=_font(size), fill=(20, 20, 20) if dark else (255, 255, 255), anchor="mm")
    top = FRAME + 110
    left = (CARD_W - pw) // 2
    d.rounded_rectangle([left - 8, top - 8, left + pw + 8, top + ph + 8], radius=18, fill=colour)
    img.paste(cache[key], (left, top))
    for i, note in enumerate(notes):  # SUB TO CLAIM IT / COMMENT "GOLD FOX"
        size = 46
        while size > 28 and d.textlength(note, font=_font(size)) > CARD_W - 2 * FRAME - 30:
            size -= 2
        d.text((CARD_W / 2, top + ph + 44 + i * 54), note, font=_font(size), fill=colour, anchor="mm")
    name = entry["name"].upper()
    size = 60
    while size > 34 and d.textlength(name, font=_font(size)) > CARD_W - 2 * FRAME - 30:
        size -= 4
    d.text((CARD_W / 2, h - FRAME - 30 - size / 2), name, font=_font(size), fill=(255, 255, 255), anchor="mm")
    return img


def deck(entries: list[dict], count: int, rng: random.Random) -> list[dict]:
    """`count` cards: each tier its share, never the same picture twice in a row."""
    by_tier = {t: [e for e in entries if e["tier"] == t] for t in TIERS}
    tiers = [t for t in TIERS if by_tier[t]]
    out: list[dict] = []
    for _ in range(count):
        while True:
            pick = rng.choice(by_tier[rng.choices(tiers, [SHARES[t] for t in tiers])[0]])
            if not out or pick["name"] != out[-1]["name"]:
                break
        out.append(pick)
    return out


def plan(settings, words, kind: str, seed: str = "", tail: float = 2.0, fps: int = 30):
    """The card overlay for the whole video (an animated WebP, a new card every HOLD frames from frame 0), like
    spin.plan; its codes saved beside the Short for the comments."""
    from trendclip import spin
    from trendclip.video_assembler import Overlay

    kind = kind.removeprefix(KIND)
    rng = random.Random(seed or "gta")
    total = words[-1].end + tail
    count = int(total * fps / HOLD) + 2
    cache: dict = {}
    if kind.startswith("land-"):
        land = LANDS[kind.removeprefix("land-")]
        entries = catalog(land["set"])
        if not any(e["name"] == land["target"] for e in entries):
            raise ValueError(f"No picture of {land['target']}: run modules.gta6 fetch {land['set']}")
        code = spin.codes(seed or "gta", land["word"].upper())
        win = (spin.CLAIM, f'COMMENT "{code["win"]}"')
        pic = SETS[land["set"]]["pic"]
        cards = [card(e, pic, cache, label, colour, win if e["name"] == land["target"] else ())
                 for e, label, colour in spin.land_deck(entries, land["target"], count, rng)]
    else:
        entries = catalog(kind)
        if not entries:
            raise ValueError(f"No {kind} pictures: run modules.gta6 fetch {kind}")
        code = spin.codes(seed or "gta", "GOLD")
        win = (spin.CLAIM, f'COMMENT "{code["win"]}"')
        cards = [card(e, SETS[kind]["pic"], cache, notes=win if e["tier"] == "legendary" else ())
                 for e in deck(entries, count, rng)]
    secret = spin.add_secret(cards, code["secret"], seed or "gta", fps=fps)
    if seed and re.fullmatch(r"[\w.-]+", seed):
        settings.shorts_dir.mkdir(parents=True, exist_ok=True)
        (settings.shorts_dir / f"{seed}.codes.json").write_text(json.dumps({**code, "secret_at": secret}, indent=1) + "\n",
                                                                encoding="utf-8")
    out = settings.assets_dir / "cache" / "gta6"
    out.mkdir(parents=True, exist_ok=True)
    path = out / (re.sub(r"[^a-z0-9]+", "-", (seed or "gta").lower())[:40].strip("-") + ".webp")
    cards[0].save(path, save_all=True, append_images=cards[1:], duration=round(1000 * HOLD / fps), loop=0,
                  lossless=False, quality=90)
    return [Overlay(path, 0.0, round(total + 0.5, 3), center=CENTER, box=max(cards[0].size), style="asis", cut=True,
                    animated=True)]


# --------------------------------------------------------------------------- the background


BACKDROP = "gta6-vice-sunset.mp4"  # no sidecar: never one of SideQuestLogic's random clips


def backdrop(settings, seconds: int = 60, fps: int = 30, force: bool = False) -> Path:
    """A Vice City sunset drawn in code (until GTA VI gameplay is there): a striped sun sinking behind the sea, a
    neon grid rolling toward the viewer, palms at the sides. Made once into assets/backgrounds/."""
    import numpy as np
    from PIL import Image, ImageDraw

    path = settings.backgrounds_dir / BACKDROP
    if path.is_file() and not force:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h, horizon = WIDTH, HEIGHT, 1180
    ys = np.linspace(0, 1, horizon)[:, None]
    stops = [(0.0, (20, 8, 52)), (0.45, (110, 26, 110)), (0.8, (255, 84, 120)), (1.0, (255, 170, 90))]
    sky = np.zeros((horizon, w, 3), np.float32)
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        m = ((ys >= a) & (ys <= b)).astype(np.float32)
        f = np.clip((ys - a) / (b - a), 0, 1)
        sky += m[:, :, None] * (np.array(ca) * (1 - f[:, :, None]) + np.array(cb) * f[:, :, None])
    sky = np.clip(sky, 0, 255)
    yy, xx = np.mgrid[0:horizon, 0:w]
    cx, cy, r = w // 2, 960, 330
    disc = ((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r
    sun_col = np.zeros((horizon, w, 3), np.float32)
    g = np.clip((yy - (cy - r)) / (2 * r), 0, 1)[:, :, None]
    sun_col[:] = np.array((255, 236, 120)) * (1 - g) + np.array((255, 70, 150)) * g
    sea_rows = h - horizon
    sea = np.zeros((sea_rows, w, 3), np.float32)
    f = np.linspace(0, 1, sea_rows)[:, None, None]
    sea[:] = np.array((60, 16, 90)) * (1 - f) + np.array((8, 4, 26)) * f
    palms = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(palms)
    for base_x, lean, height in ((60, 1, 980), (1010, -1, 1080), (190, 1, 760)):
        top = (base_x + lean * 140, h - height)
        pts = [(base_x + lean * 140 * (k / 20) ** 1.6, h - height * k / 20) for k in range(21)]
        for k in range(20):
            pd.line([pts[k], pts[k + 1]], fill=(10, 4, 20, 255), width=int(34 - k))
        import math

        for ang, reach in ((-170, 260), (-140, 240), (-105, 170), (-75, 170), (-40, 240), (-10, 260), (200, 200),
                           (-20 if lean > 0 else 160, 220)):
            a = math.radians(ang)  # a frond: a leaf that arcs out and droops, wide at its middle, pointed at its tip
            spine = []
            for k in range(13):
                u = k / 12
                spine.append((top[0] + reach * u * math.cos(a), top[1] + reach * u * math.sin(a) * 0.5 + 150 * u * u))
            left, right = [], []
            for k, (x, y) in enumerate(spine):
                u = k / 12
                half = 26 * math.sin(math.pi * min(1, u * 1.1)) + 2
                left.append((x, y - half))
                right.append((x, y + half))
            pd.polygon(left + right[::-1], fill=(10, 4, 20, 255))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r",
           str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for n in range(seconds * fps):
        t = n / fps
        top = sky.copy()
        stripe = ((yy - cy) > 0) & (((yy + t * 24) % 54) < np.clip((yy - cy) / r * 26, 0, 26))
        mask = disc & ~stripe
        top[mask] = sun_col[mask]
        bottom = sea.copy()
        rows = np.arange(sea_rows)
        shimmer = (np.sin(rows * 0.09 + t * 2.6) > 0.55) & (rows < 520)
        width = (r * (1 - rows / 600)).clip(20)
        for y in np.nonzero(shimmer)[0][::2]:
            half = int(width[y] * (0.6 + 0.4 * np.sin(y * 0.3 + t * 4)))
            bottom[y, cx - half:cx + half] = bottom[y, cx - half:cx + half] * 0.3 + np.array((255, 120, 160)) * 0.7
        frame = Image.fromarray(np.concatenate([top, bottom]).astype(np.uint8))
        d = ImageDraw.Draw(frame)
        for k in range(12):  # the grid rolling toward the viewer
            z = ((k + (t * 0.8) % 1) / 12) ** 2.2
            y = horizon + z * sea_rows
            d.line([(0, y), (w, y)], fill=(255, 60, 200), width=max(1, int(1 + z * 5)))
        for k in range(-9, 10):
            d.line([(cx, horizon), (cx + k * 260, h)], fill=(255, 60, 200), width=2)
        frame.paste(palms, (int(6 * np.sin(t * 0.7)), 0), palms)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait():
        raise RuntimeError("ffmpeg could not write the Vice City backdrop")
    return path


def gameplay(settings, progress=lambda m: None) -> str:
    """The background clip's file name: GTA VI footage from the no-copyright channels when there is some (its title
    says GTA 6 and not GTA 5), else the drawn Vice City sunset."""
    from trendclip import video_downloader

    def real(clip) -> bool:
        title = (clip.title or "").lower()
        return clip.game == LIBRARY_GAME and bool(re.search(r"gta\s*(6|vi)\b|grand theft auto (6|vi)\b", title)) \
            and not re.search(r"gta\s*(5|v)\b|grand theft auto (5|v)\b|gta online|fivem|mod\b", title)

    try:
        progress("Looking for GTA 6 gameplay")
        clip = video_downloader.download_background(LIBRARY_GAME, settings, sources=["youtube"])
        if real(clip):
            return clip.filename
        logger.info("Downloaded %r is not GTA 6 footage", clip.title)
    except Exception as err:  # noqa: BLE001 - no GTA 6 footage yet: the drawn backdrop
        logger.info("No GTA 6 gameplay to download (%s)", err)
    have = [c for c in video_downloader.list_backgrounds(settings) if real(c)]
    if have:
        return random.choice(have).filename
    progress("No GTA 6 gameplay yet: the Vice City sunset")
    return backdrop(settings).name


# --------------------------------------------------------------------------- making one


def make(script: dict, work: Path, progress=lambda f, m: None) -> dict[str, Any]:
    """Draw a GTA 6 Short into `work` (video.mp4, thumb.jpg): its texts for the channel's info.json."""
    import shutil

    from trendclip import quiz, shorts
    from trendclip.config import get_settings

    kind = str(script.get("set") or "ride")
    if kind not in SETS and kind.removeprefix("land-") not in LANDS:
        raise ValueError(f"Unknown GTA 6 set {kind!r}")
    settings = get_settings().model_copy(update={"output_dir": work})
    need = LANDS[kind.removeprefix("land-")]["set"] if kind.startswith("land-") else kind
    if not catalog(need):
        progress(None, "Fetching the GTA 6 pictures")
        fetch(need)
    words = words_for(kind, random.Random(str(script.get("title") or work.name)))
    clip = gameplay(settings, lambda m: progress(None, m))
    req = shorts.RenderRequest(
        clip=clip, game=GAME, script=words["script"], title=script.get("title") or words["title"], mode="gamer",
        description=words["description"], hashtags=["#" + h for h in HASHTAGS], title_card=CARD,
        end_card=words["end_card"], pinned_comment=words["pinned"], music_source="mood", music_mood=quiz.MUSIC_MOOD,
        spin=True, stickers=False, cta_sticker=True, output_name="gta6", channel_handle="")
    try:
        short = shorts.render_short(settings, req, lambda f, m: progress(f, m))
    except Exception as err:  # noqa: BLE001 - music trouble: the video without it
        from trendclip import music

        if not isinstance(err, music.MusicError):
            raise
        short = shorts.render_short(settings, req.model_copy(update={"music_source": "none"}), lambda f, m: progress(f, m))
    made = settings.shorts_dir / short.filename
    shutil.copyfile(made, work / "video.mp4")
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.4", "-i", str(work / "video.mp4"), "-frames:v",
                        "1", "-q:v", "2", str(work / "thumb.jpg")], check=True, timeout=60)
    except Exception:  # noqa: BLE001 - YouTube picks its own frame
        logger.exception("GTA 6 thumbnail failed")
    codes = settings.shorts_dir / "gta6.codes.json"
    return {"title": req.title, "description": short.description, "hashtags": HASHTAGS,
            "comment": words["pinned"], "category": CATEGORY, "duration_seconds": short.duration_seconds,
            "ear": short.ear, "background": clip,
            "codes": json.loads(codes.read_text(encoding="utf-8")) if codes.is_file() else None,
            "thumbnail": "thumb.jpg" if (work / "thumb.jpg").is_file() else None}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="GTA 6 pause Shorts: fetch the pictures, draw the backdrop, make a sample")
    p.add_argument("what", choices=["fetch", "backdrop", "sample"])
    p.add_argument("name", nargs="?", default="ride")
    p.add_argument("--out", default="")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO)
    if a.what == "fetch":
        for n in ([a.name] if a.name in SETS else list(SETS)):
            print(n, len(fetch(n)), "pictures")
    elif a.what == "backdrop":
        from trendclip.config import get_settings

        print(backdrop(get_settings(), force=True))
    else:
        out = Path(a.out or f"output/gta6-sample-{a.name}")
        out.mkdir(parents=True, exist_ok=True)
        info = make({"format": "gta", "set": a.name}, out, lambda f, m: print(f"[{int((f or 0) * 100)}%] {m}"))
        print(json.dumps(info, indent=1, ensure_ascii=False, default=str))
