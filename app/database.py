"""SQLite database — single source of truth for all application data.

On first run it creates data/prism.db and migrates any existing JSON files.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

# IMPORTANT: use an absolute path anchored to the repo root, not process cwd.
# Otherwise running uvicorn from a different working directory creates/uses a different DB.
DB_PATH = (Path(__file__).resolve().parents[1] / "data" / "prism.db")


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
CREATE TABLE IF NOT EXISTS projects (
    code            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    mpp_path        TEXT,
    health          TEXT NOT NULL DEFAULT 'ontrack',
    progress        REAL NOT NULL DEFAULT 0.0,
    start_date      TEXT,
    end_date        TEXT,
    forecast_end_date TEXT,
    created_at      TEXT NOT NULL,
    team_type       TEXT NOT NULL DEFAULT 'cubes'
);

CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    project_code      TEXT NOT NULL,
    title             TEXT NOT NULL,
    start_date        TEXT,
    end_date          TEXT,
    pct               REAL NOT NULL DEFAULT 0.0,
    outline_level     INTEGER NOT NULL DEFAULT 1,
    is_summary        INTEGER NOT NULL DEFAULT 0,
    is_milestone      INTEGER NOT NULL DEFAULT 0,
    is_critical       INTEGER NOT NULL DEFAULT 0,
    forecast_end_date TEXT,
    comments          TEXT,
    extended_days     INTEGER NOT NULL DEFAULT 0,
    actual_start_date TEXT,
    actual_finish_date TEXT,
    FOREIGN KEY(project_code) REFERENCES projects(code)
);

CREATE TABLE IF NOT EXISTS dashboard_settings (
    id      TEXT PRIMARY KEY DEFAULT 'global',
    data    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    username        TEXT UNIQUE NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    role            TEXT NOT NULL DEFAULT 'viewer',
    project_filter  TEXT NOT NULL DEFAULT 'all',
    allowed_projects TEXT NOT NULL DEFAULT '[]',
    active          INTEGER NOT NULL DEFAULT 1,
    settings_override TEXT NOT NULL DEFAULT '{}'
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

CREATE TABLE IF NOT EXISTS resource_pool (
    role            TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    total_count     INTEGER NOT NULL DEFAULT 1,
    hours_per_day   REAL NOT NULL DEFAULT 8.0
);

CREATE TABLE IF NOT EXISTS project_allocations (
    id              TEXT PRIMARY KEY,
    project_code    TEXT NOT NULL,
    role            TEXT NOT NULL,
    assigned_count  INTEGER NOT NULL DEFAULT 1,
    UNIQUE(project_code, role),
    FOREIGN KEY(project_code) REFERENCES projects(code)
);

CREATE TABLE IF NOT EXISTS resources (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    role     TEXT NOT NULL,
    title    TEXT,
    active   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS named_allocations (
    id             TEXT PRIMARY KEY,
    project_code   TEXT NOT NULL,
    resource_id    TEXT NOT NULL,
    allocation_pct INTEGER NOT NULL DEFAULT 100,
    UNIQUE(project_code, resource_id),
    FOREIGN KEY(project_code) REFERENCES projects(code),
    FOREIGN KEY(resource_id) REFERENCES resources(id)
);

CREATE TABLE IF NOT EXISTS products (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS project_products (
    project_code TEXT NOT NULL,
    product_id   TEXT NOT NULL,
    PRIMARY KEY (project_code, product_id),
    FOREIGN KEY(project_code) REFERENCES projects(code),
    FOREIGN KEY(product_id)   REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS c2026_plan (
    id      TEXT PRIMARY KEY DEFAULT 'main',
    data    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_progress (
    id          TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    project_code TEXT,
    status      TEXT NOT NULL DEFAULT '',
    ba          TEXT NOT NULL DEFAULT '',
    uiux        TEXT NOT NULL DEFAULT '',
    qc          TEXT NOT NULL DEFAULT '',
    c_classic   TEXT NOT NULL DEFAULT '',
    fe          TEXT NOT NULL DEFAULT '',
    be          TEXT NOT NULL DEFAULT '',
    due_date    TEXT NOT NULL DEFAULT '',
    start_date  TEXT NOT NULL DEFAULT '',
    end_date    TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_overrides_project  ON overrides(project);
CREATE INDEX IF NOT EXISTS idx_audit_project       ON audit_log(project);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp     ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_cr_status           ON change_requests(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project       ON tasks(project_code);
CREATE INDEX IF NOT EXISTS idx_alloc_project       ON project_allocations(project_code);
CREATE INDEX IF NOT EXISTS idx_named_alloc_proj    ON named_allocations(project_code);
CREATE INDEX IF NOT EXISTS idx_named_alloc_res     ON named_allocations(resource_id);
"""


# ── CUBES 2026 plan seed ─────────────────────────────────────────

def _seed_c2026(conn):
    existing = conn.execute("SELECT id FROM c2026_plan WHERE id='main'").fetchone()
    if existing:
        return
    default_plan = {
        "program_name": "CUBES 2026",
        "projects": [
            {
                "id": "c26p1", "name": "Compliance — Audit Module",
                "description": "Core audit & compliance tracking module",
                "show_hiring": False,
                "phases": [
                    {
                        "id": "c26ph1", "name": "Phase 1 - Definitions",
                        "team_rows": [
                            {"id": "c26r1","team":"BA","status":"Completed","pct":100,"days":77,"start":"2025-07-01","end":"2025-10-15"},
                            {"id": "c26r2","team":"UI/UX","status":"Completed","pct":100,"days":77,"start":"2025-07-01","end":"2025-10-15"},
                            {"id": "c26r3","team":"FE","status":"Completed - Rework","pct":100,"days":None,"start":"2025-10-16","end":"2026-09-03"},
                            {"id": "c26r4","team":"BE","status":"Completed - Rework","pct":100,"days":99,"start":"2025-10-16","end":"2026-03-03"},
                            {"id": "c26r5","team":"QC","status":"In Progress","pct":None,"days":None,"start":"2025-12-24","end":"2026-02-16"}
                        ],
                        "releases": [
                            {"id":"c26rel1","name":"V0.1.0","days":19,"start":"2025-12-31","end":"2026-01-26"},
                            {"id":"c26rel2","name":"V0.1.1 - Phase 1","days":10,"start":"2026-01-27","end":"2026-02-09"},
                            {"id":"c26rel3","name":"V0.1.1 - Phase 2","days":5,"start":"2026-02-25","end":"2026-03-02"},
                            {"id":"c26rel4","name":"V0.1.1 - Phase 3","days":None,"start":None,"end":None}
                        ]
                    },
                    {
                        "id": "c26ph2", "name": "Phase 2 - Execution",
                        "team_rows": [
                            {"id":"c26r6","team":"BA","status":"Completed","pct":100,"days":50,"start":"2025-10-17","end":"2025-12-25"},
                            {"id":"c26r7","team":"UI/UX","status":"Completed","pct":100,"days":50,"start":"2025-10-17","end":"2025-12-25"},
                            {"id":"c26r8","team":"FE","status":"In Progress","pct":36,"days":45,"start":"2025-12-25","end":None},
                            {"id":"c26r9","team":"BE","status":"In Progress","pct":75,"days":None,"start":"2025-12-25","end":None},
                            {"id":"c26r10","team":"QC","status":"Not Started","pct":0,"days":60,"start":None,"end":None}
                        ],
                        "releases": []
                    },
                    {
                        "id": "c26ph3", "name": "Phase 3 - Reporting & Dashboards",
                        "team_rows": [
                            {"id":"c26r11","team":"BA","status":"In Progress","pct":100,"days":42,"start":"2026-01-01","end":"2026-02-28"},
                            {"id":"c26r12","team":"UI/UX","status":"In Progress","pct":100,"days":45,"start":"2026-01-10","end":"2026-03-15"},
                            {"id":"c26r13","team":"FE","status":"Not Started","pct":0,"days":None,"start":None,"end":None},
                            {"id":"c26r14","team":"BE","status":"Not Started","pct":0,"days":None,"start":None,"end":None},
                            {"id":"c26r15","team":"QC","status":"Not Started","pct":0,"days":23,"start":None,"end":None}
                        ],
                        "releases": []
                    }
                ],
                "hiring": []
            },
            {
                "id": "c26p2", "name": "Shared Functions",
                "description": "Action Management, Collaboration, Workflow, etc.",
                "show_hiring": False,
                "phases": [
                    {
                        "id": "c26ph4", "name": "Phase 1 - Planning",
                        "team_rows": [
                            {"id":"c26r16","team":"BA","status":"In Progress","pct":1,"days":None,"start":None,"end":None},
                            {"id":"c26r17","team":"UI/UX","status":"TBD","pct":None,"days":None,"start":None,"end":None},
                            {"id":"c26r18","team":"FE","status":"TBD","pct":None,"days":None,"start":None,"end":None},
                            {"id":"c26r19","team":"BE","status":"TBD","pct":None,"days":None,"start":None,"end":None},
                            {"id":"c26r20","team":"QC","status":"TBD","pct":None,"days":None,"start":None,"end":None}
                        ],
                        "releases": []
                    }
                ],
                "hiring": []
            },
            {
                "id": "c26p3", "name": "CUBES Intelligence",
                "description": "AI-powered search, analytics & insights within CUBES",
                "show_hiring": True,
                "phases": [
                    {
                        "id": "c26ph5", "name": "Phase 1 - Development",
                        "team_rows": [
                            {"id":"c26r21","team":"BA","status":"TBD","pct":None,"days":None,"start":None,"end":None},
                            {"id":"c26r22","team":"UI/UX","status":"TBD","pct":None,"days":None,"start":None,"end":None},
                            {"id":"c26r23","team":"FE","status":"TBD","pct":None,"days":None,"start":None,"end":None},
                            {"id":"c26r24","team":"BE","status":"TBD","pct":None,"days":None,"start":None,"end":None},
                            {"id":"c26r25","team":"QC","status":"TBD","pct":None,"days":None,"start":None,"end":None}
                        ],
                        "releases": []
                    }
                ],
                "hiring": [
                    {"id":"c26h1","role":"BA","q1":"","q2":"Hire","q3":"","q4":""},
                    {"id":"c26h2","role":"UI/UX","q1":"TBD","q2":"","q3":"","q4":""},
                    {"id":"c26h3","role":"FE","q1":"TBD","q2":"TBD","q3":"","q4":""},
                    {"id":"c26h4","role":"BE","q1":"TBD","q2":"TBD","q3":"","q4":""},
                    {"id":"c26h5","role":"AI Engineer","q1":"","q2":"TBD","q3":"","q4":""}
                ]
            },
            {
                "id": "c26p4", "name": "Performance Sub-Module Revamp",
                "description": "Revamping Performance module from Cubes Classic to new platform",
                "show_hiring": False,
                "phases": [
                    {
                        "id": "c26ph6", "name": "Phase 1 - Planning",
                        "team_rows": [
                            {"id":"c26r26","team":"BA","status":"TBD","pct":None,"days":None,"start":None,"end":None},
                            {"id":"c26r27","team":"UI/UX","status":"TBD","pct":None,"days":None,"start":None,"end":None},
                            {"id":"c26r28","team":"FE","status":"TBD","pct":None,"days":None,"start":None,"end":None},
                            {"id":"c26r29","team":"BE","status":"TBD","pct":None,"days":None,"start":None,"end":None},
                            {"id":"c26r30","team":"QC","status":"TBD","pct":None,"days":None,"start":None,"end":None}
                        ],
                        "releases": []
                    }
                ],
                "hiring": []
            }
        ]
    }
    conn.execute("INSERT INTO c2026_plan (id, data) VALUES ('main', ?)", (json.dumps(default_plan),))


# ── extension projects seed ──────────────────────────────────────

def _seed_ext_projects(conn):
    """Seed the 8 initial CR/extension projects if they don't exist yet."""
    ext_projects = [
        # (cr_id, name, client, start_date, progress, products_names, stage)
        ("CR#1-ICP",    "CR#1-ICP",    "ICP",    "2025-11-09", 1.0,  ["EW","ICG-EW","Leadership Dashboard","Data Management"], "Ready For Dev"),
        ("CR#2-DMT",    "CR#2-DMT",    "DMT",    "2026-01-07", 0.0,  ["Initiatives","Data Management"],                        "Ready For Dev"),
        ("CR#1-AZZ",    "CR#1-AZZ",    "AAZ",    "2026-01-28", 0.0,  ["Risk Dashboard"],                                       "Ready For Dev"),
        ("CR#1-DMT",    "CR#1-DMT-2",  "DMT",    "2026-02-09", 0.0,  ["Initiatives"],                                          "In BA"),
        ("CR#2-DDOF",   "CR#2-DDOF",   "DDOF",   "2026-01-28", 0.0,  ["Cubes Classic"],                                        "In BA"),
        ("CR#2-ICP",    "CR#2-ICP",    "ICP",    "2026-01-12", 0.0,  ["ICG-EW"],                                               "New"),
        ("CR#3-ADAFSA", "CR#3-ADAFSA", "ADAFSA", "2026-02-01", 0.0,  ["Common Dashboard","Cubes Classic"],                     "Under Development"),
        ("CR#3-DOF",    "CR#3-DOF",    "DOF",    "2026-02-09", 0.0,  ["Leadership Dashboard","Common Dashboard","Cubes Classic"], "New"),
    ]
    now_iso = datetime.now(timezone.utc).isoformat()
    for cr_id, name, client, start_date, progress, product_names, stage in ext_projects:
        existing = conn.execute("SELECT code FROM projects WHERE cr_id=?", (cr_id,)).fetchone()
        if existing:
            continue
        code = cr_id.replace("#", "").replace("-", "")[:8].upper() + "EXT"
        health = "G" if progress >= 100 else ("A" if progress >= 50 else "R")
        conn.execute(
            "INSERT INTO projects (code, name, mpp_path, health, progress, start_date, end_date, "
            "created_at, team_type, is_lightweight, client, cr_id, stage, show_on_main) "
            "VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?,1)",
            (code, name, "", health, progress, start_date, None, now_iso,
             "cubes", client, cr_id, stage)
        )
        # Link products
        for pname in product_names:
            prod = conn.execute("SELECT id FROM products WHERE name=?", (pname,)).fetchone()
            if prod:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO project_products (project_code, product_id) VALUES (?,?)",
                        (code, prod["id"])
                    )
                except Exception:
                    pass


# ── Project Progress seed ─────────────────────────────────────────
def _seed_project_progress(conn):
    count = conn.execute("SELECT COUNT(*) FROM project_progress").fetchone()[0]
    if count > 0:
        return
    rows = [
        # (project_name, project_code, status, ba, uiux, qc, c_classic, fe, be, due_date, start_date, end_date, sort_order)
        ("Cubes Enterprise Wallets", "EW",
         "Active",
         "done", "done", "5", "5", "5", "5",
         "2026-04-29", "", "", 1),
        ("Version Control", None,
         "Product team already discussed the Version Control with Dev team done 6-April expected",
         "done", "done", "", "", "", "",
         "2026-04-29", "", "", 2),
        ("Appraisal System Integration", None,
         "orientation session waiting Pro->Dev done NO NA TBD",
         "", "", "", "", "", "",
         "2026-05-10", "", "", 3),
        ("APQC", "APQC",
         "waiting for team feedback",
         "", "", "", "", "", "",
         "2026-04-09", "2026-03-04", "2026-03-31", 4),
        ("ICP", "ICP",
         "",
         "", "", "", "", "", "",
         "", "2026-02-23", "2026-03-24", 5),
        ("EF", "EF",
         "",
         "", "", "", "", "", "",
         "", "2026-02-23", "2026-04-06", 6),
        ("PMO/SPM - DOF", None,
         "waiting product team input",
         "", "", "", "", "", "",
         "", "", "", 7),
        ("Cubes Road map", None,
         "Github POC to do it for appraisal and version control for now",
         "", "", "", "", "", "",
         "", "", "", 8),
        ("DOF", None,
         "waiting QC",
         "", "", "", "", "", "",
         "", "", "", 9),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO project_progress (id, project_name, project_code, status, ba, uiux, qc, c_classic, fe, be, due_date, start_date, end_date, sort_order) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()),) + r
        )


# ── init & migration ─────────────────────────────────────────────

def init_db() -> None:
    """Create schema and migrate from legacy JSON files (runs once)."""
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        _migrate_users(conn)
        _migrate_overrides(conn)
        _migrate_audit(conn)
        _migrate_change_requests(conn)
        
        # safely migrate schema updates for 1.1.0 (Planner Update)
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN forecast_end_date TEXT")
        except sqlite3.OperationalError:
            pass
            
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN is_critical INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE tasks ADD COLUMN forecast_end_date TEXT")
            conn.execute("ALTER TABLE tasks ADD COLUMN comments TEXT")
        except sqlite3.OperationalError:
            pass

        # schema v1.5 — priority + resource tables
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN priority TEXT DEFAULT 'P3'")
        except sqlite3.OperationalError:
            pass

        # schema v1.6 — extended_days, actual dates, dashboard settings
        for stmt in [
            "ALTER TABLE tasks ADD COLUMN extended_days INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE tasks ADD COLUMN actual_start_date TEXT",
            "ALTER TABLE tasks ADD COLUMN actual_finish_date TEXT",
        ]:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass

        # schema v1.7 — team_type for CUBES vs Implementation split
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN team_type TEXT NOT NULL DEFAULT 'cubes'")
        except sqlite3.OperationalError:
            pass

        # schema v1.8 — requested_by field on projects
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN requested_by TEXT")
        except sqlite3.OperationalError:
            pass

        # schema v1.9 — exec summary display-only extra days
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN exec_additional_days INTEGER")
        except sqlite3.OperationalError:
            pass

        # Seed default dashboard settings if missing
        row = conn.execute("SELECT id FROM dashboard_settings WHERE id='global'").fetchone()
        if not row:
            default_settings = json.dumps({
                "viewer": {
                    "show_pct_edit": True,
                    "show_actual_dates": True,
                    "show_extended_days": True,
                    "show_risk_log_btn": True,
                    "show_intelligence": True,
                    "show_focus": True,
                    "show_gantt": True,
                    "show_audit_log": True,
                },
                "editor": {
                    "show_pct_edit": True,
                    "show_actual_dates": True,
                    "show_extended_days": True,
                    "show_risk_log_btn": True,
                    "show_intelligence": True,
                    "show_focus": True,
                    "show_gantt": True,
                    "show_audit_log": True,
                }
            })
            conn.execute("INSERT INTO dashboard_settings VALUES ('global', ?)", (default_settings,))

        # Seed resource_pool if empty
        pool_count = conn.execute("SELECT COUNT(*) FROM resource_pool").fetchone()[0]
        if pool_count == 0:
            default_pool = [
                ("BA",   "Business Analysis",   2, 8.0),
                ("UIUX", "UI/UX Design",        1, 8.0),
                ("BE",   "Backend Development", 4, 8.0),
                ("FE",   "Frontend Development",3, 8.0),
                ("QC",   "Quality Control",     2, 8.0),
                ("PM",   "Project Management",  1, 8.0),
            ]
            conn.executemany(
                "INSERT OR IGNORE INTO resource_pool VALUES (?,?,?,?)", default_pool
            )

        # schema v1.8 — is_lightweight flag for simple projects
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN is_lightweight INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # schema v1.9 — password support
        try:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        except sqlite3.OperationalError:
            pass

        # schema v1.9 — client name on projects
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN client TEXT")
        except sqlite3.OperationalError:
            pass

        # Seed Firas Saifan account (viewer, implementation only)
        import hashlib
        def _hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()
        firas_pw = _hash_pw("Firas@TSME26")
        firas_email = "firas.saifan@tsmesolutions.com"
        existing_firas = conn.execute(
            "SELECT id FROM users WHERE email=?", (firas_email,)
        ).fetchone()
        if not existing_firas:
            firas_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO users (id, name, username, email, role, project_filter, allowed_projects, active, password_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (firas_id, "Firas Saifan", "firas.saifan", firas_email,
                 "viewer", "implementation", "[]", 1, firas_pw)
            )

        # schema v2.0 — allowed_products on users
        try:
            conn.execute("ALTER TABLE users ADD COLUMN allowed_products TEXT NOT NULL DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass

        # schema v2.1 — predecessor_id + duration_days on tasks for dependency-aware scheduling
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN predecessor_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN duration_days INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # schema v2.2 — stakeholder (requesting party) on projects
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN stakeholder TEXT")
        except sqlite3.OperationalError:
            pass

        # schema v2.25 — sync_locked flag on projects
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN sync_locked INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # schema v2.3 — predecessor relationship type and lag days on tasks
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN predecessor_type TEXT NOT NULL DEFAULT 'FS'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN predecessor_lag INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # schema v2.0 — products + project_products (created via _SCHEMA above)
        # Seed default product list if empty
        prod_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if prod_count == 0:
            default_products = [
                (str(uuid.uuid4()), "Initiatives",          1),
                (str(uuid.uuid4()), "Cubes Classic",        2),
                (str(uuid.uuid4()), "Data Management",      3),
                (str(uuid.uuid4()), "Common Dashboard",     4),
                (str(uuid.uuid4()), "Leadership Dashboard", 5),
                (str(uuid.uuid4()), "ICG-EW",               6),
                (str(uuid.uuid4()), "EW",                   7),
                (str(uuid.uuid4()), "Risk Dashboard",       8),
                (str(uuid.uuid4()), "Appraisals",           9),
                (str(uuid.uuid4()), "Surveys",              10),
                (str(uuid.uuid4()), "360 Dashboard",        11),
            ]
            conn.executemany(
                "INSERT INTO products (id, name, sort_order) VALUES (?,?,?)", default_products
            )

        # schema v2.4 — CUBES 2026 program plan
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS c2026_plan (id TEXT PRIMARY KEY DEFAULT 'main', data TEXT NOT NULL DEFAULT '{}')")
        except Exception:
            pass
        _seed_c2026(conn)

        # schema v2.5 — c2026_access on users (edit | view | no_access)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN c2026_access TEXT NOT NULL DEFAULT 'view'")
        except Exception:
            pass

        # schema v2.6 — allowed_modules per user (JSON array of module keys)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN allowed_modules TEXT NOT NULL DEFAULT '[]'")
        except Exception:
            pass

        # schema v2.7 — plain_password (admin-generated passwords stored as plain text)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN plain_password TEXT")
        except Exception:
            pass

        # schema v2.9 — per-user settings overrides (column visibility etc.)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN settings_override TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            pass

        # schema v2.8 — project extension extra fields
        for stmt in [
            "ALTER TABLE projects ADD COLUMN cr_id TEXT",
            "ALTER TABLE projects ADD COLUMN cr_status TEXT",
            "ALTER TABLE projects ADD COLUMN stage TEXT",
            "ALTER TABLE projects ADD COLUMN show_on_main INTEGER NOT NULL DEFAULT 1",
        ]:
            try:
                conn.execute(stmt)
            except Exception:
                pass

        # schema v2.8 — resource summary table
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_summary (
                    id           TEXT PRIMARY KEY,
                    section      TEXT NOT NULL,
                    project      TEXT NOT NULL DEFAULT '',
                    sub_project  TEXT NOT NULL DEFAULT '',
                    resource_name TEXT NOT NULL DEFAULT '',
                    utilization_pct INTEGER NOT NULL DEFAULT 0,
                    remarks      TEXT NOT NULL DEFAULT '',
                    sort_order   INTEGER NOT NULL DEFAULT 0
                )
            """)
        except Exception:
            pass

        # Seed resource summary if empty
        rs_count = conn.execute("SELECT COUNT(*) FROM resource_summary").fetchone()[0]
        if rs_count == 0:
            rs_rows = [
                # section, project, sub_project, resource_name, utilization_pct, remarks, sort_order
                ("BE", "ICP", "",                        "Tameem",              100, "ICP priority",                  1),
                ("BE", "ICP", "",                        "Sohaib",              100, "ICP priority",                  2),
                ("BE", "ICP", "",                        "BE Resource – TBD",   100, "ICP priority",                  3),
                ("BE", "Audit", "—",                     "—",                     0, "Blocked due to ICP priority",  4),
                ("QC", "ICP", "ICPAula – initiatives",   "Aula",                100, "",                              5),
                ("QC", "APQC", "—",                      "Abdullah Mustafa",    100, "",                              6),
                ("QC", "ICP", "Testing leadership (dashboard)", "Ahmed Fikiri", 100, "",                              7),
                ("QC", "ICP", "Data management",         "Duha",                100, "",                              8),
                ("FE", "ICP", "",                        "FE Resource – TBD",   100, "ICP",                           9),
                ("FE", "ICP", "",                        "FE Resource – TBD",   100, "ICP",                          10),
                ("FE", "ICP", "",                        "FE Resource – TBD",   100, "ICP",                          11),
                ("FE", "EF",  "—",                       "Jaradat",              90, "Active",                       12),
                ("FE", "EF",  "—",                       "Jamal",                90, "Active",                       13),
                ("FE", "APQC","—",                       "Loiy",                100, "Active",                       14),
                ("FE", "APQC","—",                       "Jaradat",              10, "Support",                      15),
                ("FE", "EW Board","—",                   "Anas Al Khamis",      100, "Main owner",                   16),
                ("FE", "EW Board","—",                   "Jamal",                10, "Support",                      17),
            ]
            conn.executemany(
                "INSERT INTO resource_summary (id,section,project,sub_project,resource_name,utilization_pct,remarks,sort_order) VALUES (?,?,?,?,?,?,?,?)",
                [(str(uuid.uuid4()),) + r for r in rs_rows]
            )

        # Seed 8 extension (lightweight) projects if not already present
        _seed_ext_projects(conn)

        # schema v2.11 — project_comments (stakeholder comments in executive summary)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_comments (
                    project_code TEXT PRIMARY KEY,
                    comment      TEXT NOT NULL DEFAULT '',
                    updated_by   TEXT NOT NULL DEFAULT '',
                    updated_at   TEXT NOT NULL DEFAULT ''
                )
            """)
        except Exception:
            pass

        # schema v2.10 — project_progress tracker table
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_progress (
                    id          TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    project_code TEXT,
                    status      TEXT NOT NULL DEFAULT '',
                    ba          TEXT NOT NULL DEFAULT '',
                    uiux        TEXT NOT NULL DEFAULT '',
                    qc          TEXT NOT NULL DEFAULT '',
                    c_classic   TEXT NOT NULL DEFAULT '',
                    fe          TEXT NOT NULL DEFAULT '',
                    be          TEXT NOT NULL DEFAULT '',
                    due_date    TEXT NOT NULL DEFAULT '',
                    start_date  TEXT NOT NULL DEFAULT '',
                    end_date    TEXT NOT NULL DEFAULT '',
                    sort_order  INTEGER NOT NULL DEFAULT 0
                )
            """)
        except Exception:
            pass
        _seed_project_progress(conn)

        # data migration: ensure all projects with a cr_id are flagged as lightweight
        # (older rows may have is_lightweight=0 if seeded before schema v1.8)
        try:
            conn.execute(
                "UPDATE projects SET is_lightweight=1 WHERE cr_id IS NOT NULL AND TRIM(cr_id) != ''"
            )
        except Exception:
            pass

        # schema v1.8 — named resources table
        # (resources table is created via _SCHEMA above; just ensure it exists via executescript)

        # Seed named resources if empty
        res_count = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        if res_count == 0:
            default_resources = [
                (str(uuid.uuid4()), "Ali Abu Fkhideh",  "FE", "Sr. Development",        1),
                (str(uuid.uuid4()), "Mohammad Jaradat", "FE", "Front-End Developer",     1),
                (str(uuid.uuid4()), "Loiy Hindi",       "FE", "Front-End Developer",     1),
                (str(uuid.uuid4()), "Mohammed Al J.",   "FE", "Front-End Developer",     1),
                (str(uuid.uuid4()), "Radwan Alali",     "FE", "Associate Team Lead",     1),
                (str(uuid.uuid4()), "Ahmed Haj Saleh",  "FE", "Front-End Developer",     1),
                (str(uuid.uuid4()), "Karam Obieda",     "FE", "Front-End Developer",     1),
                (str(uuid.uuid4()), "Issam Qatqat",     "FE", "Front-End Developer",     1),
                (str(uuid.uuid4()), "Anas Alkhamis",    "FE", "Front-End Developer",     1),
                (str(uuid.uuid4()), "Yahia Younis",     "BE", "Sr. Back End Development",1),
                (str(uuid.uuid4()), "Suhib Alasmar",    "BE", "Developer, CUBES",        1),
                (str(uuid.uuid4()), "Omar Khader",      "BE", "Developer, CUBES",        1),
                (str(uuid.uuid4()), "Mohammad Yas.",    "BE", "Developer, CUBES",        1),
                (str(uuid.uuid4()), "Mohammad Als.",    "BE", "Backend Developer",       1),
                (str(uuid.uuid4()), "Tameem",           "BE", "Backend Developer",       1),
                (str(uuid.uuid4()), "Hamza Farmawi",    "BA", "Business Analyst",        1),
                (str(uuid.uuid4()), "Hana",             "QC", "QC Engineer",             1),
                (str(uuid.uuid4()), "Hazem",            "UIUX", "UI/UX Designer",        1),
            ]
            conn.executemany(
                "INSERT OR IGNORE INTO resources (id, name, role, title, active) VALUES (?,?,?,?,?)",
                default_resources
            )

        # Ensure at least one admin exists (check for admin specifically, not just any user)
        admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
        if admin_count == 0:
            # Seed the 5 original users if DB was created completely empty (like on Render)
            import hashlib
            def _h(pw): return hashlib.sha256(pw.encode()).hexdigest()
            # (id, name, username, email, role, project_filter, allowed_projects, active,
            #  password_hash, plain_password, allowed_products, c2026_access, allowed_modules)
            default_users = [
                ("u1","Mohammad Hamzeh","mohammad.hamzeh","mohammad.hamzeh@cubesplatform.com",
                 "admin","all","[]",1, _h("W3T1ES#AvZqV"),"W3T1ES#AvZqV","[]","edit","[]"),
                ("u2","Anas Abdelhadi","anas.abdelhadi","anas.abdelhadi@cubesplatform.com",
                 "editor","cubes","[]",1, _h("L5fJ00qfXOWM"),"L5fJ00qfXOWM","[]","view","[]"),
                ("u3","Rawad Khallad","rawad.khallad","rawad.khallad@cubesplatform.com",
                 "editor","cubes","[]",1, _h("DyOSabt0D#rm"),"DyOSabt0D#rm","[]","view","[]"),
                ("u4","Mohammad Younes","mohammad.younes","mohammad.younes@cubesplatform.com",
                 "viewer","all","[]",1, _h("LORS7e71sC$J"),"LORS7e71sC$J","[]","view",'["c2026"]'),
                ("u5","Dima Hamodi","dima.hamodi","dima.hamodi@tsmesolutions.com",
                 "viewer","imp","[]",1, _h("USc2y1ovWQkM"),"USc2y1ovWQkM","[]","view","[]"),
            ]
            conn.executemany(
                "INSERT INTO users (id,name,username,email,role,project_filter,allowed_projects,"
                "active,password_hash,plain_password,allowed_products,c2026_access,allowed_modules) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                default_users
            )


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
