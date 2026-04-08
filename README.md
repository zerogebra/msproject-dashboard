# PRISM — PMO Dashboard

> **Version:** v1.6 | **Stack:** Python FastAPI + React 18 (CDN/Babel) + SQLite
> **Maintained by:** Mohammad Hamzeh — Cubes Platform

---

## Overview

PRISM is a local-first Project Management Office (PMO) dashboard for tracking CUBES implementation projects. It reads data from MS Project `.mpp` files (optional), stores everything in SQLite, and provides a React-based web UI accessible over your local network or via Render.com (read-only cloud).

---

## Features

| Feature | Details |
|---|---|
| 📊 Portfolio Dashboard | Multi-project overview with KPI strip |
| 📋 Task Plan Table | WBS, status, %, actual dates, ext. days, progress bar |
| 📈 Gantt Chart | Status-coloured bars with today line and completion fill |
| 🤖 Project Intelligence | AI risk/focus summary per project |
| 📊 Resource Hub | Topbar button → slide-in panel with allocation + capacity |
| 🚀 Project Wizard | 3-step CUBES Implementation project creator (Jordan calendar) |
| ✎ Inline Editing | % progress, actual start/finish dates (admin/editor) |
| ⚠ Risk/Delay Log | Per-task extra-days + comments (admin/editor) |
| 👥 User Management | Admin panel: add/edit users, roles, project access |
| 🔐 Change Requests | Non-admins request 100% changes; admin approves/rejects |
| ⚙️ Dashboard Settings | Admin controls which columns/sections each role sees |
| 🔄 MS Project Sync | Push % changes back to .mpp files (local machine only) |
| 📋 Audit Log | Full change history at bottom of dashboard |
| 🌙 Dark / Light Mode | Toggle in topbar |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn |
| Database | SQLite (`data/prism.db`) |
| Frontend | React 18 (CDN), Babel Standalone (no Node.js needed) |
| MPP Parsing | MPXJ via JPype1 (local machine with Java JRE only) |
| Auth | Username-based login (no password), localStorage session |

---

## Project Structure

```
Project Monitoring/
├── app/
│   ├── main.py             # FastAPI app, all API endpoints
│   ├── database.py         # SQLite schema, migrations, seeding
│   ├── users.py            # User management
│   ├── overrides.py        # % override management
│   ├── change_requests.py  # Approval workflow
│   ├── mpp_reader.py       # MPXJ MPP parser (local only)
│   └── msp_push.py         # COM push to MS Project (local only)
├── static/
│   └── dashboard_prototype.html  # Entire frontend (React/JSX)
├── data/
│   └── prism.db            # SQLite database (committed to git)
├── mpp/                    # MS Project .mpp files (NOT committed)
├── requirements.txt
├── render.yaml             # Render.com deployment config
├── README.md               # This file
└── run server.bat          # Windows: start local server
```

---

## Setup (Local)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start server (all network interfaces — share with team)
py -3.11 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 3. Open in browser
http://localhost:8000

# Share with team on the same network:
http://192.168.x.x:8000   ← replace with your machine's IP
```

Or simply double-click **`run server.bat`** on Windows.

---

## Default Users

| Name | Username | Role | Project Access |
|---|---|---|---|
| Mohammad Hamzeh | mohammad.hamzeh | **Admin** | All projects |
| Anas Abdelhadi | anas.abdelhadi | Editor | Cubes projects |
| Rawad Khallad | rawad.khallad | Editor | Cubes projects |
| Mohammad Younes | mohammad.younes | Viewer | All projects |
| Dima Hamodi | dima.hamodi | Viewer | Imp projects |

**Login:** type your **username** or **email** on the login screen (no password required).

---

## Key Workflows

### Update Task Progress
1. Click the **%** cell next to any task
2. Type new value → press **Enter**
3. Progress ratchet rule: cannot decrease below current value (unless admin)
4. Tasks at 100%: non-admins must submit a **change request**

### Log Risk / Delay
1. Click **⚠ Log Risk/Delay** on any task row (editor/admin)
2. Enter extra days needed, mark as critical, add comments
3. Actual Finish date auto-adjusts

### Edit Actual Dates (Admin only)
- Click any **Actual Start** or **Actual Finish** cell → inline date picker appears
- Press **Enter** or click away to save

### Resource Hub
- Click **📊 Resources** button in the topbar
- View utilization per role across all projects
- Admin: edit pool size (👥 Resource Pool tab) and per-project allocations

### Create New Project (Wizard)
- Click **🚀 New Project** in the sidebar
- **Step 1:** Project name, start date, priority, working hours/day
- **Step 2:** Enter effort per discipline (days), set resources — system calculates total hours
- **Step 3:** Review auto-generated CUBES Implementation plan (Jordan calendar applied)

### Sync from MS Project (Local machine only)
1. Close all MS Project files first
2. Click **🔄 Sync from MPP** in the sidebar
3. Or click **🔄 Push to MS Project** on a project card to write % back to the `.mpp` file

---

## Admin Controls

Open the **Admin Panel** via the **👥 Admin** button in the topbar.

### Users Tab
- Add, edit, deactivate users
- Set role: **Admin** / **Editor** / **Viewer**
- Set project access: `all`, `cubes`, `imp`, or specific project codes

### Change Requests Tab
- Review pending % change requests from non-admin users
- Approve or reject with a note

### ⚙️ Dashboard Settings Tab
Control which columns and sections are visible per user role:

| Setting | What it controls |
|---|---|
| % Edit Column | Inline % editing |
| Actual Start/Finish | Actual date columns |
| Ext. Days | Variance column (+N days) |
| ⚠ Log Risk/Delay | Risk logging button on task rows |
| 🤖 Project Intelligence | AI risk/focus panel |
| 🎯 In Progress Focus | Focus items panel |
| Gantt Chart | Full Gantt section |
| Audit Log | Change history section at bottom |

Admins always see everything regardless of settings.

---

## Working Calendar (Jordan)

The project wizard and all duration calculations use the **Jordan working calendar**:

- **Working days:** Sunday – Thursday
- **Weekend:** Friday + Saturday
- **Public holidays included:**

| Date | Holiday |
|---|---|
| 1 Jan | New Year |
| 27 Jan | Isra Mi'raj |
| 20–22 Mar | Eid Al-Fitr (approx.) |
| 1 May | Labour Day |
| 25 May | Independence Day |
| 27–30 May | Eid Al-Adha (approx.) |
| 10 Jun | Army Day |
| 18 Jun | Islamic New Year |
| 25 Aug | Prophet's Birthday |
| 25 Dec | Christmas |

---

## Deployment (Render.com — read-only cloud)

1. Commit `data/prism.db` to GitHub (already allowed in `.gitignore`)
2. Push code to GitHub
3. Render pulls automatically and serves the app
4. MPP sync features are **disabled on Render** (no Java available)
5. Users can view projects read-only from anywhere via the Render URL

---

## Version History

| Version | Date | Key Changes |
|---|---|---|
| v1.0 | Feb 2026 | Initial FastAPI + static HTML dashboard |
| v1.1 | Feb 2026 | React.js migration, sidebar multi-select, Gantt chart, light mode |
| v1.2 | Feb 2026 | Inline % editing, push to MS Project, audit log, version badge |
| v1.3 | Feb 2026 | User management, change request workflow, login system |
| v1.4 | Mar 2026 | SQLite migration, ratchet rule, 100% lock, admin approval |
| v1.5 | Mar 2026 | Resource Hub, Smart Project Wizard (Jordan calendar), priority, column visibility |
| v1.6 | Mar 2026 | Resource Hub → topbar modal, `extended_days` bug fix, actual date editing, Dima access fix, Dashboard Settings tab |
| v1.7 | Feb 2026 | Cubes 2026 plan: BA/UI/UX dates, Project #2 renamed, Note column in Program Summary |
| v1.8 | Feb 2026 | Cubes 2026: cross-project dependency system (row codes, Depends On column, FS/SS/FF), working-day `days` column |

---

## Known Limitations

- MS Project sync requires the local machine to have **Java JRE** installed and MS Project **closed**
- No password authentication — intended for trusted internal teams on private networks
- Render deployment is **read-only** (no MPP import/export in cloud)

---

## Support

Contact: **Mohammad Hamzeh** — mohammad.hamzeh@cubesplatform.com
