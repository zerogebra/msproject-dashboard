import os
from pathlib import Path
from typing import Optional, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.mpp_reader import MPPReader
from app.overrides import (
    get_project_overrides,
    load_overrides,
    save_override,
    delete_override,
    clear_project_overrides,
    load_audit_log,
    mark_audit_synced,
    append_push_event,
    pending_count,
)
from app.msp_push import is_msp_file_open, push_pct_to_msp
from app.database import init_db
from app.users import (
    list_users, get_user_by_id, find_user,
    add_user, update_user, delete_user,
    can_access_project,
)
from app.change_requests import (
    list_requests, count_pending, create_request,
    review_request, get_request,
)

load_dotenv()

MPP_ROOT = os.getenv("MPP_ROOT", "./mpp")
MPXJ_JAR = os.getenv("MPXJ_JAR", "./libs/mpxj-all.jar")

app = FastAPI(title="PRISM PMO API")
app.mount("/static", StaticFiles(directory="static"), name="static")

reader = MPPReader(MPXJ_JAR)

# Initialise SQLite (creates schema + migrates JSON files on first run)
init_db()


# ── helpers ─────────────────────────────────────────────────────

def _scan_projects():
    """Scan MPP_ROOT and return list of (name, path, ProjectFile)."""
    root = Path(MPP_ROOT)
    results = []
    reader = MPPReader()
    for file in root.rglob("*.mpp"):
        try:
            pf    = reader.load(str(file))
            props = pf.getProjectProperties()
            name  = str(props.getProjectTitle() or file.stem)
            results.append((name, str(file), pf))
        except Exception:
            pass
    return results


def auto_seed_db():
    from app.database import get_conn
    import uuid
    from datetime import datetime, timezone
    
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        if count == 0:
            print("Seeding database from MPP files for the first time...")
            for name, mpp_path, pf in _scan_projects():
                code = name[:3].upper() + str(uuid.uuid4())[:4].upper()
                try: start_date = str(pf.getProjectProperties().getStartDate() or "")
                except: start_date = ""
                try: end_date = str(pf.getProjectProperties().getFinishDate() or "")
                except: end_date = ""
                
                try:
                    conn.execute(
                        "INSERT INTO projects (code, name, mpp_path, health, progress, start_date, end_date, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (code, name, mpp_path, "ontrack", 0.0, start_date, end_date, datetime.now(timezone.utc).isoformat())
                    )
                except Exception as e:
                    print(f"Skipping {name} due to error: {e}")
                    continue
                
                for t in pf.getTasks():
                    if not t or not t.getName(): continue
                    task_title = str(t.getName())
                    try: level = int(t.getOutlineLevel() or 1)
                    except: level = 1
                    try: is_summary = 1 if bool(t.getSummary()) else 0
                    except: is_summary = 0
                    try: is_milestone = 1 if bool(t.getMilestone()) else 0
                    except: is_milestone = 0
                    try: pct = float(t.getPercentageComplete() or 0)
                    except: pct = 0.0
                    try: t_start = str(t.getStart() or "")
                    except: t_start = ""
                    try: t_end = str(t.getFinish() or "")
                    except: t_end = ""
                    
                    conn.execute(
                        "INSERT INTO tasks (id, project_code, title, start_date, end_date, pct, outline_level, is_summary, is_milestone) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), code, task_title, t_start, t_end, pct, level, is_summary, is_milestone)
                    )

auto_seed_db()


# ── routes ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home():
    return Path("static/dashboard_prototype.html").read_text(encoding="utf-8")


@app.get("/api/portfolio")
def portfolio(username: str = "user"):
    from app.database import get_conn
    projects = []

    # Resolve requesting user for project-access filtering
    requesting_user = find_user(username)

    with get_conn() as conn:
        all_projects = conn.execute("SELECT * FROM projects").fetchall()
        for p in all_projects:
            name = p["name"]
            code = p["code"]
            
            # Skip projects the user is not allowed to see
            if requesting_user and not can_access_project(requesting_user, name):
                continue

            tasks = conn.execute("SELECT * FROM tasks WHERE project_code=?", (code,)).fetchall()
            
            items = []
            for t in tasks:
                items.append({
                    "id":           t["id"],
                    "title":        t["title"],
                    "start":        t["start_date"],
                    "end":          t["end_date"],
                    "pct":          t["pct"],
                    "original_pct": t["pct"],
                    "level":        t["outline_level"],
                    "is_summary":   bool(t["is_summary"]),
                    "is_milestone": bool(t["is_milestone"]),
                    "is_critical":  bool(t["is_critical"]),
                    "forecast_end_date": t["forecast_end_date"],
                    "comments":     t["comments"],
                    "overridden":   False,
                    "override_info": None,
                })

            projects.append({
                "code":             code,
                "name":             name,
                "mpp_path":         p["mpp_path"],
                "forecast_end_date": p["forecast_end_date"],
                "pending_overrides": 0,
                "items":            items,
            })

    return {"projects": projects}


# ── override endpoints ───────────────────────────────────────────

class OverrideRequest(BaseModel):
    project:        str
    task:           str
    field:          str  = "pct"
    value:          float
    original_value: float
    user:           str  = "user"


class DeleteOverrideRequest(BaseModel):
    project: str
    task:    str


@app.post("/api/override")
def set_override(req: OverrideRequest):
    save_override(
        req.project, req.task, req.field,
        req.value, req.original_value, req.user
    )
    return {"status": "ok", "project": req.project, "task": req.task,
            "pending": pending_count(req.project)}


@app.delete("/api/override")
def remove_override(req: DeleteOverrideRequest):
    delete_override(req.project, req.task)
    return {"status": "ok", "pending": pending_count(req.project)}


# ── push to MS Project ──────────────────────────────────────────

@app.post("/api/push/{project_name:path}")
def push_project(project_name: str, user: str = "user"):
    # Find the .mpp path
    mpp_path = None
    for name, path, _ in _scan_projects():
        if name == project_name:
            mpp_path = path
            break

    if not mpp_path:
        raise HTTPException(404, f"Project '{project_name}' not found")

    overrides = get_project_overrides(project_name)
    if not overrides:
        return {"status": "ok", "message": "No pending overrides to push"}

    if is_msp_file_open(mpp_path):
        raise HTTPException(
            409,
            "MS Project file is currently open. "
            "Please close it in MS Project and try again."
        )

    result = push_pct_to_msp(mpp_path, overrides)

    if result["success"]:
        clear_project_overrides(project_name)
        mark_audit_synced(project_name)
        append_push_event(project_name, result["updated"], user)
        return {
            "status":  "ok",
            "updated": result["updated"],
            "errors":  result["errors"],
            "message": f"Pushed {result['updated']} task(s) to MS Project successfully.",
        }
    else:
        raise HTTPException(500, detail={
            "message": "Push failed",
            "errors":  result["errors"],
        })


# ── audit log ───────────────────────────────────────────────────

@app.get("/api/audit")
def get_audit(project: Optional[str] = None, limit: int = 200):
    log = load_audit_log(project=project, limit=limit)
    return {"entries": log, "total": len(log)}


@app.delete("/api/audit")
def clear_audit(caller_id: str = ""):
    from app.users import get_user_by_id
    caller = get_user_by_id(caller_id) if caller_id else None
    if not caller or caller.get("role") != "admin":
        raise HTTPException(403, "Only admins can clear the audit log.")
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM audit_log")
    return {"message": "Audit log cleared."}


# ── authentication ───────────────────────────────────────────────

class LoginRequest(BaseModel):
    login: str   # username or email

@app.post("/api/login")
def login(req: LoginRequest):
    user = find_user(req.login)
    if not user:
        raise HTTPException(404, "User not found. Check your username or email.")
    if not user.get("active", True):
        raise HTTPException(403, "Your account has been deactivated. Contact the admin.")
    # Return safe user info (no sensitive fields)
    return {
        "id":             user["id"],
        "name":           user["name"],
        "username":       user["username"],
        "email":          user["email"],
        "role":           user["role"],
        "project_filter": user.get("project_filter", "all"),
        "allowed_projects": user.get("allowed_projects", []),
    }


# ── user management (admin only) ────────────────────────────────

def _require_admin(caller_id: str):
    caller = get_user_by_id(caller_id)
    if not caller or caller.get("role") != "admin":
        raise HTTPException(403, "Admin access required.")
    return caller


@app.get("/api/users")
def get_users(caller_id: str):
    _require_admin(caller_id)
    return {"users": list_users()}


class UserCreateRequest(BaseModel):
    name:             str
    username:         str
    email:            str
    role:             str   = "viewer"    # admin | editor | viewer
    project_filter:   str   = "all"       # all | cubes | implementation | specific
    allowed_projects: List[str] = []

@app.post("/api/users")
def create_user(req: UserCreateRequest, caller_id: str):
    _require_admin(caller_id)
    if find_user(req.username) or find_user(req.email):
        raise HTTPException(409, "A user with that username or email already exists.")
    user = add_user(req.name, req.username, req.email,
                    req.role, req.project_filter, req.allowed_projects)
    return {"status": "ok", "user": user}


class UserUpdateRequest(BaseModel):
    name:             Optional[str]       = None
    username:         Optional[str]       = None
    email:            Optional[str]       = None
    role:             Optional[str]       = None
    project_filter:   Optional[str]       = None
    allowed_projects: Optional[List[str]] = None
    active:           Optional[bool]      = None

@app.put("/api/users/{user_id}")
def edit_user(user_id: str, req: UserUpdateRequest, caller_id: str):
    _require_admin(caller_id)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    updated = update_user(user_id, updates)
    if not updated:
        raise HTTPException(404, "User not found.")
    return {"status": "ok", "user": updated}


@app.delete("/api/users/{user_id}")
def remove_user(user_id: str, caller_id: str):
    _require_admin(caller_id)
    if not delete_user(user_id):
        raise HTTPException(404, "User not found.")
    return {"status": "ok"}


# ── change-request workflow ──────────────────────────────────────

class ChangeRequestCreate(BaseModel):
    project:         str
    task:            str
    current_value:   float
    requested_value: float
    reason:          str
    requested_by:    str   # username of the requester

@app.post("/api/change-requests")
def submit_change_request(req: ChangeRequestCreate):
    cr = create_request(
        req.project, req.task, req.current_value,
        req.requested_value, req.reason, req.requested_by,
    )
    return {"status": "ok", "request": cr}


@app.get("/api/change-requests")
def get_change_requests(caller_id: str, status: Optional[str] = None):
    caller = get_user_by_id(caller_id)
    if not caller:
        raise HTTPException(403, "Unknown caller.")
    if caller["role"] == "admin":
        reqs = list_requests(status=status)
    else:
        reqs = list_requests(status=status, user=caller["username"])
    return {"requests": reqs, "pending_count": count_pending()}


@app.get("/api/change-requests/pending-count")
def pending_change_requests():
    return {"pending_count": count_pending()}


class ReviewRequest(BaseModel):
    action:      str   # "approved" or "rejected"
    reviewer:    str   # admin username
    review_note: str = ""

@app.post("/api/change-requests/{request_id}/review")
def review_change_request(request_id: str, req: ReviewRequest, caller_id: str):
    _require_admin(caller_id)
    if req.action not in ("approved", "rejected"):
        raise HTTPException(400, "action must be 'approved' or 'rejected'.")
    cr = review_request(request_id, req.action, req.reviewer, req.review_note)
    if not cr:
        raise HTTPException(404, "Change request not found.")

    # If approved, apply the override immediately
    if req.action == "approved":
        save_override(
            cr["project"], cr["task"], "pct",
            cr["requested_value"], cr["current_value"], req.reviewer,
        )

    return {"status": "ok", "request": cr}


# ── Standalone Projects & Tasks API ──────────────────────────────

from fastapi import File, UploadFile
import uuid
import shutil
from datetime import datetime, timezone

class ProjectCreate(BaseModel):
    name: str

@app.post("/api/projects")
def create_project(req: ProjectCreate, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    code = req.name[:3].upper() + str(uuid.uuid4())[:4].upper()
    now_iso = datetime.now(timezone.utc).isoformat()
    
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (code, name, mpp_path, health, progress, start_date, end_date, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (code, req.name, "", "ontrack", 0.0, now_iso, now_iso, now_iso)
        )
    return {"status": "ok", "code": code}


@app.post("/api/projects/{code}/clone")
def clone_project(code: str, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    new_code = code[:3] + str(uuid.uuid4())[:4].upper()
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        # Clone project
        p = conn.execute("SELECT * FROM projects WHERE code=?", (code,)).fetchone()
        if not p:
            raise HTTPException(404, "Original project not found.")
            
        new_name = p["name"] + " (Copy)"
        conn.execute(
            "INSERT INTO projects (code, name, mpp_path, health, progress, start_date, end_date, forecast_end_date, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (new_code, new_name, p["mpp_path"], p["health"], p["progress"], p["start_date"], p["end_date"], p["forecast_end_date"], now_iso)
        )
        
        # Clone tasks
        tasks = conn.execute("SELECT * FROM tasks WHERE project_code=?", (code,)).fetchall()
        for t in tasks:
            conn.execute(
                "INSERT INTO tasks (id, project_code, title, start_date, end_date, pct, outline_level, is_summary, is_milestone, is_critical, forecast_end_date, comments) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), new_code, t["title"], t["start_date"], t["end_date"], t["pct"], t["outline_level"], t["is_summary"], t["is_milestone"], t["is_critical"], t["forecast_end_date"], t["comments"])
            )
            
    return {"status": "ok", "code": new_code}


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    pct: Optional[float] = None
    is_critical: Optional[int] = None
    forecast_end_date: Optional[str] = None
    comments: Optional[str] = None

@app.put("/api/tasks/{task_id}")
def update_task(task_id: str, req: TaskUpdate, caller_id: str):
    # Depending on your architecture, admins/editors can modify:
    caller = get_user_by_id(caller_id)
    if not caller or caller.get("role") == "viewer":
        raise HTTPException(403, "Editor/Admin access required.")
        
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return {"status": "ok"}
        
    from app.database import get_conn
    cols = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [task_id]
    
    with get_conn() as conn:
        conn.execute(f"UPDATE tasks SET {cols} WHERE id=?", vals)
        
    return {"status": "ok"}


@app.post("/api/upload-mpp")
async def upload_mpp(file: UploadFile = File(...), caller_id: str = ""):
    if caller_id:
        _require_admin(caller_id)
    
    if not file.filename.endswith(".mpp"):
        raise HTTPException(400, "File must be an MPP.")
        
    root = Path(MPP_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    file_path = root / file.filename
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Read the new file mapping directly to SQLite
    from app.database import get_conn
    try:
        pf    = reader.load(str(file_path))
        props = pf.getProjectProperties()
        name  = str(props.getProjectTitle() or file_path.stem)
        code = name[:3].upper() + str(uuid.uuid4())[:4].upper()
        now_iso = datetime.now(timezone.utc).isoformat()
        
        try: start_date = str(props.getStartDate() or "")
        except: start_date = ""
        try: end_date = str(props.getFinishDate() or "")
        except: end_date = ""

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO projects (code, name, mpp_path, health, progress, start_date, end_date, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (code, name, str(file_path), "ontrack", 0.0, start_date, end_date, now_iso)
            )
            
            for t in pf.getTasks():
                if not t or not t.getName(): continue
                task_title = str(t.getName())
                try: level = int(t.getOutlineLevel() or 1)
                except: level = 1
                try: is_summary = 1 if bool(t.getSummary()) else 0
                except: is_summary = 0
                try: is_milestone = 1 if bool(t.getMilestone()) else 0
                except: is_milestone = 0
                try: pct = float(t.getPercentageComplete() or 0)
                except: pct = 0.0
                try: t_start = str(t.getStart() or "")
                except: t_start = ""
                try: t_end = str(t.getFinish() or "")
                except: t_end = ""
                
                conn.execute(
                    "INSERT INTO tasks (id, project_code, title, start_date, end_date, pct, outline_level, is_summary, is_milestone) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), code, task_title, t_start, t_end, pct, level, is_summary, is_milestone)
                )
    except Exception as e:
        raise HTTPException(500, f"Error processing MPP: {str(e)}")
        
    return {"status": "ok", "message": "MPP Imported"}


# ── Refresh / Sync from MPP files ─────────────────────────────────

@app.post("/api/refresh-from-mpp")
def refresh_from_mpp(caller_id: str = ""):
    """Re-read all .mpp files from MPP_ROOT and upsert into the DB.

    For each .mpp file:
      - If project (matched by name) already exists → delete its tasks
        and re-insert fresh ones from the latest file.
      - If project is new → insert project + tasks.
    Manual overrides in overrides.json are NOT touched.
    """
    if caller_id:
        _require_admin(caller_id)

    from app.database import get_conn

    projects_updated = 0
    projects_added = 0
    tasks_total = 0
    errors = []

    for name, mpp_path, pf in _scan_projects():
        try:
            props = pf.getProjectProperties()
            try: start_date = str(props.getStartDate() or "")
            except: start_date = ""
            try: end_date = str(props.getFinishDate() or "")
            except: end_date = ""

            with get_conn() as conn:
                existing = conn.execute(
                    "SELECT code FROM projects WHERE name=?", (name,)
                ).fetchone()

                if existing:
                    code = existing["code"]
                    # Update project dates
                    conn.execute(
                        "UPDATE projects SET mpp_path=?, start_date=?, end_date=? WHERE code=?",
                        (mpp_path, start_date, end_date, code)
                    )
                    # Remove old tasks and re-insert
                    conn.execute("DELETE FROM tasks WHERE project_code=?", (code,))
                    projects_updated += 1
                else:
                    code = name[:3].upper() + str(uuid.uuid4())[:4].upper()
                    now_iso = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        "INSERT INTO projects (code, name, mpp_path, health, progress, start_date, end_date, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (code, name, mpp_path, "ontrack", 0.0, start_date, end_date, now_iso)
                    )
                    projects_added += 1

                # Insert fresh tasks
                for t in pf.getTasks():
                    if not t or not t.getName(): continue
                    task_title = str(t.getName())
                    try: level = int(t.getOutlineLevel() or 1)
                    except: level = 1
                    try: is_summary = 1 if bool(t.getSummary()) else 0
                    except: is_summary = 0
                    try: is_milestone = 1 if bool(t.getMilestone()) else 0
                    except: is_milestone = 0
                    try: pct = float(t.getPercentageComplete() or 0)
                    except: pct = 0.0
                    try: t_start = str(t.getStart() or "")
                    except: t_start = ""
                    try: t_end = str(t.getFinish() or "")
                    except: t_end = ""

                    conn.execute(
                        "INSERT INTO tasks (id, project_code, title, start_date, end_date, pct, outline_level, is_summary, is_milestone) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), code, task_title, t_start, t_end, pct, level, is_summary, is_milestone)
                    )
                    tasks_total += 1

        except Exception as e:
            errors.append(f"{name}: {str(e)}")

    return {
        "status": "ok",
        "projects_updated": projects_updated,
        "projects_added": projects_added,
        "tasks_imported": tasks_total,
        "errors": errors,
        "message": f"Sync complete — {projects_updated} updated, {projects_added} new, {tasks_total} tasks imported."
    }
