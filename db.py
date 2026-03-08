"""
db.py – lightweight SQLite persistence for hackathon analysis jobs.
Thread-safe with a per-operation lock.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "hackathon.db")
_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with _lock:
        with _connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id            TEXT PRIMARY KEY,
                    url           TEXT NOT NULL,
                    status        TEXT DEFAULT 'pending',
                    hackathon_name TEXT,
                    judge_count   INTEGER DEFAULT 0,
                    data          TEXT DEFAULT '{}',
                    log           TEXT DEFAULT '[]',
                    created_at    TEXT,
                    updated_at    TEXT
                )
            """)
            conn.commit()


def create_job(job_id: str, url: str):
    now = datetime.utcnow().isoformat()
    with _lock:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, url, status, data, log, created_at, updated_at) "
                "VALUES (?, ?, 'pending', '{}', '[]', ?, ?)",
                (job_id, url, now, now),
            )
            conn.commit()


def get_job(job_id: str):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    job = dict(row)
    blob = json.loads(job.pop("data") or "{}")
    job["log"] = json.loads(job.pop("log") or "[]")
    job.update(blob)  # flatten stored data into top-level keys
    return job


def update_job(job_id: str, updates: dict):
    """Merge updates into the job.  Special keys: status, hackathon_name, judge_count, error."""
    with _lock:
        with _connect() as conn:
            row = conn.execute(
                "SELECT data, status, hackathon_name, judge_count FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return
            blob = json.loads(row["data"] or "{}")
            status = row["status"]
            hackathon_name = row["hackathon_name"]
            judge_count = row["judge_count"]

            for k, v in updates.items():
                if k == "status":
                    status = v
                elif k == "hackathon_name":
                    hackathon_name = v
                elif k == "judge_count":
                    judge_count = v
                elif k == "error":
                    blob["error"] = v  # Store error in blob
                else:
                    blob[k] = v

            now = datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE jobs SET data=?, status=?, hackathon_name=?, judge_count=?, updated_at=? "
                "WHERE id=?",
                (json.dumps(blob), status, hackathon_name, judge_count, now, job_id),
            )
            conn.commit()


def add_log(job_id: str, message: str, level: str = "info"):
    with _lock:
        with _connect() as conn:
            row = conn.execute("SELECT log FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return
            log = json.loads(row["log"] or "[]")
            log.append({
                "msg": message,
                "level": level,
                "ts": datetime.utcnow().isoformat(),
            })
            now = datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE jobs SET log=?, updated_at=? WHERE id=?",
                (json.dumps(log), now, job_id),
            )
            conn.commit()


def list_jobs():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, url, status, hackathon_name, judge_count, created_at "
            "FROM jobs ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]
