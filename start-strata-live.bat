@echo off
title Strata Live (read-only monitor)
cd /d "%~dp0"
rem Uses Strata's own Python but changes nothing in D:\strata. Close this window to stop the monitor.
"D:\strata\.venv\Scripts\python.exe" "%~dp0strata_live.py"
if errorlevel 1 pause
