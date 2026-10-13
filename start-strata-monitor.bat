@echo off
rem Strata Monitor: the desktop window (no web server, no console). Errors go to logs\monitor.log.
start "" "D:\strata\.venv\Scripts\pythonw.exe" "%~dp0strata_monitor.pyw"
