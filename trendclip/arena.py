"""Three more pause Shorts on the flicking cards (spin.py), the owner's picks of 2026-11-11. Each gives a viewer a
reason to pause again, to send it on, and to come back: the opponent changes from Short to Short.

- friend  "Pause To Fight Your Friend": two weapons flick side by side, the left one yours, the right one your
          friend's; more attack damage wins (minecraft.wiki's Java numbers, spin.WEAPONS). Made for sharing: you
          send it to the friend you want to fight.
- pet     "Pause To Get Your Mob vs The <boss>": a boss stands on top, the mobs flick under it, from a chicken to
          a warden; the one you stop on fights for you. Hit for hit, your mob first: it wins when it needs no
          more hits than the boss does (health, armour and attack damage in Java Edition on Normal).
- double  "Pause Twice To Beat The <mob>": a mini adventure with two pauses. Five hearts; the first pause opens a
          chest (food gives hearts back, rotten flesh takes one), the second gives the weapon for the fight (a
          slow weapon costs up to three hearts). The comment is the hearts left: one to seven.

Asked for in the Create tab's game choice; no Gemini. Every one hides the video's mythic card (spin.add_mythic).
"""

from __future__ import annotations

import math
import random
import re
from types import SimpleNamespace

from . import spin
from .config import Settings
from .video_assembler import Overlay, Word

GREEN, ORANGE, RED, GREY = (40, 170, 70), (230, 140, 20), (200, 40, 40), (110, 110, 118)

# --------------------------------------------------------------------------- friend
FRIEND_MARK = "The left weapon is yours"
FRIEND_SCRIPT = ("{game} fans only. Pick a friend to fight. Pause the video right now. The left weapon is yours. "
                 "The right one is your friend's. More damage wins. {mythic} Comment who won. "
                 "Then send this to that friend for a rematch.")
FRIEND_TITLE = "Pause To Fight Your Friend ⚔️"
FRIEND_END_CARD = "Comment who won"
FRIEND_PINNED = ("Who won, you or your friend? 👇 Left weapon = you, right = your friend ⚔️\n"
                 "Send this to the friend you fought for a rematch.{mythic_pin}")
FRIEND_DESCRIPTION = ("Pause the video: the left {game} weapon is yours, the right one is your friend's. More attack "
                      "damage wins. Comment who won and send it to that friend.")
FRIEND_HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#pausechallenge", "#1v1"]

# --------------------------------------------------------------------------- pet
PET_MARK = "The mob you stop on fights for you"
# name -> (tier, attack damage in Java Edition on Normal; 0 = it never attacks). Left out: the mobs whose damage
# is random or depends on the distance (skeletons, pillagers, creepers, ghasts, witches, hoglins).
ALLIES: dict[str, tuple[str, float]] = {
    "Chicken": ("common", 0), "Pig": ("common", 0), "Cow": ("common", 0), "Sheep": ("common", 0),
    "Rabbit": ("common", 0), "Villager": ("common", 0), "Bat": ("common", 0), "Parrot": ("common", 0),
    "Turtle": ("common", 0), "Squid": ("common", 0), "Snow Golem": ("common", 0),
    "Silverfish": ("common", 1), "Bee": ("common", 2), "Fox": ("common", 2), "Axolotl": ("common", 2),
    "Spider": ("rare", 2), "Endermite": ("rare", 2), "Zombie": ("rare", 3), "Husk": ("rare", 3),
    "Drowned": ("rare", 3), "Dolphin": ("rare", 3), "Wolf": ("rare", 4), "Slime": ("rare", 4),
    "Shulker": ("rare", 4),
    "Polar Bear": ("epic", 6), "Panda": ("epic", 6), "Magma Cube": ("epic", 6), "Phantom": ("epic", 6),
    "Guardian": ("epic", 6), "Enderman": ("epic", 7), "Piglin": ("epic", 8), "Elder Guardian": ("epic", 8),
    "Vex": ("epic", 9),
    "Ravager": ("legendary", 12), "Iron Golem": ("legendary", 14.5), "Ender Dragon": ("legendary", 10),
    "Wither": ("legendary", 8), "Warden": ("legendary", 30),
}
ABOUT = {"Iron Golem"}  # hits 7.5 to 21.5 at random: counted at the middle, and the card says "about"
# The bosses a pet Short can be against: name -> attack damage (their health and armour are spin's). Each leaves
# between one card in twenty and half of them a winner (tests/test_arena.py), so pausing again is worth it; against
# the ender dragon or the wither only a warden would win, so they are not here.
PET_BOSSES = {"Zombie": 3, "Spider": 2, "Enderman": 7, "Polar Bear": 6, "Piglin": 8, "Vex": 9, "Elder Guardian": 8,
              "Ravager": 12}
PET_SCRIPT = ("{game} fans only. {article} {boss} is coming for you. Pause the video right now. "
              "The mob you stop on fights for you. It hits first. Can it win? {mythic} Comment your mob. "
              "Then send this to a friend, and see whose mob wins.")
PET_TITLE = "Pause To Get Your Mob vs The {boss} 🐺"
PET_END_CARD = "Comment your mob"
PET_PINNED = ("Which mob did you get vs the {boss}? 👇 Did it win? 🐺\n"
              "Send this to a friend and see whose mob wins.{mythic_pin}")
PET_DESCRIPTION = ("Pause the video: the {game} mob you stop on fights the {boss} for you, hit for hit, your mob "
                   "first (Java Edition, Normal). Comment your mob and challenge a friend.")
PET_HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#pausechallenge", "#minecraftmobs"]

# --------------------------------------------------------------------------- double
DOUBLE_MARK = "Pause again"
START_HEARTS = 5
GIVES = {"Bread": 1, "Golden Carrot": 1, "Golden Apple": 2, "Enchanted Golden Apple": 3, "Totem of Undying": 3,
         "Rotten Flesh": -1}  # the quest's village chest (quest.Village.GIVES)
DOUBLE_SCRIPT = ("{game} fans only. You have five hearts. Pause now to open the chest. Food gives hearts back. "
                 "Now {article_low} {boss} attacks. Pause again. That weapon is yours. A slow weapon costs hearts. "
                 "{mythic} Comment your hearts. Then send this to a friend, and see who survives.")
DOUBLE_SWITCH_AFTER = 4  # the chest's cards until this many sentences are said, then the fight's
DOUBLE_TITLE = "Pause Twice To Beat The {boss} 🎁⚔️"
DOUBLE_END_CARD = "Comment your hearts"
DOUBLE_PINNED = ("How many hearts do you have left? 👇 You start with 5: chest first, then the fight ❤️\n"
                 "7 hearts is the perfect run. Send this to a friend and see who survives.{mythic_pin}")
DOUBLE_DESCRIPTION = ("Two pauses, five hearts: open the chest (food gives hearts back), then pause again for your "
                      "weapon against the {boss}. A slow weapon costs hearts. Comment how many you have left.")
DOUBLE_HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#pausechallenge", "#minecraftchallenge"]

assert FRIEND_MARK in FRIEND_SCRIPT and PET_MARK in PET_SCRIPT and DOUBLE_MARK in DOUBLE_SCRIPT
WANTED = {"friend": "fight your friend", "pet": "get your mob", "double": "pause twice"}  # the Create tab's choices


def wanted(notes: str = "") -> str:
    """The kind asked for in the Create tab's notes, "" when none."""
    low = (notes or "").lower()
    return next((kind for kind, phrase in WANTED.items() if phrase in low), "")


def kind_of(script_text: str) -> str:
    text = script_text or ""
    if FRIEND_MARK in text:
        return "friend"
    if PET_MARK in text:
        return "pet"
    return "double" if DOUBLE_MARK in text and "five hearts" in text else ""


def boss_of(script_text: str) -> str:
    """The boss (or the mob of the fight) the narration names."""
    m = re.search(r"(?:(?:A|An|The) ([A-Z][A-Za-z ]+?) is coming for you|Now (?:a|an|the) ([A-Z][A-Za-z ]+?) attacks)",
                  script_text or "")
    return (m.group(1) or m.group(2)) if m else ""


def ready(settings: Settings, kind: str) -> bool:
    from . import dungeon

    found = dungeon.pictures(settings)
    if kind == "friend":
        return len([w for w in spin.WEAPONS if w in found]) >= 4
    if kind == "pet":
        return bool(pet_bosses(found)) and len([a for a in ALLIES if a in found]) >= 4
    return bool(spin.weapon_catalog(settings)) and "Chest" in found and any(n in found for n in dungeon.LOOT)


def pet_bosses(found: dict) -> list[str]:
    return [b for b in PET_BOSSES if b in found]


def script(settings: Settings, game: str, kind: str, *, format: str = "short", rng: random.Random | None = None):
    """The Short's ShortScript: fixed narration, `spin` set, no answer (every viewer gets their own)."""
    from . import dungeon, quiz

    if not ready(settings, kind):
        raise ValueError("No pictures yet: make a Pause to Get Your Weapon Short and a Dungeon Run part first")
    rng = rng or random.Random()
    found = dungeon.pictures(settings)
    game_name = quiz.game_name(game)
    boss = ""
    if kind == "pet":
        boss = rng.choice(pet_bosses(found))
    elif kind == "double":
        boss = rng.choice([b["name"] for b in spin.weapon_catalog(settings)["bosses"]])
    words, title, description, hashtags, pinned, end_card, on_screen = {
        "friend": (FRIEND_SCRIPT, FRIEND_TITLE, FRIEND_DESCRIPTION, FRIEND_HASHTAGS, FRIEND_PINNED, FRIEND_END_CARD,
                   "Pause to fight your friend"),
        "pet": (PET_SCRIPT, PET_TITLE, PET_DESCRIPTION, PET_HASHTAGS, PET_PINNED, PET_END_CARD, "Pause to get your mob"),
        "double": (DOUBLE_SCRIPT, DOUBLE_TITLE, DOUBLE_DESCRIPTION, DOUBLE_HASHTAGS, DOUBLE_PINNED, DOUBLE_END_CARD,
                   "Pause twice"),
    }[kind]
    art = spin.article(boss) if boss else ""
    fill = {"game": game_name, "boss": boss, "article": art, "article_low": art.lower(), **spin.MYTHIC_FILL}
    card = "PAUSE TWICE!" if kind == "double" else spin.CARD
    pack = SimpleNamespace(title=title.format(**fill), description=description.format(**fill), hashtags=hashtags,
                           title_card=card)
    out = quiz._script(settings, game, "builtin", "gamer", format, words.format(**fill),
                       spin.HOOK.format(game=game_name), [], "", pinned.format(**fill), pack, on_screen=on_screen,
                       card=card, end_card=end_card)
    return out.model_copy(update={"spin": True, "reactions": []})


# --------------------------------------------------------------------------- the numbers


def _armoured(damage: float, armour: float) -> float:
    """`damage` after `armour` points, by the wiki's formula (no toughness), as spin.hits_to_kill."""
    return damage * (1 - min(20, max(armour / 5, armour - 4 * damage / 8)) / 25)


def stats(mob: str) -> tuple[float, float]:
    """(health, armour) in Java Edition."""
    return {**spin.FOES, **spin.BOSSES}[mob]


def pet_fight(boss: str, mob: str) -> tuple[int | None, int]:
    """(hits your mob needs to beat the boss, or None when it never attacks; hits the boss needs to beat it)."""
    health, armour = stats(boss)
    mine = ALLIES[mob][1]
    my_health, my_armour = stats(mob)
    theirs = math.ceil(my_health / _armoured(PET_BOSSES[boss], my_armour) - 1e-9)
    return (math.ceil(health / _armoured(mine, armour) - 1e-9) if mine else None), theirs


def pet_verdict(boss: str, mob: str) -> tuple[str, tuple[int, int, int]]:
    mine, theirs = pet_fight(boss, mob)
    if mine is None:
        return "CAN'T ATTACK: YOU LOSE", RED
    about = "ABOUT " if mob in ABOUT else ""
    word = "HIT" if mine == 1 else "HITS"
    if mine <= theirs:  # your mob hits first
        return f"YOU WIN IN {about}{mine} {word}!", GREEN
    return f"YOU LOSE: NEEDS {about}{mine} {word}", RED


def damage_text(value: float) -> str:
    return "7-21" if value == 14.5 else f"{value:g}"


def friend_verdict(mine: str, theirs: str) -> tuple[str, tuple[int, int, int]]:
    a, b = spin.WEAPONS[mine][1], spin.WEAPONS[theirs][1]
    if a == b:
        return f"DRAW! {a:g} VS {b:g} - PAUSE AGAIN", GREY
    return (f"YOU WIN! {a:g} VS {b:g} DAMAGE", GREEN) if a > b else (f"FRIEND WINS! {b:g} VS {a:g} DAMAGE", RED)


def loot_verdict(item: str) -> tuple[str, tuple[int, int, int]]:
    gives = GIVES.get(item, 0)
    if gives > 0:
        return f"+{gives} HEART{'S' if gives > 1 else ''}", GREEN
    return ("-1 HEART: YUCK!", RED) if gives < 0 else ("NOT FOOD: NO HEARTS", GREY)


def hearts_lost(boss: str, weapon: str) -> int:
    """One heart for a fast kill, two for a slow one, three for the slowest (the weapon card's green, orange and
    red: up to 1.25 times the best weapon's hits, up to twice, more; an arrow that can't hurt it is the slowest)."""
    hits = spin.hits_to_kill(boss, weapon)
    best = min(spin.hits_to_kill(boss, w) or 10 ** 6 for w in spin.WEAPONS)
    return 3 if hits is None or hits > best * 2 else 2 if hits > best * 1.25 else 1


def fight_verdict(boss: str, weapon: str) -> tuple[str, tuple[int, int, int]]:
    hits, lost = spin.hits_to_kill(boss, weapon), hearts_lost(boss, weapon)
    colour = {1: GREEN, 2: ORANGE, 3: RED}[lost]
    lose = f"LOSE {lost} HEART{'S' if lost > 1 else ''}"
    if hits is None:
        return f"{spin.ARROW_PROOF[boss]}: {lose}", colour
    return f"{hits} {'SHOT' if weapon == 'Bow' else 'HIT'}{'S' if hits != 1 else ''}: {lose}", colour


# --------------------------------------------------------------------------- the decks and the cards


def _by_tier(names: list[str], tier_of) -> list[float]:
    per = {t: sum(tier_of(n) == t for n in names) for t in spin.TIERS}
    return [spin.TIERS[tier_of(n)][1] / per[tier_of(n)] for n in names]


def _deck(names: list[str], tier_of, count: int, rng: random.Random) -> list[str]:
    """`count` names: each tier its share of the cards, never the same one twice in a row."""
    weights = _by_tier(names, tier_of)
    out: list[str] = []
    for _ in range(count):
        while True:
            pick = rng.choices(names, weights)[0]
            if not out or pick != out[-1]:
                break
        out.append(pick)
    return out


def friend_card(mine: dict, theirs: dict, cache: dict):
    """Your weapon on the left, your friend's on the right, each under its rarity; who wins across the bottom."""
    text, bar = friend_verdict(mine["name"], theirs["name"])
    left_tier, right_tier = spin.WEAPONS[mine["name"]][0], spin.WEAPONS[theirs["name"]][0]
    return spin.versus_card(mine, f"YOU: {left_tier.upper()}", theirs, right_tier, text, bar, cache, right_size=288,
                            left_colour=spin.TIERS[left_tier][0], right_label=f"FRIEND: {right_tier.upper()}",
                            left_size=288)


def pet_card(boss: dict, mob: dict, cache: dict):
    name = boss["name"]
    text, bar = pet_verdict(name, mob["name"])
    note = f"{stats(name)[0]:g} HEALTH · {damage_text(PET_BOSSES[name])} DAMAGE"
    return spin.stack_card(f"THE BOSS: {name.upper()}", boss, note, "YOUR MOB", ALLIES[mob["name"]][0], mob, text,
                           bar, cache)


def chest_card(chest: dict, item: dict, cache: dict):
    from . import dungeon

    text, bar = loot_verdict(item["name"])
    return spin.stack_card("PAUSE 1 OF 2: THE CHEST", chest, f"YOU HAVE {START_HEARTS} HEARTS", "YOUR LOOT",
                           dungeon.LOOT[item["name"]], item, text, bar, cache)


def fight_card(boss: dict, weapon: dict, cache: dict):
    name = boss["name"]
    text, bar = fight_verdict(name, weapon["name"])
    return spin.stack_card(f"PAUSE 2 OF 2: {name.upper()}", boss, f"{spin.health_of(name)} HEALTH", "YOUR WEAPON",
                           spin.WEAPONS[weapon["name"]][0], weapon, text, bar, cache)


def _switch(words: list[Word], total: float) -> float:
    """When the chest's cards give way to the fight's: at the first word of the "Now a ... attacks" sentence."""
    ends = [k for k, w in enumerate(words) if w.text.rstrip().endswith((".", "!", "?"))]
    if len(ends) >= DOUBLE_SWITCH_AFTER and ends[DOUBLE_SWITCH_AFTER - 1] + 1 < len(words):
        return round(words[ends[DOUBLE_SWITCH_AFTER - 1] + 1].start, 3)
    return round(total * 0.42, 3)


def _save(cards: list, path, fps: int) -> None:
    cards[0].save(path, save_all=True, append_images=cards[1:], duration=round(1000 * spin.HOLD / fps), loop=0,
                  lossless=False, quality=90)


def plan(settings: Settings, words: list[Word], script_text: str, kind: str, seed: str = "", tail: float = 2.0,
         fps: int = 30) -> list[Overlay]:
    """The Short's flicking cards: one animated WebP for the whole video, two for the double's two pauses."""
    from . import dungeon

    spoken = script_text or " ".join(w.text for w in words)
    found = dungeon.pictures(settings)
    rng = random.Random(seed or kind)
    total = words[-1].end + tail
    count = int(total * fps / spin.HOLD) + 2
    out_dir = settings.assets_dir / "cache" / "spin"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-z0-9]+", "-", (seed or kind).lower())[:40].strip("-")
    cache: dict = {}
    weapons = [w for w in spin.WEAPONS if w in found]
    weapon_tier = lambda n: spin.WEAPONS[n][0]  # noqa: E731
    end = round(total + 0.5, 3)
    if kind == "friend":
        left, right = _deck(weapons, weapon_tier, count, rng), _deck(weapons, weapon_tier, count, rng)
        cards = [friend_card(found[a], found[b], cache) for a, b in zip(left, right)]
        spin.add_mythic(settings, cards, spoken, seed or kind, set(weapons), fps=fps)
        path = out_dir / f"{stem}.webp"
        _save(cards, path, fps)
        return [Overlay(path, 0.0, end, center=spin.CENTER, box=max(cards[0].size), style="asis", cut=True,
                        animated=True)]
    box = max(spin.BW, spin.BH)
    boss = boss_of(spoken)
    if kind == "pet":
        boss = boss if boss in PET_BOSSES and boss in found else pet_bosses(found)[0]
        allies = [a for a in ALLIES if a in found and a != boss]
        cards = [pet_card(found[boss], found[m], cache) for m in _deck(allies, lambda n: ALLIES[n][0], count, rng)]
        spin.add_mythic(settings, cards, spoken, seed or kind, set(allies) | {boss}, fps=fps)
        path = out_dir / f"{stem}.webp"
        _save(cards, path, fps)
        return [Overlay(path, 0.0, end, center=spin.BOSS_CENTER, box=box, style="asis", cut=True, animated=True)]
    # double: the chest, then the fight
    boss = boss if boss in found and boss in {**spin.FOES, **spin.BOSSES} else "Zombie"
    switch = _switch(words, total)
    loot = [n for n in dungeon.LOOT if n in found]
    first = [chest_card(found["Chest"], found[n], cache)
             for n in _deck(loot, lambda n: dungeon.LOOT[n], int(switch * fps / spin.HOLD) + 2, rng)]
    second = [fight_card(found[boss], found[n], cache)
              for n in _deck(weapons, weapon_tier, int((total - switch) * fps / spin.HOLD) + 2, rng)]
    spin.add_mythic(settings, second, spoken, seed or kind, set(weapons) | set(loot) | {boss}, fps=fps, start=switch)
    paths = out_dir / f"{stem}-chest.webp", out_dir / f"{stem}.webp"
    _save(first, paths[0], fps)
    _save(second, paths[1], fps)
    return [Overlay(paths[0], 0.0, switch, center=spin.BOSS_CENTER, box=box, style="asis", cut=True, animated=True),
            Overlay(paths[1], switch, end, center=spin.BOSS_CENTER, box=box, style="asis", cut=True, animated=True)]


def win_share(boss: str) -> float:
    """The share of a pet Short's cards on which the viewer's mob beats `boss`."""
    names = [a for a in ALLIES if a != boss]
    weights = _by_tier(names, lambda n: ALLIES[n][0])
    won = sum(w for n, w in zip(names, weights) if (pet_fight(boss, n)[0] or 10 ** 9) <= pet_fight(boss, n)[1])
    return won / sum(weights)
