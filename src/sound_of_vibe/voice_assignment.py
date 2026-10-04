"""Transactional voice reservations shared by Kimi and Codex workers."""

import sqlite3
import time
from contextlib import closing
from pathlib import Path


class VoiceAssignments:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "voice_sessions.sqlite3"
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS voices (session TEXT PRIMARY KEY, zh TEXT UNIQUE, en TEXT UNIQUE, touched REAL)")

    def choose(self, session: str, preferred: dict, catalog: list, now=None) -> dict:
        now = time.time() if now is None else now
        pools = {language: sorted({v["ShortName"] for v in catalog
                                  if v["Locale"].startswith(language + "-")})
                 for language in ("zh", "en")}
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            # Recover reservations left behind by a crashed/abandoned CLI.
            db.execute("DELETE FROM voices WHERE touched < ?", (now - 86400,))
            rows = db.execute("SELECT session,zh,en FROM voices").fetchall()
            previous = next((dict(zip(("zh", "en"), row[1:])) for row in rows if row[0] == session), None)
            if previous and all(previous[k] in pools[k] for k in pools):
                db.execute("UPDATE voices SET touched=? WHERE session=?", (now, session))
                return previous
            result = {}
            for index, language in enumerate(("zh", "en"), 1):
                used = {row[index] for row in rows if row[0] != session}
                preferred_locale = "zh-CN-" if language == "zh" else "en-US-"
                candidates = sorted(pools[language], key=lambda name: (name != preferred.get(language),
                                                                      not name.startswith(preferred_locale), name))
                result[language] = next((name for name in candidates if name not in used), None)
                if result[language] is None:
                    raise ValueError("No distinct " + language + " voice available; close an unused session")
            db.execute("INSERT OR REPLACE INTO voices VALUES (?,?,?,?)",
                       (session, result["zh"], result["en"], now))
            return result

    def release(self, session: str):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("DELETE FROM voices WHERE session=?", (session,))
