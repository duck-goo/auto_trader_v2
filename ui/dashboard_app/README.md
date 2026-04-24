# Dashboard App

React/Vite dashboard for `dashboard_snapshot.json`.

## Purpose
- Show the trading dashboard state in a browser.
- Read the local snapshot API when the Python server is running.
- Keep file upload support for offline snapshot review.

## Run
```powershell
.\venv\Scripts\python.exe scripts\serve_dashboard_app.py --trade-date 2026-04-20

cd ui\dashboard_app
npm run dev
```

## Current Scope
- Sample snapshot fallback
- Manual JSON file upload
- Local `/api/dashboard-snapshot` loading
- Auto refresh toggle with safe stop on API failure
- Overview / controls / scan / executions / recovery / rehearsal / actions panels

## Notes
- Auto refresh is disabled by default for safety.
- If a background refresh fails, polling stops and the current screen is kept.
- To resume polling, reload from server first and then start auto refresh again.
