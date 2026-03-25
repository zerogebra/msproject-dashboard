"""Change-request workflow — backed by SQLite via app.database."""
import uuid
from datetime import datetime, timezone
from app.database import get_conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_requests(status: str = None, user: str = None) -> list[dict]:
    with get_conn() as conn:
        if status and user:
            rows = conn.execute(
                "SELECT * FROM change_requests WHERE status=? AND requested_by=? ORDER BY requested_at DESC",
                (status, user)
            ).fetchall()
        elif status:
            rows = conn.execute(
                "SELECT * FROM change_requests WHERE status=? ORDER BY requested_at DESC",
                (status,)
            ).fetchall()
        elif user:
            rows = conn.execute(
                "SELECT * FROM change_requests WHERE requested_by=? ORDER BY requested_at DESC",
                (user,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM change_requests ORDER BY requested_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def count_pending() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM change_requests WHERE status='pending'"
        ).fetchone()
    return row[0] if row else 0


def create_request(project: str, task: str, current_value: float,
                   requested_value: float, reason: str,
                   requested_by: str) -> dict:
    req_id = str(uuid.uuid4())
    now    = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO change_requests
               (id,project,task,current_value,requested_value,reason,requested_by,requested_at,status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (req_id, project, task, current_value,
             requested_value, reason, requested_by, now, "pending")
        )
    return get_request(req_id)


def review_request(request_id: str, action: str,
                   reviewer: str, note: str = "") -> dict | None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE change_requests
               SET status=?, reviewed_by=?, reviewed_at=?, review_note=?
               WHERE id=?""",
            (action, reviewer, _now(), note, request_id)
        )
    return get_request(request_id)


def get_request(request_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM change_requests WHERE id=?", (request_id,)
        ).fetchone()
    return dict(row) if row else None
