import json
import os
from pathlib import Path
from typing import Optional, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Optional Java/MPXJ (only available on local machine with JRE installed) ──
_JAVA_AVAILABLE = False
try:
    from app.mpp_reader import MPPReader
    from app.msp_push import is_msp_file_open, push_pct_to_msp
    _JAVA_AVAILABLE = True
except Exception:
    pass  # Render / cloud: no Java — MPP sync endpoints will be disabled gracefully

from app.overrides import (
    get_project_overrides,
    save_override,
    delete_override,
    clear_project_overrides,
    load_audit_log,
    mark_audit_synced,
    append_push_event,
    pending_count,
)
from app.database import init_db
from app.users import (
    list_users, get_user_by_id, find_user,
    add_user, update_user, delete_user,
    can_access_project, hash_password, generate_password, verify_password,
)
from app.change_requests import (
    list_requests, count_pending, create_request,
    review_request, get_request,
)

load_dotenv()

MPP_ROOT = os.getenv("MPP_ROOT", "./mpp")
MPXJ_JAR = os.getenv("MPXJ_JAR", "./libs/mpxj-all.jar")
BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="CUBES Project Monitoring API")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if _JAVA_AVAILABLE:
    reader = MPPReader(MPXJ_JAR)
else:
    reader = None

# Initialise SQLite (creates schema + migrates JSON files on first run)
init_db()


# ── helpers ─────────────────────────────────────────────────────

def _scan_projects():
    """Scan MPP_ROOT and return list of (name, path, ProjectFile). Returns [] if Java unavailable."""
    if not _JAVA_AVAILABLE:
        return []
    root = Path(MPP_ROOT)
    results = []
    local_reader = MPPReader()
    for file in root.rglob("*.mpp"):
        try:
            pf    = local_reader.load(str(file))
            props = pf.getProjectProperties()
            name  = str(props.getProjectTitle() or file.stem)
            results.append((name, str(file), pf))
        except Exception:
            pass
    return results


def auto_seed_db():
    """Seed projects/tasks from MPP files on first run (local only)."""
    if not _JAVA_AVAILABLE:
        return  # On Render: no Java — data comes from committed SQLite DB
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
    return (STATIC_DIR / "dashboard_prototype.html").read_text(encoding="utf-8")


@app.get("/api/portfolio")
def portfolio(username: str = "user"):
    from app.database import get_conn
    projects = []

    # Resolve requesting user for project-access filtering
    requesting_user = find_user(username)

    with get_conn() as conn:
        all_projects = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        for p in all_projects:
            name = p["name"]
            code = p["code"]

            # Skip lightweight (extension) projects that are flagged off from main page
            is_lw = bool(p["is_lightweight"]) if "is_lightweight" in p.keys() else False
            show_on_main = p["show_on_main"] if "show_on_main" in p.keys() else 1
            if is_lw:
                continue

            # Skip projects the user is not allowed to see
            if requesting_user and not can_access_project(requesting_user, name):
                continue

            tasks = conn.execute("SELECT * FROM tasks WHERE project_code=? ORDER BY rowid", (code,)).fetchall()

            # Load any in-app overrides so the UI reflects edits made in the dashboard
            proj_overrides = get_project_overrides(name)

            items = []
            for t in tasks:
                task_title   = t["title"]
                raw_pct      = t["pct"]
                override     = proj_overrides.get(task_title, {})
                display_pct  = override.get("pct", raw_pct)
                overridden   = "pct" in override
                items.append({
                    "id":                 t["id"],
                    "title":              task_title,
                    "start":              t["start_date"],
                    "end":                t["end_date"],
                    "pct":                display_pct,
                    "original_pct":       raw_pct,
                    "level":              t["outline_level"],
                    "is_summary":         bool(t["is_summary"]),
                    "is_milestone":       bool(t["is_milestone"]),
                    "is_critical":        bool(t["is_critical"]),
                    "forecast_end_date":  t["forecast_end_date"],
                    "extended_days":      t["extended_days"] or 0,
                    "actual_start_date":  t["actual_start_date"],
                    "actual_finish_date": t["actual_finish_date"],
                    "comments":           t["comments"],
                    "predecessor_id":     t["predecessor_id"],
                    "predecessor_type":   t["predecessor_type"] if "predecessor_type" in t.keys() else "FS",
                    "predecessor_lag":    t["predecessor_lag"]  if "predecessor_lag"  in t.keys() else 0,
                    "duration_days":      t["duration_days"] if "duration_days" in t.keys() else 0,
                    "overridden":         overridden,
                    "override_info": {
                        "updated_by": override.get("updated_by", ""),
                        "updated_at": override.get("updated_at", ""),
                    } if overridden else None,
                })

            projects.append({
                "code":             code,
                "name":             name,
                "mpp_path":         p["mpp_path"],
                "team_type":        p["team_type"] or "cubes",
                "forecast_end_date": p["forecast_end_date"],
                "pending_overrides": pending_count(name),
                "stakeholder":      p["stakeholder"] if "stakeholder" in p.keys() else None,
                "sync_locked":      bool(p["sync_locked"]) if "sync_locked" in p.keys() else False,
                "requested_by":          p["requested_by"] if "requested_by" in p.keys() else None,
                "exec_additional_days":  p["exec_additional_days"] if "exec_additional_days" in p.keys() else None,
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
    if not _JAVA_AVAILABLE:
        raise HTTPException(501, "MS Project push is only available on the local machine with Java installed.")
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


# ── sync lock ───────────────────────────────────────────────────

class SyncLockRequest(BaseModel):
    caller_id: str
    locked: bool

@app.post("/api/projects/{code}/sync-lock")
def toggle_sync_lock(code: str, req: SyncLockRequest):
    from app.database import get_conn
    caller = find_user(req.caller_id)
    if not caller or caller.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    with get_conn() as conn:
        conn.execute("UPDATE projects SET sync_locked=? WHERE code=?", (1 if req.locked else 0, code))
    return {"status": "ok", "code": code, "sync_locked": req.locked}


# ── audit log ───────────────────────────────────────────────────

@app.get("/api/audit")
def get_audit(project: Optional[str] = None, limit: int = 200):
    log = load_audit_log(project=project, limit=limit)
    return {"entries": log, "total": len(log)}


@app.delete("/api/audit")
def clear_audit(caller_id: str = ""):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM audit_log")
    return {"message": "Audit log cleared."}


# ── authentication ───────────────────────────────────────────────

class LoginRequest(BaseModel):
    login:    str            # username or email
    password: str = ""       # optional — empty means no-password account

@app.post("/api/login")
def login(req: LoginRequest):
    user = find_user(req.login)
    if not user:
        raise HTTPException(404, "User not found. Check your username or email.")
    if not user.get("active", True):
        raise HTTPException(403, "Your account has been deactivated. Contact the admin.")
    if not verify_password(user, req.password):
        raise HTTPException(401, "Incorrect password.")
    # Return safe user info (no sensitive fields)
    return {
        "id":                user["id"],
        "name":              user["name"],
        "username":          user["username"],
        "email":             user["email"],
        "role":              user["role"],
        "project_filter":    user.get("project_filter", "all"),
        "allowed_projects":  user.get("allowed_projects", []),
        "allowed_products":  user.get("allowed_products", []),
        "c2026_access":      user.get("c2026_access", "view"),
        "allowed_modules":   user.get("allowed_modules", []),
        "settings_override": user.get("settings_override", {}),
        "has_password":      bool(user.get("password_hash")),
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
    allowed_products: List[str] = []
    c2026_access:     str   = "view"      # edit | view | no_access
    allowed_modules:  List[str] = []      # [] = all modules; specific list = restricted
    password:         Optional[str] = None   # if omitted, auto-generate

@app.post("/api/users")
def create_user(req: UserCreateRequest, caller_id: str):
    _require_admin(caller_id)
    if find_user(req.username) or find_user(req.email):
        raise HTTPException(409, "A user with that username or email already exists.")
    plain_pw = req.password if req.password else generate_password()
    user = add_user(req.name, req.username, req.email,
                    req.role, req.project_filter, req.allowed_projects,
                    password=plain_pw, allowed_products=req.allowed_products,
                    c2026_access=req.c2026_access,
                    allowed_modules=req.allowed_modules)
    return {"status": "ok", "user": user, "generated_password": plain_pw}


class UserUpdateRequest(BaseModel):
    name:              Optional[str]       = None
    username:          Optional[str]       = None
    email:             Optional[str]       = None
    role:              Optional[str]       = None
    project_filter:    Optional[str]       = None
    allowed_projects:  Optional[List[str]] = None
    allowed_products:  Optional[List[str]] = None
    c2026_access:      Optional[str]       = None   # edit | view | no_access
    allowed_modules:   Optional[List[str]] = None   # [] = all; specific list = restricted
    active:            Optional[bool]      = None
    password:          Optional[str]       = None   # set to reset password
    settings_override: Optional[dict]      = None   # per-user column/panel visibility overrides

@app.post("/api/users/{user_id}/reset-password")
def reset_password(user_id: str, caller_id: str):
    """Generate a new random password for the user, save it permanently in DB."""
    _require_admin(caller_id)
    plain_pw = generate_password()
    update_user(user_id, {"password_hash": hash_password(plain_pw), "plain_password": plain_pw})
    return {"status": "ok", "generated_password": plain_pw}


@app.post("/api/users/bulk-generate-passwords")
def bulk_generate_passwords(caller_id: str):
    """Generate and permanently save passwords for all users who don't have one yet."""
    _require_admin(caller_id)
    users = list_users()
    results = []
    for u in users:
        if not u.get("plain_password"):
            plain_pw = generate_password()
            update_user(u["id"], {"password_hash": hash_password(plain_pw), "plain_password": plain_pw})
            results.append({"id": u["id"], "name": u["name"], "generated_password": plain_pw})
    return {"status": "ok", "generated": results}

@app.put("/api/users/{user_id}")
def edit_user(user_id: str, req: UserUpdateRequest, caller_id: str):
    _require_admin(caller_id)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if "password" in updates:
        plain_pw = updates.pop("password")
        updates["password_hash"] = hash_password(plain_pw)
        updates["plain_password"] = plain_pw
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


@app.delete("/api/projects/{code}")
def delete_project(code: str, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        p = conn.execute("SELECT code, name FROM projects WHERE code=?", (code,)).fetchone()
        if not p:
            raise HTTPException(404, "Project not found.")
        proj_name = p["name"]
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM tasks WHERE project_code=?", (code,))
        conn.execute("DELETE FROM project_allocations WHERE project_code=?", (code,))
        conn.execute("DELETE FROM named_allocations WHERE project_code=?", (code,))
        conn.execute("DELETE FROM project_products WHERE project_code=?", (code,))
        conn.execute("DELETE FROM overrides WHERE project=?", (proj_name,))
        conn.execute("DELETE FROM audit_log WHERE project=?", (proj_name,))
        conn.execute("DELETE FROM projects WHERE code=?", (code,))
        conn.execute("PRAGMA foreign_keys=ON")
    return {"status": "ok"}


class ProjectRename(BaseModel):
    name: str

@app.put("/api/projects/{code}/rename")
def rename_project(code: str, req: ProjectRename, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        p = conn.execute("SELECT code FROM projects WHERE code=?", (code,)).fetchone()
        if not p:
            raise HTTPException(404, "Project not found.")
        conn.execute("UPDATE projects SET name=? WHERE code=?", (req.name, code))
    return {"status": "ok"}


class ProjectRequestedByUpdate(BaseModel):
    requested_by: str

@app.put("/api/projects/{code}/requested_by")
def update_project_requested_by(code: str, req: ProjectRequestedByUpdate, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        p = conn.execute("SELECT code FROM projects WHERE code=?", (code,)).fetchone()
        if not p:
            raise HTTPException(404, "Project not found.")
        conn.execute("UPDATE projects SET requested_by=? WHERE code=?", (req.requested_by.strip() or None, code))
    return {"status": "ok"}


class ProjectExecSummaryUpdate(BaseModel):
    exec_additional_days: Optional[int] = None

@app.put("/api/projects/{code}/exec-summary")
def update_project_exec_summary(code: str, req: ProjectExecSummaryUpdate, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        p = conn.execute("SELECT code FROM projects WHERE code=?", (code,)).fetchone()
        if not p:
            raise HTTPException(404, "Project not found.")
        conn.execute(
            "UPDATE projects SET exec_additional_days=? WHERE code=?",
            (req.exec_additional_days, code)
        )
    return {"status": "ok"}


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    pct: Optional[float] = None
    is_critical: Optional[int] = None
    forecast_end_date: Optional[str] = None
    extended_days: Optional[int] = None
    actual_start_date: Optional[str] = None
    actual_finish_date: Optional[str] = None
    comments: Optional[str] = None
    predecessor_id: Optional[str] = None
    predecessor_type: Optional[str] = None
    predecessor_lag: Optional[int] = None
    duration_days: Optional[int] = None

@app.put("/api/tasks/{task_id}")
def update_task(task_id: str, req: TaskUpdate, caller_id: str):
    caller = get_user_by_id(caller_id)
    if not caller or caller.get("role") == "viewer":
        raise HTTPException(403, "Editor/Admin access required.")

    # Use exclude_unset so explicitly-sent null values clear the DB field
    updates = req.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "ok"}

    from app.database import get_conn
    cols = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [task_id]

    with get_conn() as conn:
        conn.execute(f"UPDATE tasks SET {cols} WHERE id=?", vals)

    return {"status": "ok"}


# ── WIZARD PROJECT CREATION ──────────────────────────────────────────────────

class WizardTask(BaseModel):
    title: str
    outline_level: int = 3
    is_summary: bool = False
    is_milestone: bool = False
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    pct: float = 0.0
    role: Optional[str] = None
    resources: int = 1
    duration_days: float = 0.0
    predecessor: Optional[str] = None

class WizardProjectCreate(BaseModel):
    name: str
    start_date: str
    priority: str = "P3"
    team_type: str = "cubes"       # 'cubes' | 'implementation'
    hours_per_day: float = 8.0
    tasks: List[WizardTask]
    allocations: Optional[dict] = {}  # {role: count}
    caller_id: str
    client: Optional[str] = None
    stakeholder: Optional[str] = None

def _insert_wizard_project(conn, req: WizardProjectCreate, code: str, now_iso: str):
    """Insert a single wizard project + tasks + allocations into an open connection."""
    task_dates = [t.start_date for t in req.tasks if t.start_date] + \
                 [t.end_date   for t in req.tasks if t.end_date]
    proj_start = min(task_dates) if task_dates else req.start_date
    proj_end   = max(task_dates) if task_dates else req.start_date

    conn.execute(
        "INSERT INTO projects (code, name, mpp_path, health, progress, start_date, end_date, created_at, priority, team_type, client, stakeholder) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (code, req.name, "", "ontrack", 0.0, proj_start, proj_end, now_iso, req.priority, req.team_type,
         req.client or "", req.stakeholder or "")
    )
    conn.execute(
        "INSERT INTO tasks (id, project_code, title, start_date, end_date, pct, outline_level, is_summary, is_milestone, is_critical) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), code, req.name, proj_start, proj_end, 0.0, 1, 1, 0, 0)
    )
    # First pass: insert all tasks and record their IDs by title
    task_id_map: dict[str, str] = {}  # title -> task_id
    task_rows = []
    for t in req.tasks:
        tid = str(uuid.uuid4())
        task_id_map[t.title] = tid
        task_rows.append((tid, t))
        conn.execute(
            "INSERT INTO tasks (id, project_code, title, start_date, end_date, pct, outline_level, is_summary, is_milestone, is_critical, duration_days) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tid, code, t.title,
             t.start_date or proj_start, t.end_date or proj_end,
             t.pct, t.outline_level, 1 if t.is_summary else 0,
             1 if t.is_milestone else 0, 0, float(t.duration_days or 0))
        )
    # Second pass: resolve predecessor text to actual task IDs
    for tid, t in task_rows:
        if not t.predecessor:
            continue
        # predecessor text may reference one or more titles separated by commas or "+"
        # Find the best single match among known task titles (pick longest match)
        pred_text = t.predecessor
        best_match_id = None
        best_match_len = 0
        for title, mapped_id in task_id_map.items():
            if title.lower() in pred_text.lower() and len(title) > best_match_len:
                best_match_id = mapped_id
                best_match_len = len(title)
        if best_match_id:
            conn.execute("UPDATE tasks SET predecessor_id=? WHERE id=?", (best_match_id, tid))

    for role, count in (req.allocations or {}).items():
        conn.execute(
            "INSERT OR REPLACE INTO project_allocations (id, project_code, role, assigned_count) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), code, role, int(count))
        )

@app.post("/api/projects/wizard")
def create_wizard_project(req: WizardProjectCreate):
    _require_admin(req.caller_id)
    from app.database import get_conn
    from datetime import timezone

    code = req.name[:4].upper().replace(" ", "") + str(uuid.uuid4())[:4].upper()
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        _insert_wizard_project(conn, req, code, now_iso)

    return {"status": "ok", "code": code, "name": req.name}


# ── RESOURCE HUB ─────────────────────────────────────────────────────────────

@app.get("/api/resource-hub")
def get_resource_hub():
    from app.database import get_conn
    with get_conn() as conn:
        pool = [dict(r) for r in conn.execute("SELECT * FROM resource_pool ORDER BY role").fetchall()]
        projects_raw = conn.execute(
            "SELECT code, name, priority, start_date, end_date, team_type FROM projects ORDER BY name"
        ).fetchall()
        allocs_raw = conn.execute("SELECT * FROM project_allocations").fetchall()
        named_allocs_raw = conn.execute(
            """SELECT na.project_code, na.resource_id, na.allocation_pct, r.name, r.role, r.title
               FROM named_allocations na JOIN resources r ON r.id = na.resource_id"""
        ).fetchall()

    allocs_by_proj = {}
    for a in allocs_raw:
        allocs_by_proj.setdefault(a["project_code"], {})[a["role"]] = a["assigned_count"]

    named_by_proj = {}
    for na in named_allocs_raw:
        named_by_proj.setdefault(na["project_code"], []).append({
            "resource_id":    na["resource_id"],
            "name":           na["name"],
            "role":           na["role"],
            "title":          na["title"],
            "allocation_pct": na["allocation_pct"],
        })

    projects_out = []
    for p in projects_raw:
        projects_out.append({
            "code":             p["code"],
            "name":             p["name"],
            "priority":         p["priority"] or "P3",
            "team_type":        p["team_type"] or "cubes",
            "start_date":       p["start_date"],
            "end_date":         p["end_date"],
            "allocations":      allocs_by_proj.get(p["code"], {}),
            "named_allocations": named_by_proj.get(p["code"], []),
        })

    # Compute totals per role — overall and per team group
    roles = [r["role"] for r in pool]
    utilization  = {role: 0 for role in roles}
    util_cubes   = {role: 0 for role in roles}
    util_impl    = {role: 0 for role in roles}
    for p in projects_out:
        target = util_cubes if p["team_type"] == "cubes" else util_impl
        for role, cnt in p["allocations"].items():
            if role in utilization:
                utilization[role] = utilization.get(role, 0) + cnt
                target[role] = target.get(role, 0) + cnt

    with get_conn() as conn:
        all_resources = [dict(r) for r in conn.execute("SELECT * FROM resources ORDER BY role, name").fetchall()]

    return {
        "pool":        pool,
        "projects":    projects_out,
        "utilization": utilization,
        "util_cubes":  util_cubes,
        "util_impl":   util_impl,
        "resources":   all_resources,
    }


class ResourcePoolUpdate(BaseModel):
    role: str
    total_count: int
    hours_per_day: float = 8.0

@app.put("/api/resource-pool")
def update_resource_pool(req: ResourcePoolUpdate, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "UPDATE resource_pool SET total_count=?, hours_per_day=? WHERE role=?",
            (req.total_count, req.hours_per_day, req.role)
        )
    return {"status": "ok"}


class AllocationUpdate(BaseModel):
    role: str
    assigned_count: int

@app.put("/api/projects/{code}/allocation")
def update_project_allocation(code: str, req: AllocationUpdate, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO project_allocations (id, project_code, role, assigned_count) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), code, req.role, req.assigned_count)
        )
    return {"status": "ok"}


# ── PROJECT EXTENSION (lightweight projects) ─────────────────────────────────

@app.get("/api/project-extension")
def get_project_extension():
    from app.database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM projects WHERE is_lightweight=1 ORDER BY name"
        ).fetchall()
        # Attach product names per project
        result = []
        for p in rows:
            prod_rows = conn.execute(
                """SELECT pr.id, pr.name FROM project_products pp
                   JOIN products pr ON pr.id = pp.product_id
                   WHERE pp.project_code=? ORDER BY pr.sort_order""",
                (p["code"],)
            ).fetchall()
            d = dict(p)
            d["products"] = [{"id": r["id"], "name": r["name"]} for r in prod_rows]
            d["cr_id"]       = p["cr_id"]       if "cr_id"       in p.keys() else ""
            d["cr_status"]   = p["cr_status"]   if "cr_status"   in p.keys() else ""
            d["stage"]       = p["stage"]       if "stage"       in p.keys() else ""
            d["show_on_main"] = bool(p["show_on_main"]) if "show_on_main" in p.keys() else True
            result.append(d)
    return {"projects": result}


class LightweightProjectCreate(BaseModel):
    name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    progress: float = 0.0
    team_type: str = "cubes"
    client: Optional[str] = None
    cr_id: Optional[str] = None
    cr_status: Optional[str] = None
    stage: Optional[str] = None
    show_on_main: bool = True

@app.post("/api/project-extension")
def create_lightweight_project(req: LightweightProjectCreate, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    code = (req.cr_id or req.name)[:6].upper().replace("#","").replace("-","") + str(uuid.uuid4())[:4].upper()
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (code, name, mpp_path, health, progress, start_date, end_date, "
            "created_at, team_type, is_lightweight, client, cr_id, cr_status, stage, show_on_main) "
            "VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
            (code, req.name.strip(), "", "G", req.progress,
             req.start_date or now_iso, req.end_date or None, now_iso,
             req.team_type, req.client or "", req.cr_id or "",
             req.cr_status or "", req.stage or "", 1 if req.show_on_main else 0)
        )
    return {"status": "ok", "code": code}


class LightweightProjectUpdate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    progress: Optional[float] = None
    team_type: Optional[str] = None
    client: Optional[str] = None
    stakeholder: Optional[str] = None
    cr_id: Optional[str] = None
    cr_status: Optional[str] = None
    stage: Optional[str] = None
    show_on_main: Optional[bool] = None

@app.put("/api/project-extension/{code}")
def update_lightweight_project(code: str, req: LightweightProjectUpdate, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM projects WHERE code=? AND is_lightweight=1", (code,)).fetchone()
        if not p:
            raise HTTPException(404, "Lightweight project not found.")
        name        = req.name.strip()    if req.name        is not None else p["name"]
        start_date  = req.start_date      if req.start_date  is not None else p["start_date"]
        end_date    = req.end_date        if req.end_date    is not None else p["end_date"]
        progress    = req.progress        if req.progress    is not None else p["progress"]
        team_type   = req.team_type       if req.team_type   is not None else p["team_type"]
        client      = req.client          if req.client      is not None else (p["client"] or "")
        stakeholder = req.stakeholder     if req.stakeholder is not None else (p["stakeholder"] if "stakeholder" in p.keys() else "")
        cr_id       = req.cr_id           if req.cr_id       is not None else (p["cr_id"]     if "cr_id"     in p.keys() else "")
        cr_status   = req.cr_status       if req.cr_status   is not None else (p["cr_status"] if "cr_status" in p.keys() else "")
        stage       = req.stage           if req.stage       is not None else (p["stage"]     if "stage"     in p.keys() else "")
        show_on_main = (1 if req.show_on_main else 0) if req.show_on_main is not None else (p["show_on_main"] if "show_on_main" in p.keys() else 1)
        health = "G" if progress >= 100 else ("A" if progress >= 50 else "R")
        conn.execute(
            "UPDATE projects SET name=?, start_date=?, end_date=?, progress=?, team_type=?, "
            "health=?, client=?, stakeholder=?, cr_id=?, cr_status=?, stage=?, show_on_main=? WHERE code=?",
            (name, start_date, end_date, progress, team_type, health, client, stakeholder,
             cr_id, cr_status, stage, show_on_main, code)
        )
    return {"status": "ok"}


# ── RESOURCE SUMMARY ──────────────────────────────────────────────────────────

@app.get("/api/resource-summary")
def get_resource_summary():
    from app.database import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM resource_summary ORDER BY sort_order, section, project").fetchall()
    return {"rows": [dict(r) for r in rows]}


class ResourceSummaryRow(BaseModel):
    section: str
    project: str = ""
    sub_project: str = ""
    resource_name: str = ""
    utilization_pct: int = 0
    remarks: str = ""
    sort_order: int = 0

@app.post("/api/resource-summary")
def create_resource_summary_row(req: ResourceSummaryRow, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    row_id = str(uuid.uuid4())
    with get_conn() as conn:
        max_order = conn.execute("SELECT MAX(sort_order) FROM resource_summary WHERE section=?", (req.section,)).fetchone()[0] or 0
        conn.execute(
            "INSERT INTO resource_summary (id,section,project,sub_project,resource_name,utilization_pct,remarks,sort_order) VALUES (?,?,?,?,?,?,?,?)",
            (row_id, req.section, req.project, req.sub_project, req.resource_name, req.utilization_pct, req.remarks, max_order + 1)
        )
    return {"status": "ok", "id": row_id}

@app.put("/api/resource-summary/{row_id}")
def update_resource_summary_row(row_id: str, req: ResourceSummaryRow, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "UPDATE resource_summary SET section=?,project=?,sub_project=?,resource_name=?,utilization_pct=?,remarks=? WHERE id=?",
            (req.section, req.project, req.sub_project, req.resource_name, req.utilization_pct, req.remarks, row_id)
        )
    return {"status": "ok"}

@app.delete("/api/resource-summary/{row_id}")
def delete_resource_summary_row(row_id: str, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM resource_summary WHERE id=?", (row_id,))
    return {"status": "ok"}


# ── NAMED RESOURCES ─────────────────────────────────────────────────────────

@app.get("/api/resources")
def list_resources():
    from app.database import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM resources ORDER BY role, name").fetchall()
    return {"resources": [dict(r) for r in rows]}


class ResourceCreate(BaseModel):
    name: str
    role: str
    title: Optional[str] = None

@app.post("/api/resources")
def create_resource(req: ResourceCreate, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    rid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO resources (id, name, role, title, active) VALUES (?,?,?,?,1)",
            (rid, req.name.strip(), req.role, req.title or "")
        )
    return {"status": "ok", "id": rid}


class ResourceUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    title: Optional[str] = None
    active: Optional[int] = None

@app.put("/api/resources/{rid}")
def update_resource(rid: str, req: ResourceUpdate, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM resources WHERE id=?", (rid,)).fetchone()
        if not r:
            raise HTTPException(404, "Resource not found.")
        name   = req.name.strip()  if req.name   is not None else r["name"]
        role   = req.role          if req.role   is not None else r["role"]
        title  = req.title         if req.title  is not None else r["title"]
        active = req.active        if req.active is not None else r["active"]
        conn.execute(
            "UPDATE resources SET name=?, role=?, title=?, active=? WHERE id=?",
            (name, role, title, active, rid)
        )
    return {"status": "ok"}


@app.delete("/api/resources/{rid}")
def delete_resource(rid: str, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM named_allocations WHERE resource_id=?", (rid,))
        conn.execute("DELETE FROM resources WHERE id=?", (rid,))
    return {"status": "ok"}


# ── NAMED ALLOCATIONS (resource → project with %) ────────────────────────────

@app.get("/api/projects/{code}/named-allocations")
def get_named_allocations(code: str):
    from app.database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT na.id, na.resource_id, na.allocation_pct,
                      r.name, r.role, r.title
               FROM named_allocations na
               JOIN resources r ON r.id = na.resource_id
               WHERE na.project_code = ?
               ORDER BY r.role, r.name""",
            (code,)
        ).fetchall()
    return {"allocations": [dict(r) for r in rows]}


class NamedAllocationUpsert(BaseModel):
    resource_id: str
    allocation_pct: int = 100

@app.put("/api/projects/{code}/named-allocations")
def upsert_named_allocation(code: str, req: NamedAllocationUpsert, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM named_allocations WHERE project_code=? AND resource_id=?",
            (code, req.resource_id)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE named_allocations SET allocation_pct=? WHERE project_code=? AND resource_id=?",
                (req.allocation_pct, code, req.resource_id)
            )
        else:
            conn.execute(
                "INSERT INTO named_allocations (id, project_code, resource_id, allocation_pct) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), code, req.resource_id, req.allocation_pct)
            )
    return {"status": "ok"}


@app.delete("/api/projects/{code}/named-allocations/{resource_id}")
def remove_named_allocation(code: str, resource_id: str, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM named_allocations WHERE project_code=? AND resource_id=?",
            (code, resource_id)
        )
    return {"status": "ok"}


# ── DASHBOARD SETTINGS ───────────────────────────────────────────────────────

@app.get("/api/dashboard-settings")
def get_dashboard_settings():
    from app.database import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT data FROM dashboard_settings WHERE id='global'").fetchone()
    if row:
        return json.loads(row["data"])
    return {}

@app.put("/api/dashboard-settings")
def save_dashboard_settings(settings: dict, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dashboard_settings (id, data) VALUES ('global', ?)",
            (json.dumps(settings),)
        )
    return {"status": "ok"}


@app.post("/api/upload-mpp")
async def upload_mpp(file: UploadFile = File(...), caller_id: str = ""):
    if not _JAVA_AVAILABLE:
        raise HTTPException(501, "MPP import is only available on the local machine with Java installed.")
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
    if not _JAVA_AVAILABLE:
        raise HTTPException(501, "MPP sync is only available on the local machine with Java installed.")
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


# ── DB VIEWER (admin only, read-only) ────────────────────────────────────────

_DB_HIDDEN_COLS = {"password_hash"}   # never expose these

@app.get("/api/db/tables")
def db_list_tables(caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return {"tables": [r["name"] for r in rows]}


@app.get("/api/db/tables/{table_name}")
def db_get_table(table_name: str, caller_id: str, page: int = 1, page_size: int = 50):
    _require_admin(caller_id)
    from app.database import get_conn
    # Validate table name (alphanumeric + underscore only)
    if not table_name.replace("_", "").isalnum():
        raise HTTPException(400, "Invalid table name.")
    offset = (page - 1) * page_size
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]  # noqa: S608
        rows  = conn.execute(f"SELECT * FROM {table_name} LIMIT ? OFFSET ?", (page_size, offset)).fetchall()  # noqa: S608
    if not rows:
        return {"columns": [], "rows": [], "total": total, "page": page, "page_size": page_size}
    columns = [c for c in rows[0].keys() if c not in _DB_HIDDEN_COLS]
    data    = [{c: row[c] for c in columns} for row in rows]
    return {"columns": columns, "rows": data, "total": total, "page": page, "page_size": page_size}


# ── PRODUCTS ─────────────────────────────────────────────────────────────────

@app.get("/api/products")
def list_products():
    from app.database import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY sort_order, name").fetchall()
    return {"products": [dict(r) for r in rows]}


class ProductCreate(BaseModel):
    name: str

@app.post("/api/products")
def create_product(req: ProductCreate, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    pid = str(uuid.uuid4())
    with get_conn() as conn:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM products").fetchone()[0]
        try:
            conn.execute(
                "INSERT INTO products (id, name, sort_order) VALUES (?,?,?)",
                (pid, req.name.strip(), max_order + 1)
            )
        except Exception:
            raise HTTPException(409, "A product with that name already exists.")
    return {"status": "ok", "id": pid}


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    sort_order: Optional[int] = None

@app.put("/api/products/{product_id}")
def update_product(product_id: str, req: ProductUpdate, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return {"status": "ok"}
    cols = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [product_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE products SET {cols} WHERE id=?", vals)
    return {"status": "ok"}


@app.delete("/api/products/{product_id}")
def delete_product(product_id: str, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM project_products WHERE product_id=?", (product_id,))
        conn.execute("DELETE FROM products WHERE id=?", (product_id,))
    return {"status": "ok"}


# ── PROJECT ↔ PRODUCTS ───────────────────────────────────────────────────────

@app.get("/api/projects/{code}/products")
def get_project_products(code: str):
    from app.database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.id, p.name, p.sort_order
               FROM project_products pp
               JOIN products p ON p.id = pp.product_id
               WHERE pp.project_code = ?
               ORDER BY p.sort_order, p.name""",
            (code,)
        ).fetchall()
    return {"products": [dict(r) for r in rows]}


class ProjectProductsSet(BaseModel):
    product_ids: List[str]   # full replacement — send the complete desired set

@app.put("/api/projects/{code}/products")
def set_project_products(code: str, req: ProjectProductsSet, caller_id: str):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM project_products WHERE project_code=?", (code,))
        for pid in req.product_ids:
            conn.execute(
                "INSERT OR IGNORE INTO project_products (project_code, product_id) VALUES (?,?)",
                (code, pid)
            )
    return {"status": "ok"}


# ── CUBES 2026 Program Plan ───────────────────────────────────────

@app.get("/api/c2026")
def get_c2026(caller_id: str = ""):
    from app.database import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT data FROM c2026_plan WHERE id='main'").fetchone()
    if not row:
        return {"program_name": "CUBES 2026", "projects": []}
    import json as _json
    return _json.loads(row["data"])


@app.put("/api/c2026")
async def save_c2026(request: Request, caller_id: str):
    _require_admin(caller_id)
    body = await request.json()
    import json as _json
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO c2026_plan (id, data) VALUES ('main', ?)",
            (_json.dumps(body),)
        )
    return {"status": "ok"}


# ── Project Comments (Stakeholder Comments & Risks) ──────────────────────────

_NO_CACHE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


@app.get("/api/project-comments")
def get_project_comments(caller_id: str = ""):
    from app.database import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT project_code, comment, updated_by, updated_at FROM project_comments").fetchall()
    data = {r["project_code"]: {"comment": r["comment"], "updated_by": r["updated_by"], "updated_at": r["updated_at"]} for r in rows}
    return JSONResponse(content=data, headers=_NO_CACHE)


@app.put("/api/project-comments/{project_code}")
async def save_project_comment(project_code: str, request: Request, caller_id: str = ""):
    from app.database import get_conn
    import datetime as _dt
    body = await request.json()
    comment = body.get("comment", "")
    # Get username from caller_id
    with get_conn() as conn:
        user_row = conn.execute("SELECT username FROM users WHERE id=?", (caller_id,)).fetchone()
        username = user_row["username"] if user_row else caller_id
        now = _dt.datetime.utcnow().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO project_comments (project_code, comment, updated_by, updated_at) VALUES (?,?,?,?)",
            (project_code, comment, username, now)
        )
    return JSONResponse(content={"status": "ok"}, headers=_NO_CACHE)


# ── Project Progress ──────────────────────────────────────────────────────────

@app.get("/api/project-progress")
def get_project_progress(caller_id: str = ""):
    from app.database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM project_progress ORDER BY sort_order, project_name"
        ).fetchall()
    return [dict(r) for r in rows]


@app.put("/api/project-progress/{row_id}")
async def update_project_progress(row_id: str, request: Request, caller_id: str = ""):
    body = await request.json()
    allowed_fields = {"project_name","project_code","status","ba","uiux","qc","c_classic","fe","be","due_date","start_date","end_date","sort_order"}
    updates = {k: v for k, v in body.items() if k in allowed_fields}
    if not updates:
        raise HTTPException(400, "No valid fields")
    from app.database import get_conn
    with get_conn() as conn:
        for field, val in updates.items():
            conn.execute(
                f"UPDATE project_progress SET {field}=? WHERE id=?", (val, row_id)
            )
    return {"status": "ok"}


@app.post("/api/project-progress")
async def add_project_progress(request: Request, caller_id: str = ""):
    import uuid as _uuid, json as _json
    body = await request.json()
    new_id = str(_uuid.uuid4())
    from app.database import get_conn
    with get_conn() as conn:
        max_order = conn.execute("SELECT MAX(sort_order) FROM project_progress").fetchone()[0] or 0
        conn.execute(
            "INSERT INTO project_progress (id,project_name,project_code,status,ba,uiux,qc,c_classic,fe,be,due_date,start_date,end_date,sort_order) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id, body.get("project_name","New Project"), body.get("project_code",""),
             body.get("status",""), body.get("ba",""), body.get("uiux",""), body.get("qc",""),
             body.get("c_classic",""), body.get("fe",""), body.get("be",""),
             body.get("due_date",""), body.get("start_date",""), body.get("end_date",""),
             max_order + 1)
        )
    return {"id": new_id}


@app.delete("/api/project-progress/{row_id}")
def delete_project_progress(row_id: str, caller_id: str = ""):
    _require_admin(caller_id)
    from app.database import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM project_progress WHERE id=?", (row_id,))
    return {"status": "ok"}
