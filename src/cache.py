from __future__ import annotations

import hashlib
import json
from pathlib import Path

DEFAULT_CACHE_DIR = Path(".cache")


class JSONCache:
    """Flat on-disk cache so repeated runs (resume parsing, fit scoring,
    screening-question answers) don't re-spend LLM tokens on work already
    done. Keyed by an explicit hash the caller builds from whatever makes
    the request unique -- no TTL, since a resume or a job posting's content
    doesn't change under us; delete the file to force a refresh.

    base_dir lets multiple accounts/profiles keep fully separate caches
    (profiles/<name>/cache/) so one person's cached LLM answers never mix
    with another's -- defaults to the original shared .cache/ for backward
    compatibility with single-profile runs."""

    def __init__(self, name: str, base_dir: Path | str = DEFAULT_CACHE_DIR):
        cache_dir = Path(base_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.path = cache_dir / f"{name}.json"
        self._data: dict = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.path.write_text(json.dumps(self._data))

    @staticmethod
    def make_key(*parts: str) -> str:
        return hashlib.sha256("||".join(parts).encode()).hexdigest()
