# Agent Audit Log

This file tracks key actions and fixes applied by the agent so progress is preserved across restarts/logins.

## 2026-04-09

- Fixed module visibility logic to use per-user `allowed_modules` as source of truth (`canSee()` no longer uses role-wide topbar overrides for non-admin access checks).
- Fixed DB path resolution to always use the repo DB file (`app/database.py` now uses absolute path anchored to project root).
- Root-cause analysis for Firas/Dima access mismatch completed:
  - Firas had `project_filter = implementation`
  - Dima had `project_filter = imp`
  - Previous filter logic used raw substring matching, so `implementation` did not match `[Imp]` project names.
- Global filter fix applied in `app/users.py`:
  - `implementation` and `imp` are now treated as equivalent for implementation-tagged projects.
  - `cubes` matching improved for `[Cubes]` tagged names.
- Added persistent cross-session coordination log (this file).
- Diagnosed per-user visibility mismatch (Firas vs Dima):
  - Confirmed DB records:
    - `firas.saifan` modules: `["project_page","c2026"]`, filter: `implementation`
    - `dima.hamodi` modules: `["project_page"]`, filter: `imp`
  - Confirmed local login API returns these module arrays correctly.
  - Identified true root cause: project filter matching logic treated `implementation` and `imp` differently.
  - Global fix applied in backend project filter: both aliases now match implementation-tagged projects.
- Added automated DB backup:
  - Script: `scripts/backup_db.ps1`
  - Scheduled task: `ProjectMonitoring-DB-Backup`
  - Frequency: daily at 09:00 AM
  - Destination: `D:\DB Backup`
  - Verified by running task manually once (`Last Result: 0`).

