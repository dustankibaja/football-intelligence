@echo off
REM Football Intelligence - Windows Scheduled Task Execution Wrapper
REM Invokes ops-run-once in LIVE_SHADOW mode using the repository virtual environment
cd /d "%~dp0..\.."
call .venv\Scripts\python.exe -m app.automation.cli --mode LIVE_SHADOW --trigger WINDOWS_TASK_SCHEDULER
