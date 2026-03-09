"""SQLite database — single source of truth for all application data.

On first run it creates data/prism.db and migrates any existing JSON files.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/prism.db")


# ── connection ───────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads + writes
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── schema ───────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    username        TEXT UNIQUE NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    role            TEXT NOT NULL DEFAULT 'viewer',
    project_filter  TEXT NOT NULL DEFAULT 'all',
    allowed_projects TEXT NOT NULL DEFAULT '[]',
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS overrides (
    project         TEXT NOT NULL,
    task            TEXT NOT NULL,
    field           TEXT NOT NULL DEFAULT 'pct',
    value           REAL NOT NULL,
    original_value  REAL NOT NULL,
    updated_by      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (project, task, field)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    project         TEXT NOT NULL,
    task            TEXT,
    field           TEXT,
    action          TEXT NOT NULL,
    old_value       REAL,
    new_value       REAL,
    user            TEXT NOT NULL,
    synced_to_msp   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS change_requests (
    id              TEXT PRIMARY KEY,
    project         TEXT NOT NULL,
    task            TEXT NOT NULL,
    current_value   REAL NOT NULL,
    requested_value REAL NOT NULL,
    reason          TEXT NOT NULL,
    requested_by    TEXT NOT NULL,
    requested_at    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    reviewed_by     TEXT,
    reviewed_at     TEXT,
    review_note     TEXT
);

CREATE INDEX IF NOT EXISTS idx_overrides_project  ON overrides(project);
CREATE INDEX IF NOT EXISTS idx_audit_project       ON audit_log(project);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp     ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_cr_status           ON change_requests(status);
"""


# ── init & migration ─────────────────────────────────────────────

def init_db() -> None:
    """Create schema and migrate from legacy JSON files (runs once)."""
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        _migrate_users(conn)
        _migrate_overrides(conn)
        _migrate_audit(conn)
        _migrate_change_requests(conn)


def _migrate_users(conn: sqlite3.Connection) -> None:
    path = Path("data/users.json")
    if not path.exists():
        return
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return  # already migrated
    data = json.loads(path.read_text(encoding="utf-8"))
    for u in data.get("users", []):
        conn.execute(
            "INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?,?,?)",
            (u["id"], u["name"], u["username"].lower(), u["email"].lower(),
             u.get("role","viewer"), u.get("project_filter","all"),
             json.dumps(u.get("allowed_projects",[])),
             1 if u.get("active", True) else 0)
        )


def _migrate_overrides(conn: sqlite3.Connection) -> None:
    path = Path("data/overrides.json")
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    meta_keys = {"updated_by", "updated_at", "original_value", "original_pct"}
    for project, pd in data.get("projects", {}).items():
        for task, td in pd.get("tasks", {}).items():
            updated_by = td.get("updated_by", "")
            updated_at = td.get("updated_at", "")
            for field, fv in td.items():
                if field in meta_keys:
                    continue
                # "pct" → field="pct"; original stored as "original_pct" or "original_value"
                original = td.get(f"original_{field}",
                           td.get("original_value", fv))
                conn.execute(
                    "INSERT OR IGNORE INTO overrides VALUES (?,?,?,?,?,?,?)",
                    (project, task, field, fv, original, updated_by, updated_at)
                )


def _migrate_audit(conn: sqlite3.Connection) -> None:
    path = Path("data/audit_log.json")
    if not path.exists():
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    # support both list format and {"entries": [...]} format
    entries = raw if isinstance(raw, list) else raw.get("entries", [])
    for e in entries:
        conn.execute(
            "INSERT OR IGNORE INTO audit_log VALUES (?,?,?,?,?,?,?,?,?,?)",
            (e.get("id", str(uuid.uuid4())), e.get("timestamp",""),
             e.get("project",""), e.get("task"), e.get("field"),
             e.get("action",""), e.get("old_value"), e.get("new_value"),
             e.get("user",""), 1 if e.get("synced_to_msp") else 0)
        )


def _migrate_change_requests(conn: sqlite3.Connection) -> None:
    path = Path("data/change_requests.json")
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    for r in data.get("requests", []):
        conn.execute(
            "INSERT OR IGNORE INTO change_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["project"], r["task"],
             r["current_value"], r["requested_value"], r["reason"],
             r["requested_by"], r["requested_at"], r.get("status","pending"),
             r.get("reviewed_by"), r.get("reviewed_at"), r.get("review_note"))
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
