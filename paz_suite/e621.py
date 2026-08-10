"""e621 tag lookup — one cache, shared by Convert and Library.

Grabber downloads carry nothing but the post ID ("6574692.webm"), so a
freshly converted library starts out tagless. e621's public JSON API turns
that ID back into artist, characters, species, rating and score. Rules of
the road: a descriptive User-Agent, roughly one request per second, and a
local cache so no post is ever asked about twice. Only the ID is ever sent -
no filenames, no paths, no thumbnails.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

from .config import CONFIG_DIR, E621_META_PATH

APP_NAME = "PAZ Suite"
APP_VERSION = "1.0"

E621_API = "https://e621.net/posts/{pid}.json"
E621_POST = "https://e621.net/posts/{pid}"
E621_UA = f"{APP_NAME}/{APP_VERSION} (personal library tagger)"

# Artist-category tags that aren't actually artists.
_ARTIST_NOISE = {"conditional_dnp", "avoid_posting", "unknown_artist",
                  "sound_warning", "epilepsy_warning", "third-party_edit"}


class E621Meta:
    """
    Sidecar tag database keyed by post ID.

    Records look like {artist:[], character:[], species:[], copyright:[],
    lore:[], rating:"e", score:int, tags:"flat lowercase string", url:...}.
    A post that 404s (or is hidden from anonymous users) is cached as
    {"missing": True} so it is not retried every run; transient network
    errors are NOT cached.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._dirty = False
        try:
            with open(E621_META_PATH, "r", encoding="utf-8") as fh:
                self._data = json.load(fh)
        except (OSError, ValueError):
            self._data = {}

    def get(self, pid: str) -> dict | None:
        with self._lock:
            return self._data.get(pid)

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            data = dict(self._data)
            self._dirty = False
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = E621_META_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, E621_META_PATH)
        except OSError:
            pass

    def fetch(self, pid: str, user: str = "", key: str = "") -> dict:
        """One API call. Returns the record; {"error": ...} on transient failure."""
        url = E621_API.format(pid=pid)
        if user and key:
            url += ("?login=" + urllib.parse.quote(user) +
                    "&api_key=" + urllib.parse.quote(key))
        request = urllib.request.Request(url, headers={"User-Agent": E621_UA})
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            record = {"missing": True, "error": f"HTTP {exc.code}"}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"error": str(exc)}          # transient: do not cache
        else:
            post = payload.get("post") or {}
            tags = post.get("tags") or {}

            def cat(name):
                return list(tags.get(name) or [])

            flat = []
            for group in ("artist", "character", "species", "copyright",
                          "general", "meta", "lore"):
                flat.extend(tags.get(group) or [])
            record = {
                "artist": [a for a in cat("artist") if a not in _ARTIST_NOISE],
                "character": cat("character"),
                "species": cat("species"),
                "copyright": cat("copyright"),
                "lore": cat("lore"),
                "rating": (post.get("rating") or "")[:1],
                "score": (post.get("score") or {}).get("total", 0),
                "tags": " ".join(flat).lower(),
                "url": E621_POST.format(pid=pid),
            }
        with self._lock:
            self._data[pid] = record
            self._dirty = True
        return record
