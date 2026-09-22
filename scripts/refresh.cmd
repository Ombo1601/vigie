@echo off
rem Vigie scheduled refresh wrapper (Task Scheduler entry point).
rem Space-free path keeps schtasks /TR quoting simple; %~dp0 tracks the repo.
"C:\msys64\mingw64\bin\python.exe" -X utf8 "%~dp0refresh.py" %*
