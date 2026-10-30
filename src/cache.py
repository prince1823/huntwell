from __future__ import annotations

import hashlib
import json
import os
import threading
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
    compatibility with single-profile runs.

    Thread-safe: the webapp runs LinkedIn/Wellfound/Ashby as three parallel
    threads that all share ONE Tailor instance, and therefore one JSONCache
    per cache name -- a real silent-failure bug, confirmed live, where a
    Wellfound candidate that scored well above the fit threshold produced
    no result at all, most likely from two threads' unlocked
    rewrite-the-whole-file .set() calls racing and corrupting the file (a
    bad read afterward raises, silently caught by the platform thread's own
    try/except, which is why it looked like nothing happened rather than
    an explicit error). A lock around get/set serializes access across
    threads within this process; the write itself is also done atomically
    (write to a temp file, then os.replace) so a crash or a second
    process's read mid-write can never see a half-written file."""

    def __init__(self, name: str, base_dir: Path | str = DEFAULT_CACHE_DIR):
        cache_dir = Path(base_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.path = cache_dir / f"{name}.json"
        self._lock = threading.Lock()
        self._data: dict = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, key: str):
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
            tmp_path.write_text(json.dumps(self._data))
            os.replace(tmp_path, self.path)

    @staticmethod
    def make_key(*parts: str) -> str:
        return hashlib.sha256("||".join(parts).encode()).hexdigest()
