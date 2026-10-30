"""Borderline Why's numbered series: a Short that fits a series ("Weird Borders") goes out as its next episode,
"Why is Gambia inside Senegal? | Weird Borders #7".

Which Shorts belong: a topic carries its series (the seeds are tagged by hand, Gemini's and your LLM's proposals
say it, a topic added by hand is judged by its words); bw_script.finish copies it into the script and make()
into info.json ("series").

The number is given at the upload, not when the video is made: a video that fails its check, is stopped with
"Don't post this" or is made again never reaches YouTube, and numbering it at make time would leave gaps
(#6, #8) viewers notice. upload() reserves the next number for the video's key (a retry keeps it), puts it in
the title, and confirms it once YouTube took the video; a failed upload gives the number back. The counter is
output/borderlinewhy/series.json (edit "start" there to continue from an older count).

After the upload the channel posts COMMENT under the video; the API can't pin, so Discord asks the owner to.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

WEIRD_BORDERS = "Weird Borders"
SERIES: dict[str, str] = {
    WEIRD_BORDERS: "an odd border, enclave, exclave, panhandle, strip, cut-off piece of land, disputed or unclaimed "
                   "patch, or a line on the map that splits a place in a strange way",
}
COMMENT = "Which weird border should I cover next?"
DEFAULT_EVERY = 2  # at least one Short in this many is a series episode, when the topic list has one (BW_SERIES_EVERY)
TITLE_ROOM = 92  # publish._yt_title adds " #shorts" to a Short's title (100 characters at most)

# The seed topics (bw_topics.SEEDS) that are Weird Borders episodes, by Wikipedia article; the other seeds are not.
SEED_ARTICLES = {
    "Northwest Angle", "Llívia", "Baarle-Hertog", "Pheasant Island", "Point Roberts, Washington", "Kaliningrad Oblast",
    "The Gambia", "Lesotho", "Caprivi Strip", "Kentucky Bend", "Märket", "Bir Tawil", "Ceuta", "Büsingen am Hochrhein",
    "Campione d'Italia", "Lake Constance", "India–Bangladesh enclaves", "War of the Pacific", "Oklahoma Panhandle",
    "Toledo War", "Ellis Island", "San Marino", "Lateran Treaty", "Diomede Islands", "Hans Island",
    "Territorial claims in Antarctica", "Mason–Dixon line", "Gibraltar", "Pig War", "Treaty of Aranjuez (1777)",
}
# A topic with no series of its own (added by hand) is a Weird Borders episode when its question says so.
_BORDER_WORDS = re.compile(
    r"\b(borders?|enclaves?|exclaves?|panhandle|strip|surrounded|cut off|landlocked|bend|notch|corridor|"
    r"no country|split between|divided between|half in|(?:sits?|lies|is) (?:completely |entirely )?inside|"
    r"only (?:drive|reach|get) to|can't reach|piece of land|swap(?:ped)? .*land|own(?:s|ed)? (?:a |two |an )?.*(?:in|inside) )",
    re.I)

_lock = threading.Lock()


def normalize(name: str | None) -> str:
    """A series name as SERIES spells it, or "" (no series)."""
    wanted = (name or "").strip().lower()
    return next((s for s in SERIES if s.lower() == wanted), "")


def classify(question: str, article: str = "", tag: str | None = None) -> str:
    """The series a topic belongs to: its own tag when it has one ("" = none), else the seed list, else its words."""
    if tag is not None:
        return normalize(tag)
    if article in SEED_ARTICLES:
        return WEIRD_BORDERS
    return WEIRD_BORDERS if _BORDER_WORDS.search(question or "") else ""


def every() -> int:
    from modules.scene_images import setting

    try:
        value = int(setting("BW_SERIES_EVERY", str(DEFAULT_EVERY)))
    except ValueError:
        return DEFAULT_EVERY
    return value if value >= 1 else DEFAULT_EVERY


def due(recent: list[str]) -> str:
    """The series the next topic should come from ("" = any): when none of the last every()-1 Shorts made
    (newest first) was an episode. every()=1 means always."""
    window = every() - 1
    if any(recent[:window]):
        return ""
    return WEIRD_BORDERS


# --------------------------------------------------------------------------- the counter


def path(out_dir: Path) -> Path:
    return out_dir / "series.json"


def load(out_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads(path(out_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any], out_dir: Path) -> None:
    file = path(out_dir)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(file)


def _entry(data: dict[str, Any], series: str) -> dict[str, Any]:
    entry = data.setdefault(series, {})
    entry.setdefault("start", 0)  # the number before #1 (a count kept from older videos)
    entry.setdefault("episodes", {})
    return entry


def _top(entry: dict[str, Any]) -> int:
    return max([int(entry.get("start") or 0), *(int(e["number"]) for e in entry["episodes"].values())])


def peek(out_dir: Path, series: str) -> int:
    """The number the next episode of `series` would get."""
    with _lock:
        return _top(_entry(load(out_dir), series)) + 1


def number_of(out_dir: Path, series: str, key: str) -> int | None:
    found = (load(out_dir).get(series) or {}).get("episodes", {}).get(key)
    return int(found["number"]) if found else None


def reserve(out_dir: Path, series: str, key: str) -> int:
    """The video's episode number: the one it already holds (a retried upload), else the next one."""
    with _lock:
        data = load(out_dir)
        entry = _entry(data, series)
        if key in entry["episodes"]:
            return int(entry["episodes"][key]["number"])
        number = _top(entry) + 1
        entry["episodes"][key] = {"number": number, "status": "reserved",
                                  "at": datetime.now().replace(microsecond=0).isoformat()}
        _save(data, out_dir)
        return number


def release(out_dir: Path, series: str, key: str) -> None:
    """A failed upload gives its number back, when no later episode took the next one (else it keeps it for the retry)."""
    with _lock:
        data = load(out_dir)
        entry = _entry(data, series)
        found = entry["episodes"].get(key)
        if found and found.get("status") == "reserved" and int(found["number"]) == _top(entry):
            del entry["episodes"][key]
            _save(data, out_dir)


def confirm(out_dir: Path, series: str, key: str, video_id: str) -> None:
    with _lock:
        data = load(out_dir)
        found = _entry(data, series)["episodes"].get(key)
        if found:
            found.update(status="uploaded", video_id=video_id, at=datetime.now().replace(microsecond=0).isoformat())
            _save(data, out_dir)


def summary(out_dir: Path) -> list[dict[str, Any]]:
    """Each series with its last uploaded number and the next one (the dashboard's status)."""
    data = load(out_dir)
    out = []
    for name in SERIES:
        entry = _entry(data, name)
        uploaded = [int(e["number"]) for e in entry["episodes"].values() if e.get("status") == "uploaded"]
        out.append({"series": name, "last": max(uploaded, default=int(entry["start"])) or None,
                    "next": _top(entry) + 1, "episodes": len(uploaded)})
    return out


# --------------------------------------------------------------------------- titles


def label(series: str, number: int) -> str:
    return f"{series} #{number}"


def strip(title: str) -> str:
    """The title without a series tag an LLM may have added ("... | Weird Borders #3", "Weird Borders: ...")."""
    for name in SERIES:
        title = re.sub(rf"\s*[|:\-–—]?\s*{re.escape(name)}\s*(?:#|ep\.?\s*|episode\s*)?\d*\s*[|:\-–—]?\s*", " ", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip(" |:-–—")


def titled(title: str, series: str, number: int) -> str:
    """"<title> | Weird Borders #7", the title cut (at a word) so the whole fits a Short's title."""
    tag = f" | {label(series, number)}"
    base = strip(title)
    room = TITLE_ROOM - len(tag)
    if len(base) > room:
        base = base[:room].rsplit(" ", 1)[0].rstrip(" ,;:-") or base[:room]
    return base + tag


# --------------------------------------------------------------------------- the comment


def comment_text() -> str:
    from modules.scene_images import setting

    return setting("BW_COMMENT", COMMENT).strip() or COMMENT


def post_comment(youtube, video_id: str, text: str) -> dict[str, Any]:
    """Post `text` under the video as the channel (commentThreads.insert). The API can't pin it."""
    body = {"snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text.strip()}}}}
    response = youtube.commentThreads().insert(part="snippet", body=body).execute()
    cid = (response or {}).get("id", "")
    return {"comment_id": cid, "text": text.strip(), "posted_at": datetime.now().replace(microsecond=0).isoformat(),
            "url": f"https://www.youtube.com/watch?v={video_id}&lc={cid}"}
