"""Overrides and audit log — backed by SQLite via app.database."""
import uuid
from datetime import datetime, timezone
from app.database import get_conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── overrides ────────────────────────────────────────────────────

def load_overrides() -> dict:
    """Return overrides in the legacy nested-dict format consumed by main.py."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM overrides").fetchall()
    result: dict = {"projects": {}}
    for row in rows:
        proj  = row["project"]
        task  = row["task"]
        field = row["field"]
        result["projects"].setdefault(proj, {"tasks": {}})
        result["projects"][proj]["tasks"].setdefault(task, {
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"],
            "original_value": row["original_value"],
        })
        result["projects"][proj]["tasks"][task][field] = row["value"]
    return result


def get_project_overrides(project: str) -> dict:
    """Return {task_name: {pct, original_value, ...}} for one project."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM overrides WHERE project=?", (project,)
        ).fetchall()
    out: dict = {}
    for row in rows:
        task = row["task"]
        out.setdefault(task, {
            "updated_by":     row["updated_by"],
            "updated_at":     row["updated_at"],
            "original_value": row["original_value"],
        })
        out[task][row["field"]] = row["value"]
    return out


def pending_count(project: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM overrides WHERE project=?", (project,)
        ).fetchone()
    return row[0] if row else 0


def save_override(project: str, task: str, field: str,
                  value: float, original_value: float, user: str) -> None:
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO overrides(project,task,field,value,original_value,updated_by,updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(project,task,field) DO UPDATE SET
                 value=excluded.value,
                 updated_by=excluded.updated_by,
                 updated_at=excluded.updated_at""",
            (project, task, field, value, original_value, user, now)
        )
    _append_audit(project, task, field, "override",
                  old_value=original_value, new_value=value, user=user)


def delete_override(project: str, task: str) -> None:
    with get_conn() as conn:
        # Capture original before deleting for audit
        row = conn.execute(
            "SELECT * FROM overrides WHERE project=? AND task=?", (project, task)
        ).fetchone()
        if row:
            conn.execute(
                "DELETE FROM overrides WHERE project=? AND task=?", (project, task)
            )
            _append_audit(project, task, row["field"], "reset",
                          old_value=row["value"],
                          new_value=row["original_value"],
                          user=row["updated_by"])


def clear_project_overrides(project: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM overrides WHERE project=?", (project,))


# ── audit log ────────────────────────────────────────────────────

def _append_audit(project: str, task: str, field: str, action: str,
                  old_value=None, new_value=None, user: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO audit_log(id,timestamp,project,task,field,action,old_value,new_value,user,synced_to_msp)
               VALUES (?,?,?,?,?,?,?,?,?,0)""",
            (str(uuid.uuid4()), _now(), project, task, field,
             action, old_value, new_value, user)
        )


def load_audit_log(project: str = None, limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        if project:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE project=? ORDER BY timestamp DESC LIMIT ?",
                (project, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def mark_audit_synced(project: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE audit_log SET synced_to_msp=1 WHERE project=? AND action='override'",
            (project,)
        )


def append_push_event(project: str, updated_count: int, user: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO audit_log(id,timestamp,project,task,field,action,old_value,new_value,user,synced_to_msp)
               VALUES (?,?,?,?,?,?,?,?,?,1)""",
            (str(uuid.uuid4()), _now(), project, None, None,
             "push_to_msp", None, float(updated_count), user)
        )
