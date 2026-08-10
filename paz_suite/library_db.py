"""Library index: the SQLite schema, the in-memory record shape, and the
e621-style search parser. Pure logic — no widgets — so the sync worker and
the search box can both be tested without a running GUI.
"""

from __future__ import annotations

import fnmatch
import os
import sqlite3
from dataclasses import dataclass, field

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path     TEXT PRIMARY KEY,
    name     TEXT,
    folder   TEXT,
    pid      TEXT,
    size     INTEGER,
    mtime    INTEGER,
    duration REAL,
    width    INTEGER,
    height   INTEGER,
    fps      REAL
);
"""


def db_connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_pid ON files(pid)")
    return conn


@dataclass
class Rec:
    path: str
    name: str
    folder: str
    pid: str
    size: int
    mtime: int
    duration: float
    width: int
    height: int
    fps: float
    # filled from the e621 cache at load time
    artists: list = field(default_factory=list)
    characters: list = field(default_factory=list)
    species: list = field(default_factory=list)
    copyrights: list = field(default_factory=list)
    lore: list = field(default_factory=list)
    rating: str = ""
    score: int = 0
    tags: set = field(default_factory=set)
    url: str = ""
    premium: bool = False        # a 4K/60+ copy exists (or this IS 4K)
    # artist/character/species/copyright/lore names, union'd once at load
    # time instead of on every tag-panel and detail-panel render - the
    # difference is real once a library runs into five figures of clips.
    named: frozenset = field(default_factory=frozenset)

    def compute_named(self) -> None:
        self.named = frozenset(self.artists) | frozenset(self.characters) \
            | frozenset(self.species) | frozenset(self.copyrights) | frozenset(self.lore)


def parse_query(text: str) -> tuple:
    """Split an e621-style query into include / exclude term lists."""
    includes, excludes = [], []
    for token in text.split():
        target = includes
        if token.startswith("-") and len(token) > 1:
            target = excludes
            token = token[1:]
        token = token.lower()
        if ":" in token:
            kind, _, value = token.partition(":")
            if kind in ("artist", "character", "species", "copyright",
                        "series", "lore", "rating", "folder", "id",
                        "is") and value:
                target.append((kind, value))
                continue
        target.append(("tag", token))
    return includes, excludes


def term_hits(rec: Rec, kind: str, value: str) -> bool:
    if kind == "is":
        if value in ("untagged", "notags"):
            return not rec.tags
        if value == "tagged":
            return bool(rec.tags)
        if value in ("noid", "unknown"):
            return not rec.pid
        if value == "silent":
            return rec.duration <= 0
        if value in ("4k", "premium"):
            return rec.premium
        if value in ("no4k", "sd"):
            return not rec.premium
        return False
    if kind == "artist":
        return any(value == a or fnmatch.fnmatch(a, value) for a in rec.artists)
    if kind == "character":
        return any(value == c or fnmatch.fnmatch(c, value) for c in rec.characters)
    if kind == "species":
        return any(value == s or fnmatch.fnmatch(s, value) for s in rec.species)
    if kind in ("copyright", "series"):
        return any(value == c or fnmatch.fnmatch(c, value) for c in rec.copyrights)
    if kind == "lore":
        return any(value == l or fnmatch.fnmatch(l, value) for l in rec.lore)
    if kind == "rating":
        return rec.rating == value[:1]
    if kind == "folder":
        return value in rec.folder.lower()
    if kind == "id":
        return rec.pid == value
    # plain tag term
    if "*" in value:
        return any(fnmatch.fnmatch(t, value) for t in rec.tags)
    if value in rec.tags:
        return True
    # substring fallback: tags, filename, post id
    if value in rec.name.lower() or value in rec.pid:
        return True
    return any(value in t for t in rec.tags)


def rec_matches(rec: Rec, includes: list, excludes: list) -> bool:
    for kind, value in includes:
        if not term_hits(rec, kind, value):
            return False
    for kind, value in excludes:
        if term_hits(rec, kind, value):
            return False
    return True


SORTS = {
    "Newest":  lambda r: -r.mtime,
    "Name":    lambda r: r.name.lower(),
    "Longest": lambda r: -r.duration,
    "Largest": lambda r: -r.size,
    "Score":   lambda r: (-r.score, r.name.lower()),
}
