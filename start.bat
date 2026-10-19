@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv312\Scripts\pythonw.exe" (
    echo Create .venv312 and install requirements-lock.txt first. See README.md.
    pause
    exit /b 1
)
start "Media Deduplicator" ".venv312\Scripts\pythonw.exe" run.py
