Markdown# Build MS Project (.MPP) Backend + Connect to dashboard_prototype.html## TASK 1 — Create backend structureCreate these files:Show more lines
app/main.py
app/mpp_reader.py
static/dashboard_prototype.html  (use the file I already added)
requirements.txt
.env

## TASK 2 — requirements.txt

fastapi
uvicorn[standard]
jpype1
pydantic
python-dotenv

## TASK 3 — .env
Create this file:

MPP_ROOT=C:\Projects\MSP\    # folder where my .mpp files are
MPXJ_JAR=./libs/mpxj-all-15.3.1.jar

## TASK 4 — mpp_reader.py
```python
import jpype
import jpype.imports
from jpype import JClass

class MPPReader:
    def __init__(self, jar):
        if not jpype.isJVMStarted():
            jpype.startJVM(classpath=[jar])
        self.Reader = JClass("net.sf.mpxj.reader.UniversalProjectReader")

    def load(self, path):
        reader = self.Reader()
        return reader.read(path)
```

TASK 5 — main.py
Pythonfrom fastapi import FastAPIfrom fastapi.responses import HTMLResponse, JSONResponsefrom fastapi.staticfiles import StaticFilesfrom pathlib import Pathimport osfrom app.mpp_reader import MPPReaderapp = FastAPI()app.mount("/static", StaticFiles(directory="static"), name="static")MPP_ROOT = os.getenv("MPP_ROOT", "./mpp")MPXJ_JAR = os.getenv("MPXJ_JAR", "./libs/mpxj-all.jar")reader = MPPReader(MPXJ_JAR)@app.get("/", response_class=HTMLResponse)def home():    return Path("static/dashboard_prototype.html").read_text()@app.get("/api/portfolio")def portfolio(username: str = "user"):    root = Path(MPP_ROOT)    projects = []    for file in root.rglob("*.mpp"):        try:            pf = reader.load(str(file))            props = pf.getProjectProperties()            name = props.getProjectTitle() or file.stem            items = []            for t in pf.getTasks():                if t and t.getName():                    items.append({                        "title": str(t.getName()),                        "start": str(t.getStart()),                        "end": str(t.getFinish()),                        "pct": float(t.getPercentageComplete() or 0)                    })            projects.append({                "code": name[:3].upper(),                "name": name,                "items": items            })        except:            pass    return {"projects": projects}Show more lines
TASK 6 — Modify dashboard_prototype.html
Inside the HTML file, remove the JSON block.
Add this script instead:
HTML<script>(async () => {  const res = await fetch("/api/portfolio?username=mhamzeh");  window.__DATA__ = await res.json();  // keep your existing rendering code but use window.__DATA__})();</script>Show more lines
TASK 7 — Run
Generate a dev command:
uvicorn app.main:app --reload --port 8000

Open:
http://localhost:8000

END

---

# ✅ **THAT’S IT. NOTHING ELSE.**

Paste this small MD file into Cursor and Cursor will know exactly what to build.

---

If you want, I can also create a **ZIP file structure** so you only drag and drop into Cursor.
