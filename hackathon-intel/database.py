import sqlite3
import json
from datetime import datetime


class Database:
    def __init__(self, db_path="hackathon_intel.db"):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    hackathon_name TEXT,
                    judge_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running',
                    created_at TEXT,
                    results_json TEXT,
                    linkedin_urls_json TEXT,
                    error TEXT
                )
            """
            )
            conn.commit()
            self._migrate_db(conn)

    def _migrate_db(self, conn):
        """Add any missing columns to existing database."""
        # Check if linkedin_urls_json column exists
        cursor = conn.execute("PRAGMA table_info(analyses)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if "linkedin_urls_json" not in columns:
            conn.execute("ALTER TABLE analyses ADD COLUMN linkedin_urls_json TEXT")
            conn.commit()
            print("[DB] Migrated: added linkedin_urls_json column")

    def create_analysis(self, job_id, url):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO analyses (id, url, status, created_at) VALUES (?, ?, ?, ?)",
                (job_id, url, "running", datetime.utcnow().isoformat()),
            )
            conn.commit()

    def update_analysis(self, job_id, results):
        name = results.get("hackathon", {}).get("name", "Unknown Hackathon")
        judge_count = len(results.get("judges", []))
        
        # Extract LinkedIn URLs for easy access
        linkedin_urls = {}
        for judge in results.get("judges", []):
            if judge.get("linkedin_url"):
                linkedin_urls[judge.get("name", "Unknown")] = judge.get("linkedin_url")
        
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE analyses SET status=?, hackathon_name=?, judge_count=?, results_json=?, linkedin_urls_json=? WHERE id=?",
                ("complete", name, judge_count, json.dumps(results), json.dumps(linkedin_urls), job_id),
            )
            conn.commit()

    def mark_error(self, job_id, error):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE analyses SET status=?, error=? WHERE id=?",
                ("error", error[:500], job_id),
            )
            conn.commit()

    def get_analysis(self, job_id):
        with self._get_conn() as conn:
            row = (
                conn.execute(
                    "SELECT * FROM analyses WHERE id=?", (job_id,)
                ).fetchone()
            )
            if row:
                return dict(row)
        return None

    def get_all_analyses(self):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, url, hackathon_name, judge_count, status, created_at FROM analyses ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            return [dict(r) for r in rows]
