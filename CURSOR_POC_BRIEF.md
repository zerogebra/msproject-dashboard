
# CUBES – MS Project Portfolio Dashboard (POC with Cursor + Python)

**Goal (POC):** Use the provided **HTML front‑end** (`dashboard_prototype.html`) and build a small **Python backend** that reads **Microsoft Project (.MPP)** files from a **local or shared drive**, computes simple portfolio metrics, and serves JSON to the HTML dashboard. Then run locally and later expose a link for users.

> Why this is feasible:
> - The open‑source **MPXJ** library can read Microsoft Project **MPP/MPX/XML** formats programmatically (Java library with Python access via JPype). It supports many Project versions and is free (LGPL). citeturn16search9
> - There are open APIs/libraries for Project formats across multiple languages if needed later. citeturn16search8
> - Cursor is an AI coding editor/agent that can implement the code we describe here inside your local workspace. citeturn16search11

---

## What Cursor Should Build (High‑Level)
1. **Python service (FastAPI)** that:
   - Reads all `.mpp` files under a configured folder (local path or shared drive `\\server\share`).
   - Uses **JPype** to call **MPXJ** (`UniversalProjectReader`) and extract: project name/code, start/end, % complete, milestones, and a flattened list of items (Epic/Feature/Story or Task) with dates & owners. citeturn16search9
   - Computes portfolio KPIs (total projects, health buckets) and a minimal portfolio timeline window.
   - Exposes JSON endpoints consumed by the HTML dashboard.
2. **Static hosting** of `dashboard_prototype.html` at `/` and `/dashboard`.
3. **Config** via environment variables (paths, user access map) and a simple JSON file for per‑user privileges (PM/View only).
4. **Local run** with `uvicorn` and **optional** packaging for later deployment.

---

## Directory Layout Cursor Should Create
```
msproject-poc/
├─ app/
│  ├─ main.py                 # FastAPI app entry
│  ├─ mpp_reader.py           # MPXJ + JPype bridge
│  ├─ models.py               # Pydantic schemas
│  ├─ access.py               # Very light privilege filter
│  ├─ settings.py             # Config via env vars
│  ├─ cache.py                # Optional: refresh & cache JSON
│  └─ __init__.py
├─ static/
│  └─ dashboard_prototype.html
├─ config/
│  ├─ users.json              # username → allowed project codes
│  └─ sample.env              # example environment variables
├─ libs/java/
│  └─ mpxj-all-15.3.1.jar     # MPXJ uber JAR (place here)
├─ scripts/
│  └─ dev-run.bat|sh          # convenience launcher
├─ .env                       # local dev settings
├─ pyproject.toml             # or requirements.txt
└─ README.md
```

> **Note on MPXJ**: The latest MPXJ releases (e.g., **15.3.1** in Feb 2026) are published on SourceForge. Use the **`mpxj-all-<version>.jar`** (uber JAR) so all dependencies are bundled. citeturn16search9turn16search6

---

## Step 1 — Local Environment (Python + Java)

1) **Create venv & install packages**
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install fastapi uvicorn[standard] pydantic jpype1 python-dotenv
```

2) **Install Java (JRE/JDK)** if not already installed (needed for JPype). Set `JAVA_HOME` and make sure `java -version` works.

3) **Download MPXJ**
- From the MPXJ page, download the latest **mpxj-all-<version>.zip** and extract the **`mpxj-all-<version>.jar`** to `libs/java/`. (MPXJ supports reading MPP/MPX and Microsoft Project XML formats across many versions.) citeturn16search9

4) **Prepare your data folder**
- Put your 10 `.mpp` files under a folder you can access (e.g. `D:\Shared\MSP\` or `\\fileserver\PMO\MSP\`).

5) **Create `.env`** (root):
```ini
# Folder containing .mpp files (supports local or UNC paths)
MPP_ROOT=\\\\fileserver\\PMO\\MSP\\
# or MPP_ROOT=D:\\Shared\\MSP\\
# Optional: preload user privileges from config/users.json
USERS_FILE=./config/users.json
# MPXJ Uber JAR location
MPXJ_JAR=./libs/java/mpxj-all-15.3.1.jar
# App settings
PORT=8000
```

---

## Step 2 — Data Model Cursor Should Implement

```python
# models.py
from pydantic import BaseModel
from typing import List, Optional

class Item(BaseModel):
    type: str          # Epic|Feature|Story|Task|Milestone
    title: str
    owner: Optional[str]
    start: Optional[str]  # ISO date
    end: Optional[str]
    pct: Optional[float]
    status: Optional[str]

class Project(BaseModel):
    code: str
    name: str
    health: str          # ontrack|risk|off
    progress: float
    start: Optional[str]
    end: Optional[str]
    milestones: List[str] = []
    items: List[Item] = []

class Portfolio(BaseModel):
    currentUser: dict
    weekZero: str
    projects: List[Project]
```

---

## Step 3 — MPXJ Bridge (Python → Java)

```python
# mpp_reader.py
import os
import jpype
import jpype.imports
from jpype import JClass
from datetime import datetime

class MppReader:
    def __init__(self, jar_path: str):
        self.jar_path = jar_path
        if not jpype.isJVMStarted():
            jpype.startJVM(classpath=[jar_path])  # start JVM with MPXJ uber jar
        self.UniversalProjectReader = JClass('net.sf.mpxj.reader.UniversalProjectReader')

    def load_project(self, file_path: str):
        reader = self.UniversalProjectReader()
        return reader.read(file_path)  # returns a ProjectFile

    def to_python(self, pf):
        # Extract high-level fields
        name = str(pf.getProjectProperties().getProjectTitle() or 'Untitled')
        start = pf.getProjectProperties().getStartDate()
        finish = pf.getProjectProperties().getFinishDate()
        # Derive simple progress as avg of task % complete (for POC)
        tasks = list(pf.getTasks())
        pct_vals = []
        items = []
        for t in tasks:
            if t is None or t.getName() is None:
                continue
            pct = t.getPercentageComplete()
            if pct is not None:
                pct_vals.append(float(pct))
            # Map to simple type by outline level keywords (POC heuristic)
            tname = str(t.getName())
            ttype = 'Task'
            lname = tname.lower()
            if 'epic' in lname: ttype = 'Epic'
            elif 'feature' in lname: ttype = 'Feature'
            elif 'story' in lname: ttype = 'Story'
            elif 'ms:' in lname or 'milestone' in lname: ttype = 'Milestone'
            items.append({
                'type': ttype,
                'title': tname,
                'owner': str(t.getResourceNames() or ''),
                'start': t.getStart() and t.getStart().toString(),
                'end': t.getFinish() and t.getFinish().toString(),
                'pct': pct and float(pct) or 0.0,
                'status': 'On Track'  # POC: later derive from dates
            })
        progress = round(sum(pct_vals)/len(pct_vals), 1) if pct_vals else 0.0
        health = 'ontrack'  # TODO: derive by slippage
        return {
            'code': name[:3].upper(),
            'name': name,
            'health': health,
            'progress': progress,
            'start': start and start.toString(),
            'end': finish and finish.toString(),
            'milestones': [],
            'items': items
        }
```

> MPXJ exposes a `UniversalProjectReader` that reads Microsoft Project files into a `ProjectFile` object which you can iterate for tasks, dates, resources, etc. (That’s the standard entry point used by MPXJ consumers.) citeturn16search9

---

## Step 4 — FastAPI App & Endpoints

```python
# settings.py
import os
from dotenv import load_dotenv
load_dotenv()

class Settings:
    MPP_ROOT = os.getenv('MPP_ROOT', './data')
    MPXJ_JAR = os.getenv('MPXJ_JAR', './libs/java/mpxj-all-15.3.1.jar')
    USERS_FILE = os.getenv('USERS_FILE', './config/users.json')
    PORT = int(os.getenv('PORT', 8000))

settings = Settings()
```

```python
# access.py
import json, os
from typing import List

def load_user_access(file_path: str):
    if not os.path.exists(file_path):
        return {}
    return json.load(open(file_path, 'r', encoding='utf-8'))

def filter_projects_for_user(projects, username, access_map):
    allowed = set(access_map.get(username, {}).get('allowedProjects', []))
    if not allowed:
        return projects
    return [p for p in projects if p['code'] in allowed]
```

```python
# main.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.settings import settings
from app.mpp_reader import MppReader
from app.access import load_user_access, filter_projects_for_user

app = FastAPI(title='CUBES MS Project Portfolio')
app.mount('/static', StaticFiles(directory='static'), name='static')

reader = MppReader(settings.MPXJ_JAR)
ACCESS = load_user_access(settings.USERS_FILE)

# --- helpers ---

def load_all_projects():
    root = Path(settings.MPP_ROOT)
    files = list(root.rglob('*.mpp'))
    projects = []
    for f in files:
        try:
            pf = reader.load_project(str(f))
            projects.append(reader.to_python(pf))
        except Exception as ex:
            print('Failed to read', f, ex)
    return projects

@app.get('/', response_class=HTMLResponse)
@app.get('/dashboard', response_class=HTMLResponse)
async def serve_dashboard():
    html = Path('static/dashboard_prototype.html').read_text(encoding='utf-8')
    return HTMLResponse(html)

@app.get('/api/portfolio')
async def portfolio(username: str = 'mhamzeh'):
    projects = load_all_projects()
    projects = filter_projects_for_user(projects, username, ACCESS)
    payload = {
        'currentUser': {'name': username, 'role': ACCESS.get(username, {}).get('role', 'viewer'),
                        'allowedProjects': [p['code'] for p in projects]},
        'weekZero': '2026-02-22',
        'projects': projects
    }
    return JSONResponse(payload)

@app.get('/api/projects')
async def list_projects(username: str = 'mhamzeh'):
    projects = load_all_projects()
    projects = filter_projects_for_user(projects, username, ACCESS)
    return {'projects': [{'code': p['code'], 'name': p['name']} for p in projects]}

@app.get('/api/projects/{code}')
async def get_project(code: str, username: str = 'mhamzeh'):
    projects = load_all_projects()
    projects = filter_projects_for_user(projects, username, ACCESS)
    for p in projects:
        if p['code'].lower() == code.lower():
            return p
    return JSONResponse({'error': 'Not found'}, status_code=404)
```

---

## Step 5 — Wire the HTML to the API (swap sample JSON → fetch)

In `static/dashboard_prototype.html`, **replace** the `<script id="portfolioData" ...>` block with a dynamic fetch on page load:

```html
<script>
(async function(){
  const res = await fetch('/api/portfolio?username=mhamzeh');
  const data = await res.json();
  // then keep the same rendering logic as in the prototype, using `data`
})();
</script>
```

*(Cursor can refactor the current script to call `/api/portfolio` and remove the embedded JSON.)*

---

## Step 6 — Run Locally

```bash
# from repo root
uvicorn app.main:app --reload --port ${PORT:-8000}
# Open http://localhost:8000/
```

If you cannot read from a UNC path, map the shared drive to a letter (e.g., `Z:`) or ensure the Python process has permissions.

---

## Step 7 — Minimal Privileges (POC)
- Define `config/users.json` like:
```json
{
  "mhamzeh": {"role": "pm", "allowedProjects": ["ICP", "ERP", "C360", "PAY"]},
  "viewer1": {"role": "viewer", "allowedProjects": ["ICP", "ERP"]}
}
```
- The backend filters portfolio results based on `allowedProjects`.
- Later, replace with real auth (JWT/AD).

---

## Acceptance Criteria for Cursor
1. **Serve** `dashboard_prototype.html` at `/` and `/dashboard`.
2. **Read** all `.mpp` files under `MPP_ROOT` using MPXJ via JPype without crashing. (Use `mpxj-all` JAR.) citeturn16search9turn16search6
3. **Expose** endpoints:
   - `GET /api/portfolio?username=X`
   - `GET /api/projects`
   - `GET /api/projects/{code}`
4. **Front‑end** renders **real data** from `/api/portfolio` (no embedded sample JSON).
5. **Filter** projects by `allowedProjects` for the requested `username`.
6. **Docs**: Update `README.md` with setup/run steps and mention MPXJ license (LGPL). citeturn16search9

---

## Prompts You Can Paste into Cursor (one by one)

**Prompt 1 – Create project skeleton**
> Create the folder structure shown in the "Directory Layout". Add `pyproject.toml` with FastAPI, Uvicorn, JPype, Pydantic, python-dotenv. Place my existing `dashboard_prototype.html` into `static/`. Add `.env` and `config/sample.env` according to the variables listed above.

**Prompt 2 – Implement MPXJ bridge**
> Add `app/mpp_reader.py` exactly as in the spec. Ensure JPype starts JVM once using the path from `MPXJ_JAR`. Provide graceful error handling for corrupt MPP files.

**Prompt 3 – Build API**
> Implement `app/main.py`, `app/models.py`, `app/access.py`, `app/settings.py` exactly as described. Mount `/static`. Implement endpoints `/api/portfolio`, `/api/projects`, `/api/projects/{code}`.

**Prompt 4 – Wire dashboard**
> In `static/dashboard_prototype.html`, replace the embedded JSON with a fetch to `/api/portfolio?username=mhamzeh`. Keep all rendering logic; just feed it with fetched data.

**Prompt 5 – Run & validate**
> Start the server with `uvicorn app.main:app --reload --port 8000`. Open `http://localhost:8000`. Verify the KPIs, cards, timeline, and details table show data from the `.mpp` files in `MPP_ROOT`.

**Prompt 6 – Packaging**
> Add a `scripts/dev-run.sh` (and `.bat`) that loads `.env` and runs Uvicorn. Update `README.md` with all steps and MPXJ download instructions.

---

## Notes & Alternatives
- If you prefer **Project XML (MSPDI)** exports instead of `.mpp`, MPXJ reads those too and parsing is typically lighter. citeturn16search9
- If you later want a non‑Java approach, see the catalog of **open APIs** for Project formats in different languages. citeturn16search8

---

## Troubleshooting
- **JVM not found**: Set `JAVA_HOME` and ensure `jpype1` can find the JVM.
- **Class not found**: Check `MPXJ_JAR` path; use the **uber JAR** (`mpxj-all-…`). citeturn16search6
- **Cannot open .mpp**: Confirm file permissions and that the path in `MPP_ROOT` is correct (escape backslashes in `.env`).

---

## License & Attribution
- **MPXJ** is open‑source (LGPL). Respect its license; include a link in `README.md`. citeturn16search9

