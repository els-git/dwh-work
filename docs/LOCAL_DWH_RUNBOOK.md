# Local DWH Development Runbook

## Architecture

- PostgreSQL 17: independent service, port 5433
- FastAPI backend: 127.0.0.1:8000
- Dispatcher: separate thread in the same Python process
- Dispatcher polling interval: 5 seconds
- Frontend: launched separately

## Start backend and dispatcher

Open PowerShell and execute:

    & "C:\Users\elshad.asgarov\Desktop\DWH\dwh\tools\run_local_dwh.ps1" -Mode Run

Keep the PowerShell window open during development.

## Stop

Press Ctrl+C in the window running local DWH.
The dispatcher finishes the current iteration before exiting.
Do not force-kill Python during active ETL execution.

## Configuration check

    & "C:\Users\elshad.asgarov\Desktop\DWH\dwh\tools\run_local_dwh.ps1" -Mode Check

## Verify backend

Open http://127.0.0.1:8000/docs

## Important

- Do not start a second dispatcher while local DWH is running.
- Configuration is loaded from the project .env file.
- Real passwords must not be stored in documentation.
- Do not modify the unrelated PostgreSQL service.

## Completed checks

- Crash recovery migration 039: passed
- Dispatcher smoke tests 071 and 072: passed
- Timezone verification 070: passed
- Single-process launcher check 083: passed
- Backend startup 084: passed
- Backend runtime check 085: passed
