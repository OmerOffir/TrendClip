"""Game detection: map trending videos to the game they are about, then rank games.

The YouTube Data API does not expose a video's game, so we match titles and tags against a
curated catalog. Aliases are matched as whole words on normalized text (accents, case and
punctuation removed), plus a no-space form so hashtags like #MarvelRivals also match.

Avoid aliases that are common English words ("peak", "wow", "lol", "control") - they
produce false positives. Videos that match nothing still surface as emerging topics.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import GameTrend, YouTubeVideo

MAX_TAGS = 15
TOP_VIDEOS_PER_GAME = 5
TOP_CHANNELS_PER_GAME = 5


@dataclass(frozen=True)
class Game:
    name: str
    aliases: tuple[str, ...]
    franchise: str | None = None  # a more specific entry wins over its franchise


def _g(name: str, *aliases: str, franchise: str | None = None) -> Game:
    return Game(name, aliases, franchise)


CATALOG: tuple[Game, ...] = (
    # Battle royale / shooters
    _g("Fortnite", "fortnite", "fortnitemares", "fortnite og", "fortnite festival"),
    _g("Call of Duty", "call of duty", "cod", "callofduty"),
    _g("Black Ops 7", "black ops 7", "bo7", "blackops7", franchise="Call of Duty"),
    _g("Black Ops 6", "black ops 6", "bo6", "blackops6", franchise="Call of Duty"),
    _g("Warzone", "warzone", franchise="Call of Duty"),
    _g("Call of Duty: Mobile", "cod mobile", "codm", "call of duty mobile", franchise="Call of Duty"),
    _g("Battlefield", "battlefield", "redsec"),
    _g("Battlefield 6", "battlefield 6", "bf6", franchise="Battlefield"),
    _g("Apex Legends", "apex legends", "apex"),
    _g("PUBG", "pubg", "pubg mobile", "bgmi", "battlegrounds mobile"),
    _g("Free Fire", "free fire", "freefire", "garena free fire"),
    _g("Valorant", "valorant"),
    _g("Counter-Strike 2", "counter strike", "cs2", "csgo", "cs go"),
    _g("Overwatch 2", "overwatch", "ow2"),
    _g("Rainbow Six Siege", "rainbow six", "r6", "r6 siege", "siege x"),
    _g("Marvel Rivals", "marvel rivals"),
    _g("Halo", "halo", "halo infinite"),
    _g("Gears of War", "gears of war", "gears 5", "gears of war e day", "gears of war reloaded"),
    _g("Destiny 2", "destiny 2"),
    _g("Helldivers 2", "helldivers", "helldivers 2"),
    _g("Arc Raiders", "arc raiders"),
    _g("Escape from Tarkov", "tarkov", "escape from tarkov"),
    _g("Delta Force", "delta force"),
    _g("Deadlock", "deadlock"),
    _g("Doom", "doom", "doom the dark ages", "doom eternal"),
    _g("Borderlands", "borderlands", "borderlands 4"),
    _g("Warframe", "warframe"),
    # MOBA / strategy / competitive
    _g("League of Legends", "league of legends"),
    _g("Teamfight Tactics", "teamfight tactics", "tft"),
    _g("Dota 2", "dota", "dota 2"),
    _g("Mobile Legends", "mobile legends", "mlbb"),
    _g("Rocket League", "rocket league"),
    _g("StarCraft", "starcraft"),
    # Sandbox / survival / crafting
    _g("Minecraft", "minecraft"),
    _g("Hytale", "hytale"),
    _g("Terraria", "terraria"),
    _g("Rust", "rust"),
    _g("DayZ", "dayz"),
    _g("ARK", "ark survival", "ark ascended", "ark survival ascended"),
    _g("Palworld", "palworld"),
    _g("Subnautica", "subnautica", "subnautica 2"),
    _g("Grounded 2", "grounded 2"),
    _g("Stardew Valley", "stardew", "stardew valley"),
    _g("Sea of Thieves", "sea of thieves"),
    _g("Schedule I", "schedule 1", "schedule i"),
    # Roblox and its biggest experiences
    _g("Roblox", "roblox"),
    _g("Grow a Garden", "grow a garden", franchise="Roblox"),
    _g("Steal a Brainrot", "steal a brainrot", franchise="Roblox"),
    _g("99 Nights in the Forest", "99 nights in the forest", "99 nights", franchise="Roblox"),
    _g("Blox Fruits", "blox fruits", "bloxfruits", franchise="Roblox"),
    _g("Brookhaven", "brookhaven", franchise="Roblox"),
    _g("Adopt Me", "adopt me", franchise="Roblox"),
    _g("Doors (Roblox)", "roblox doors", franchise="Roblox"),
    _g("Dead Rails", "dead rails", franchise="Roblox"),
    _g("Forsaken (Roblox)", "roblox forsaken", franchise="Roblox"),
    _g("Murder Mystery 2", "murder mystery 2", "mm2", franchise="Roblox"),
    _g("Pet Simulator", "pet simulator", "pet sim 99", franchise="Roblox"),
    _g("Blade Ball", "blade ball", franchise="Roblox"),
    _g("Dress to Impress", "dress to impress", "dti", franchise="Roblox"),
    _g("Da Hood", "da hood", franchise="Roblox"),
    _g("Bee Swarm Simulator", "bee swarm", "bee swarm simulator", franchise="Roblox"),
    # Party / social / indie hits
    _g("Among Us", "among us"),
    _g("Fall Guys", "fall guys"),
    _g("Lethal Company", "lethal company"),
    _g("R.E.P.O.", "r e p o"),
    _g("Phasmophobia", "phasmophobia", "phasmo"),
    _g("Dead by Daylight", "dead by daylight", "dbd"),
    _g("Five Nights at Freddy's", "fnaf", "five nights at freddys"),
    _g("Poppy Playtime", "poppy playtime"),
    _g("Garten of Banban", "garten of banban", "banban"),
    _g("Geometry Dash", "geometry dash"),
    _g("Hollow Knight", "hollow knight", "silksong", "hollow knight silksong"),
    _g("Hades", "hades", "hades 2", "hades ii"),
    _g("Cuphead", "cuphead"),
    _g("Undertale / Deltarune", "undertale", "deltarune"),
    _g("Bloons TD", "bloons", "btd6"),
    _g("Plants vs. Zombies", "plants vs zombies", "pvz"),
    # Open world / action / RPG
    _g("Grand Theft Auto", "gta", "grand theft auto"),
    _g("GTA VI", "gta 6", "gta vi", "gta6", "grand theft auto vi", "grand theft auto 6", franchise="Grand Theft Auto"),
    _g("GTA V / Online", "gta 5", "gta v", "gtav", "gta5", "gta online", "gta rp", "fivem", franchise="Grand Theft Auto"),
    _g("Red Dead Redemption", "red dead", "red dead redemption", "rdr2", "red dead online"),
    _g("Cyberpunk 2077", "cyberpunk", "cyberpunk 2077"),
    _g("The Witcher", "witcher", "the witcher 3", "witcher 4"),
    _g("The Elder Scrolls", "skyrim", "elder scrolls", "oblivion", "oblivion remastered"),
    _g("Fallout", "fallout", "fallout 4", "fallout 76", "fallout new vegas"),
    _g("Starfield", "starfield"),
    _g("Baldur's Gate 3", "baldurs gate", "baldurs gate 3", "bg3"),
    _g("Elden Ring", "elden ring", "nightreign", "elden ring nightreign"),
    _g("Dark Souls", "dark souls"),
    _g("Sekiro", "sekiro"),
    _g("Bloodborne", "bloodborne"),
    _g("Black Myth: Wukong", "black myth wukong", "wukong"),
    _g("Hogwarts Legacy", "hogwarts legacy"),
    _g("Assassin's Creed", "assassins creed", "ac shadows"),
    _g("Ghost of Yotei", "ghost of yotei"),
    _g("Ghost of Tsushima", "ghost of tsushima"),
    _g("The Last of Us", "the last of us", "tlou"),
    _g("God of War", "god of war"),
    _g("Marvel's Spider-Man", "spider man 2", "spiderman 2", "marvels spider man"),
    _g("Death Stranding", "death stranding", "death stranding 2"),
    _g("Metal Gear Solid", "metal gear", "metal gear solid", "mgs delta"),
    _g("Monster Hunter", "monster hunter", "monster hunter wilds", "mh wilds"),
    _g("Clair Obscur: Expedition 33", "clair obscur", "expedition 33"),
    _g("Split Fiction", "split fiction"),
    _g("Kingdom Come: Deliverance", "kingdom come deliverance", "kcd2"),
    _g("S.T.A.L.K.E.R. 2", "stalker 2"),
    _g("Dying Light", "dying light", "dying light the beast"),
    _g("Mafia", "mafia the old country"),
    _g("Final Fantasy", "final fantasy", "ff7", "ff7 rebirth", "ff14", "ffxiv"),
    _g("Persona", "persona 5", "persona 3 reload"),
    _g("Kingdom Hearts", "kingdom hearts"),
    _g("Dragon Ball: Sparking! Zero", "sparking zero", "dragon ball sparking"),
    _g("Resident Evil", "resident evil", "re4", "resident evil requiem"),
    _g("Silent Hill", "silent hill", "silent hill f"),
    # Gacha / mobile
    _g("Genshin Impact", "genshin", "genshin impact"),
    _g("Honkai: Star Rail", "honkai star rail", "star rail", "hsr"),
    _g("Zenless Zone Zero", "zenless zone zero"),
    _g("Wuthering Waves", "wuthering waves", "wuwa"),
    _g("Brawl Stars", "brawl stars"),
    _g("Clash Royale", "clash royale"),
    _g("Clash of Clans", "clash of clans"),
    _g("Squad Busters", "squad busters"),
    _g("Subway Surfers", "subway surfers"),
    _g("Indian Bike Driving 3D", "indian bike driving", "indian bike driving 3d"),
    # Nintendo
    _g("Mario", "mario", "super mario", "mario party"),
    _g("Mario Kart", "mario kart", "mario kart world", franchise="Mario"),
    _g("The Legend of Zelda", "zelda", "tears of the kingdom", "breath of the wild", "totk", "botw"),
    _g("Pokemon", "pokemon"),
    _g("Pokemon Legends: Z-A", "legends z a", "pokemon legends z a", "legends za", "pokemon za", franchise="Pokemon"),
    _g("Pokemon GO", "pokemon go", franchise="Pokemon"),
    _g("Pokemon TCG Pocket", "tcg pocket", "pokemon pocket", franchise="Pokemon"),
    _g("Super Smash Bros.", "smash bros", "smash ultimate"),
    _g("Animal Crossing", "animal crossing"),
    _g("Splatoon", "splatoon"),
    _g("Donkey Kong", "donkey kong", "donkey kong bananza", "dk bananza"),
    _g("Kirby", "kirby"),
    _g("Metroid", "metroid", "metroid prime 4"),
    _g("Sonic", "sonic", "sonic the hedgehog"),
    # Racing / sports / fighting
    _g("EA Sports FC", "ea fc", "eafc", "fc 26", "fc26", "fc 25", "fc25", "fifa", "ultimate team"),
    _g("NBA 2K", "nba 2k", "nba 2k26", "2k26", "nba 2k25", "2k25"),
    _g("Madden NFL", "madden", "madden 26"),
    _g("College Football 26", "college football 26", "cfb 26", "cfb26"),
    _g("WWE 2K", "wwe 2k", "wwe 2k25", "wwe 2k26"),
    _g("F1 25", "f1 25", "f1 24"),
    _g("Forza", "forza", "forza horizon", "forza horizon 5", "forza horizon 6", "forza motorsport"),
    _g("Gran Turismo", "gran turismo", "gt7"),
    _g("Mortal Kombat", "mortal kombat", "mk1"),
    _g("Street Fighter 6", "street fighter", "sf6"),
    _g("Tekken 8", "tekken", "tekken 8"),
    # Blizzard
    _g("World of Warcraft", "world of warcraft", "warcraft", "wow classic"),
    _g("Diablo", "diablo", "diablo 4", "diablo iv"),
    _g("Hearthstone", "hearthstone"),
    _g("Path of Exile", "path of exile", "poe 2", "poe2"),
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_APOSTROPHES = re.compile(r"['\u2019`]")


def normalize(text: str) -> str:
    """'Pokémon: Legends Z-A!' -> ' pokemon legends z a ' (padded for whole-word matching)."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = _APOSTROPHES.sub("", text)
    return f" {_NON_ALNUM.sub(' ', text).strip()} "


@dataclass(frozen=True)
class _Pattern:
    needle: str  # padded, e.g. " gta 6 "
    game: Game


def _compile(catalog: tuple[Game, ...]) -> list[_Pattern]:
    patterns: list[_Pattern] = []
    for game in catalog:
        forms: set[str] = set()
        for alias in (game.name, *game.aliases):
            norm = normalize(alias).strip()
            if not norm:
                continue
            forms.add(norm)
            joined = norm.replace(" ", "")
            if " " in norm and len(joined) >= 4:
                forms.add(joined)  # hashtag form: #GTA6, #MarvelRivals
        patterns.extend(_Pattern(f" {form} ", game) for form in forms)
    # Longer needles first so "gta 6" is seen before "gta" at the same position.
    patterns.sort(key=lambda p: len(p.needle), reverse=True)
    return patterns


_PATTERNS = _compile(CATALOG)


def _drop_franchises(found: dict[str, int]) -> dict[str, int]:
    """If both a franchise and a specific entry matched, keep only the specific one."""
    by_name = {g.name: g for g in CATALOG}
    parents = {by_name[name].franchise for name in found if by_name[name].franchise}
    return {name: pos for name, pos in found.items() if name not in parents}


def _match_text(text: str) -> dict[str, int]:
    """game name -> earliest match position in text."""
    found: dict[str, int] = {}
    for pattern in _PATTERNS:
        pos = text.find(pattern.needle)
        if pos >= 0 and (pattern.game.name not in found or pos < found[pattern.game.name]):
            found[pattern.game.name] = pos
    return found


def match_title(title: str) -> list[str]:
    """Games named in a bare title, in order of appearance (franchise dropped if a child matched)."""
    hits = _drop_franchises(_match_text(normalize(title)))
    return sorted(hits, key=lambda n: hits[n])


def related_games(name: str) -> set[str]:
    """The game, its franchise, and the franchise's other entries (GTA VI -> all GTA titles)."""
    by_name = {g.name: g for g in CATALOG}
    game = by_name.get(name)
    if game is None:
        return set()
    root = game.franchise or game.name
    return {root} | {g.name for g in CATALOG if g.franchise == root}


def detect_games(video: YouTubeVideo) -> list[str]:
    """Games a video is about, primary first. Title matches outrank tag-only matches."""
    title_hits = _drop_franchises(_match_text(normalize(video.title)))
    tag_counts: dict[str, int] = {}
    tag_first: dict[str, int] = {}
    for i, tag in enumerate(video.tags[:MAX_TAGS]):
        for name in _match_text(normalize(tag)):
            tag_counts[name] = tag_counts.get(name, 0) + 1
            tag_first.setdefault(name, i)
    tag_hits = _drop_franchises(tag_counts)

    ordered = sorted(title_hits, key=lambda n: title_hits[n])
    if not ordered:
        ordered = sorted(tag_hits, key=lambda n: (-tag_hits[n], tag_first[n]))
    else:
        ordered += [n for n in sorted(tag_hits, key=lambda n: tag_first[n]) if n not in title_hits]
    # A franchise matched in tags is redundant if its specific entry matched in the title.
    by_name = {g.name: g for g in CATALOG}
    specific_parents = {by_name[n].franchise for n in ordered if by_name[n].franchise}
    return [n for n in ordered if n not in specific_parents]


def summarize_games(videos: list[YouTubeVideo]) -> list[GameTrend]:
    """Tag each video with its game and build a leaderboard ranked by summed velocity."""
    franchise_of = {g.name: g.franchise for g in CATALOG}
    total_vph = sum(v.views_per_hour for v in videos) or 1.0
    groups: dict[str, list[YouTubeVideo]] = {}

    for video in videos:
        video.games = detect_games(video)
        video.game = video.games[0] if video.games else None
        if video.game:
            groups.setdefault(video.game, []).append(video)

    trends: list[GameTrend] = []
    for name, vids in groups.items():
        vids.sort(key=lambda v: v.velocity_score, reverse=True)
        channel_views: dict[str, int] = {}
        for v in vids:
            channel_views[v.channel_title] = channel_views.get(v.channel_title, 0) + v.views
        vph = sum(v.views_per_hour for v in vids)
        trends.append(
            GameTrend(
                name=name,
                franchise=franchise_of.get(name),
                score=round(sum(v.velocity_score for v in vids), 3),
                video_count=len(vids),
                outlier_count=sum(1 for v in vids if v.is_outlier),
                total_views=sum(v.views for v in vids),
                views_per_hour=round(vph, 1),
                view_share=round(vph / total_vph, 4),
                regions=sorted({r for v in vids for r in v.regions}),
                channels=sorted(channel_views, key=channel_views.get, reverse=True)[:TOP_CHANNELS_PER_GAME],
                videos=vids[:TOP_VIDEOS_PER_GAME],
            )
        )
    trends.sort(key=lambda t: t.score, reverse=True)
    return trends
