"""SQLite run log: runs, LLM routing/fallback events, and platform posts (FR-4)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    template TEXT,
    aspect TEXT,
    target_seconds REAL,
    status TEXT NOT NULL,          -- running | rendered | published | partial | failed
    video_path TEXT,
    video_seconds REAL,
    error TEXT,
    started_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    stage TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    fallbacks INTEGER NOT NULL DEFAULT 0,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    platform TEXT NOT NULL,
    status TEXT NOT NULL,          -- published | draft | failed | skipped
    post_id TEXT,
    url TEXT,
    error TEXT,
    detail TEXT,
    ts REAL NOT NULL
);
"""


def db_path() -> Path:
    raw = os.environ.get("AUTOPILOT_DB", "projects/autopilot.sqlite3")
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


class RunLog:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def start_run(self, project_id: str, topic: str, template: str, aspect: str, target_seconds: float) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (project_id, topic, template, aspect, target_seconds, status, started_at)"
            " VALUES (?, ?, ?, ?, ?, 'running', ?)",
            (project_id, topic, template, aspect, target_seconds, time.time()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_run(self, run_id: int, **fields: Any) -> None:
        if not fields:
            return
        if fields.get("status") in {"published", "partial", "failed", "rendered"}:
            fields.setdefault("finished_at", time.time())
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))
        self.conn.commit()

    def llm_call(self, run_id: int, stage: str, meta: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO llm_calls (run_id, stage, provider, model, fallbacks, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, stage, meta.get("provider"), meta.get("model"), int(meta.get("fallbacks") or 0), time.time()),
        )
        self.conn.commit()

    def post(self, run_id: int, platform: str, status: str, post_id: Optional[str] = None,
             url: Optional[str] = None, error: Optional[str] = None, detail: Any = None) -> None:
        self.conn.execute(
            "INSERT INTO posts (run_id, platform, status, post_id, url, error, detail, ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, platform, status, post_id, url, error,
             json.dumps(detail) if detail is not None else None, time.time()),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
