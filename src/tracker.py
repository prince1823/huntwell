from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from .models import ApplicationResult, JobPosting

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    dedupe_key TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    job_id TEXT NOT NULL,
    title TEXT,
    company TEXT,
    url TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    applied_at TEXT NOT NULL
);
"""


class Tracker:
    def __init__(self, db_path: str = "applications.db"):
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(SCHEMA)
        self._conn.commit()

    def already_handled(self, job: JobPosting) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM applications WHERE dedupe_key = ?", (job.dedupe_key,)
        ).fetchone()
        return row is not None

    def record(self, result: ApplicationResult) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO applications
               (dedupe_key, platform, job_id, title, company, url, status, reason, applied_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result.job.dedupe_key,
                result.job.platform,
                result.job.job_id,
                result.job.title,
                result.job.company,
                result.job.url,
                result.status,
                result.reason,
                datetime.utcnow().isoformat(),
            ),
        )
        self._conn.commit()

    def count_applied_today(self, platform: str) -> int:
        today = date.today().isoformat()
        row = self._conn.execute(
            """SELECT COUNT(*) FROM applications
               WHERE platform = ? AND status = 'applied' AND applied_at LIKE ?""",
            (platform, f"{today}%"),
        ).fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        self._conn.close()
