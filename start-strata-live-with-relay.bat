@echo off
title Strata Live with relay (read-only monitor)
cd /d "%~dp0"
rem The relay listens on 127.0.0.1:8090 and passes everything to Strata on 8080 unchanged, timing each request.
rem To use it, point your app (pi's models.json "baseUrl") at http://127.0.0.1:8090/v1 . Put it back to
rem http://127.0.0.1:8080/v1 when you are done. Strata itself is not changed. Close this window to stop.
set STRATA_RELAY_PORT=8090
"D:\strata\.venv\Scripts\python.exe" "%~dp0strata_live.py"
if errorlevel 1 pause
