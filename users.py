"""User management — backed by SQLite via app.database."""
import json
import uuid
from app.database import get_conn


# ── helpers ──────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    if row is None:
        return None
    d = dict(row)
    d["allowed_projects"] = json.loads(d.get("allowed_projects") or "[]")
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
             allowed_projects: list[str]) -> dict:
    user_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
            (user_id, name, username.lower(), email.lower(),
             role, project_filter, json.dumps(allowed_projects), 1)
        )
    return get_user_by_id(user_id)


def update_user(user_id: str, updates: dict) -> dict | None:
    updates.pop("id", None)
    if not updates:
        return get_user_by_id(user_id)

    # Serialise allowed_projects if present
    if "allowed_projects" in updates:
        updates["allowed_projects"] = json.dumps(updates["allowed_projects"])
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
