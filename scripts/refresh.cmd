@echo off
rem Vigie scheduled refresh wrapper (Task Scheduler entry point).
rem Space-free path keeps schtasks /TR quoting simple; %~dp0 tracks the repo.
rem Production deploys are refused outside GitHub Actions (exit 2): this local
rem data/ may hold a stale or forked registre. Break-glass only, after unpacking
rem the private state: set VIGIE_ALLOW_LOCAL_DEPLOY=1 before this line.
"C:\msys64\mingw64\bin\python.exe" -X utf8 "%~dp0refresh.py"
