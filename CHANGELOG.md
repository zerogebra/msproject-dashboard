# PRISM PMO Dashboard - Changelog

## [1.1.0] - "The Planner Update" - 2026-03-12
### Added
- **Project Cloning:** Ability to duplicate existing project templates directly from the dashboard to quickly spin up new projects with identical WBS and tasks.
- **Task Risk & Impact Tracking:** Added robust tracking fields to tasks, including `is_critical` (Critical Path flagging), `forecast_end_date` (for impact forecasting), and `comments` (for risk documentation).
- **Auto-Report Generator (Upcoming):** Foundation laid for automatically generating PM email status reports based on actual progress vs. expected progress, including highlighted risks and schedule impacts.
- **Working Hours/Holidays (Upcoming):** Foundation for calculating true schedule variances by ignoring non-working days (e.g., weekends, Ramadan hours).

## [1.0.0] - "Initial Release" - 2026-03-11
### Added
- Natively reads MS Project (`.mpp`) files directly into an SQLite Database.
- Web-based Dashboard displaying RAG status, % Completion, and Task details.
- Overrides system: Users can tweak task completion percentages.
- Sync mechanism: Push manual overrides directly back into the live `.mpp` files.
- Basic User Authentication and Role-based access control (Admin, Editor, Viewer).
- Project portfolio filtering based on User Roles.
