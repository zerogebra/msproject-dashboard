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
    for file in root.rglob("*.mpp"):
        try:
            pf    = reader.load(str(file))
            props = pf.getProjectProperties()
            name  = str(props.getProjectTitle() or file.stem)
            results.append((name, str(file), pf))
        except Exception:
            pass
    return results


# ── routes ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home():
    return Path("static/dashboard_prototype.html").read_text(encoding="utf-8")


@app.get("/api/portfolio")
def portfolio(username: str = "user"):
    all_overrides = load_overrides()
    projects = []

    # Resolve requesting user for project-access filtering
    requesting_user = find_user(username)

    for name, mpp_path, pf in _scan_projects():
        # Skip projects the user is not allowed to see
        if requesting_user and not can_access_project(requesting_user, name):
            continue

        proj_overrides = (
            all_overrides
            .get("projects", {})
            .get(name, {})
            .get("tasks", {})
        )

        items = []
        for t in pf.getTasks():
            if not t or not t.getName():
                continue
            task_title = str(t.getName())
            try:
                level = int(t.getOutlineLevel() or 1)
            except Exception:
                level = 1
            try:
                is_summary = bool(t.getSummary())
            except Exception:
                is_summary = False

            original_pct = float(t.getPercentageComplete() or 0)
            override      = proj_overrides.get(task_title, {})
            pct           = override.get("pct", original_pct)
            overridden    = "pct" in override

            try:
                is_milestone_flag = bool(t.getMilestone())
            except Exception:
                is_milestone_flag = False

            items.append({
                "title":        task_title,
                "start":        str(t.getStart()),
                "end":          str(t.getFinish()),
                "pct":          pct,
                "original_pct": original_pct,
                "level":        level,
                "is_summary":   is_summary,
                "is_milestone":  is_milestone_flag,
                "overridden":   overridden,
                "override_info": {
                    "updated_by": override.get("updated_by", ""),
                    "updated_at": override.get("updated_at", ""),
                } if overridden else None,
            })

        projects.append({
            "code":             name[:3].upper(),
            "name":             name,
            "mpp_path":         mpp_path,
            "pending_overrides": pending_count(name),
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
def get_audit(project: str = None, limit: int = 200):
    log = load_audit_log(project=project, limit=limit)
    return {"entries": log, "total": len(log)}


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
def get_change_requests(caller_id: str, status: str = None):
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
