"""Dark Outliers content bank: raw stories from Reddit, Wikipedia, Gemini and RSS feeds in a local SQLite file.

`source_id` is UNIQUE (reddit_<post id>, wikipedia_<page id>, gemini_<slug>, rss_<feed>_<slug>), so a
story that was fetched before is never saved twice and never told in two different videos.

Every story belongs to a content pillar; get_next_story() keeps the scripted videos at the target mix
(PILLARS): it picks from the pillar furthest below its share, then the best score inside it.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "content_bank.db"
STATUSES = ("NEW", "PROCESSING", "SCRIPT_READY", "USED", "REJECTED")
PILLARS = {"mystery": 0.4, "history": 0.4, "outrage": 0.2}  # target share of the videos
PILLAR_LABELS = {"mystery": "Unresolved mysteries & cold cases", "history": "Creepy history & obscure anomalies",
                 "outrage": "Disturbing true events & outrage cases"}
PRODUCED = ("SCRIPT_READY", "USED")  # stories that count towards the mix

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT UNIQUE NOT NULL,
    source_name TEXT,
    title TEXT,
    raw_text TEXT,
    url TEXT,
    score INTEGER,
    status TEXT DEFAULT 'NEW',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pillar TEXT,
    note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_raw_stories_status_score ON raw_stories (status, score DESC);
"""
ADDED_COLUMNS = {"pillar": "TEXT", "note": "TEXT DEFAULT ''"}  # banks created before these existed


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str | None = None) -> None:
    with closing(_connect(db_path)) as conn, conn:
        conn.executescript(SCHEMA)
        have = {r[1] for r in conn.execute("PRAGMA table_info(raw_stories)")}
        for name, kind in ADDED_COLUMNS.items():
            if name not in have:
                conn.execute(f"ALTER TABLE raw_stories ADD COLUMN {name} {kind}")


def _pillar(pillar: str | None) -> str | None:
    if pillar is not None and pillar not in PILLARS:
        raise ValueError(f"pillar must be one of {', '.join(PILLARS)}")
    return pillar


def save_story(source_id: str, source_name: str, title: str, raw_text: str, url: str = "", score: int = 0,
               pillar: str | None = None, db_path: Path | str | None = None) -> bool:
    """Insert a NEW story. False when that source_id is already in the bank (any status)."""
    with closing(_connect(db_path)) as conn, conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_stories (source_id, source_name, title, raw_text, url, score, pillar) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (source_id, source_name, title, raw_text, url, int(score or 0), _pillar(pillar)),
        )
        return cur.rowcount == 1


def pillar_mix(db_path: Path | str | None = None) -> dict[str, dict]:
    """{pillar: {"target": 0.4, "produced": scripted or used videos, "new": NEW stories waiting}}."""
    mix = {p: {"target": t, "produced": 0, "new": 0} for p, t in PILLARS.items()}
    with closing(_connect(db_path)) as conn:
        for pillar, status, n in conn.execute(
            "SELECT pillar, status, COUNT(*) FROM raw_stories WHERE pillar IS NOT NULL GROUP BY 1, 2"
        ):
            if pillar in mix and status in PRODUCED:
                mix[pillar]["produced"] += n
            elif pillar in mix and status == "NEW":
                mix[pillar]["new"] += n
    return mix


def next_pillar(db_path: Path | str | None = None, targets: dict[str, float] | None = None) -> str | None:
    """The pillar furthest below its target share (counting the next video), among those with NEW stories.
    `targets`: other shares than PILLARS (the insights' biased mix, modules.da_insights.biased_targets)."""
    mix = pillar_mix(db_path)
    total = sum(m["produced"] for m in mix.values()) + 1
    waiting = [p for p in PILLARS if mix[p]["new"]]
    share = {p: (targets or {}).get(p, PILLARS[p]) for p in PILLARS}
    return max(waiting, key=lambda p: share[p] * total - mix[p]["produced"], default=None)


def get_next_story(db_path: Path | str | None = None, targets: dict[str, float] | None = None) -> dict | None:
    """The top-rated NEW story of the pillar the mix needs most (oldest first on a tie); stories without
    a pillar only when no pillar has any. None when the bank has no NEW story."""
    pillar = next_pillar(db_path, targets)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM raw_stories WHERE status = 'NEW' AND (pillar = ? OR ? IS NULL) "
            "ORDER BY score DESC, id ASC LIMIT 1", (pillar, pillar)
        ).fetchone()
        return dict(row) if row else None


def set_status(story_id: int, status: str, db_path: Path | str | None = None, note: str | None = None) -> None:
    """Change the status; `note` (e.g. why it was rejected) replaces the old note when given."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    with closing(_connect(db_path)) as conn, conn:
        conn.execute("UPDATE raw_stories SET status = ?, note = COALESCE(?, note) WHERE id = ?",
                     (status, note, story_id))


def update_story(story_id: int, db_path: Path | str | None = None, **fields) -> None:
    """Set raw_text, pillar, note and/or score."""
    allowed = {"raw_text", "pillar", "note", "score"}
    if not fields or set(fields) - allowed:
        raise ValueError(f"can only update {', '.join(sorted(allowed))}")
    if "pillar" in fields:
        _pillar(fields["pillar"])
    with closing(_connect(db_path)) as conn, conn:
        conn.execute(f"UPDATE raw_stories SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     (*fields.values(), story_id))


def known_ids(prefix: str = "", db_path: Path | str | None = None) -> set[str]:
    with closing(_connect(db_path)) as conn:
        return {r[0] for r in conn.execute("SELECT source_id FROM raw_stories WHERE source_id LIKE ?", (f"{prefix}%",))}


def known_titles(db_path: Path | str | None = None, limit: int = 300) -> list[str]:
    """Titles already in the bank, newest first (Gemini is told to avoid them)."""
    with closing(_connect(db_path)) as conn:
        return [r[0] for r in conn.execute("SELECT title FROM raw_stories ORDER BY id DESC LIMIT ?", (limit,))]


def all_stories(status: str | None = None, db_path: Path | str | None = None) -> list[dict]:
    """Full rows (with raw_text), oldest first."""
    with closing(_connect(db_path)) as conn:
        sql = "SELECT * FROM raw_stories" + (" WHERE status = ?" if status else "") + " ORDER BY id"
        return [dict(r) for r in conn.execute(sql, (status,) if status else ())]


def counts(db_path: Path | str | None = None) -> dict[str, int]:
    with closing(_connect(db_path)) as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM raw_stories GROUP BY status")}


def source_counts(db_path: Path | str | None = None) -> dict[str, dict[str, int]]:
    """{"reddit": {"NEW": 40, "USED": 2}, "wikipedia": {...}, ...} (by source family)."""
    out: dict[str, dict[str, int]] = {}
    with closing(_connect(db_path)) as conn:
        for family, status, n in conn.execute(
            "SELECT substr(source_id, 1, instr(source_id, '_') - 1), status, COUNT(*) FROM raw_stories GROUP BY 1, 2"
        ):
            out.setdefault(family, {})[status] = n
    return out


def list_stories(status: str | None = None, source: str | None = None, q: str = "", limit: int = 200,
                 pillar: str | None = None, db_path: Path | str | None = None) -> list[dict]:
    """Stories without their full text (a preview instead), best first. `source`: reddit / wikipedia / gemini / rss."""
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if source:
        where.append("source_id LIKE ? ESCAPE '\\'")
        args.append(f"{source}\\_%")
    if pillar:
        where.append("pillar = ?")
        args.append(pillar)
    if q.strip():
        where.append("(title LIKE ? OR raw_text LIKE ?)")
        args += [f"%{q.strip()}%"] * 2
    sql = ("SELECT id, source_id, source_name, title, url, score, status, created_at, pillar, note, "
           "LENGTH(raw_text) AS chars, substr(raw_text, 1, 280) AS preview FROM raw_stories"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY score DESC, id ASC LIMIT ?")
    with closing(_connect(db_path)) as conn:
        return [dict(r) for r in conn.execute(sql, (*args, limit))]


def get_story(story_id: int, db_path: Path | str | None = None) -> dict | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM raw_stories WHERE id = ?", (story_id,)).fetchone()
        return dict(row) if row else None


def find_source(source_id: str, db_path: Path | str | None = None) -> dict | None:
    """The story saved under `source_id` (the same story has the same source_id on every machine)."""
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM raw_stories WHERE source_id = ?", (source_id,)).fetchone()
        return dict(row) if row else None
