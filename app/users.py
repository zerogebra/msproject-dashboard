"""User management — backed by SQLite via app.database."""
import hashlib
import json
import secrets
import string
import uuid
from app.database import get_conn


def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def verify_password(user: dict, pw: str) -> bool:
    stored = user.get("password_hash")
    if not stored:
        return True   # legacy accounts without password: any input passes
    return stored == hash_password(pw)


# ── helpers ──────────────────────────────────────────────────────

ALL_MODULES = ["project_page", "project_ext", "c2026", "standup", "report", "resources"]

def _row_to_dict(row) -> dict:
    if row is None:
        return None
    d = dict(row)
    d["allowed_projects"] = json.loads(d.get("allowed_projects") or "[]")
    d["allowed_products"]  = json.loads(d.get("allowed_products")  or "[]")
    raw_modules = json.loads(d.get("allowed_modules") or "[]")
    # Empty list means "all modules" (default for existing users)
    d["allowed_modules"] = raw_modules if raw_modules else ALL_MODULES[:]
    d["active"] = bool(d["active"])
    return d


# ── public helpers ───────────────────────────────────────────────

def list_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_user_by_id(user_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _row_to_dict(row)


def find_user(login: str) -> dict | None:
    """Lookup by username or email (case-insensitive)."""
    login = login.strip().lower()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username=? OR email=?", (login, login)
        ).fetchone()
    return _row_to_dict(row)


def add_user(name: str, username: str, email: str,
             role: str, project_filter: str,
             allowed_projects: list[str],
             password: str | None = None,
             allowed_products: list[str] | None = None,
             c2026_access: str = "view",
             allowed_modules: list[str] | None = None) -> dict:
    user_id = str(uuid.uuid4())
    pw_hash = hash_password(password) if password else None
    # Empty list stored as [] means "all modules" (backwards compat)
    modules_json = json.dumps(allowed_modules if allowed_modules is not None else [])
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, name, username, email, role, project_filter, allowed_projects, active, password_hash, plain_password, allowed_products, c2026_access, allowed_modules) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, name, username.lower(), email.lower(),
             role, project_filter, json.dumps(allowed_projects), 1, pw_hash, password,
             json.dumps(allowed_products or []), c2026_access, modules_json)
        )
    return get_user_by_id(user_id)


def update_user(user_id: str, updates: dict) -> dict | None:
    updates.pop("id", None)
    if not updates:
        return get_user_by_id(user_id)

    # Serialise list fields if present
    if "allowed_projects" in updates:
        updates["allowed_projects"] = json.dumps(updates["allowed_projects"])
    if "allowed_products" in updates:
        updates["allowed_products"] = json.dumps(updates["allowed_products"])
    if "allowed_modules" in updates:
        updates["allowed_modules"] = json.dumps(updates["allowed_modules"])
    if "active" in updates:
        updates["active"] = 1 if updates["active"] else 0

    cols = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [user_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {cols} WHERE id=?", vals)
    return get_user_by_id(user_id)


def delete_user(user_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    return cur.rowcount > 0


# ── project access ───────────────────────────────────────────────

def can_access_project(user: dict, project_name: str) -> bool:
    pf = (user.get("project_filter") or "all").lower()
    if pf == "all":
        return True
    if pf == "specific":
        allowed = [p.lower() for p in (user.get("allowed_projects") or [])]
        return project_name.lower() in allowed
    return pf in project_name.lower()


def filter_projects(user: dict, projects: list) -> list:
    return [p for p in projects
            if can_access_project(user, p if isinstance(p, str) else p.get("name", ""))]
