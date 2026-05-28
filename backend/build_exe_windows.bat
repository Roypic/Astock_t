@echo off
setlocal
cd /d %~dp0

if not exist .build-venv\Scripts\python.exe python -m venv .build-venv
.build-venv\Scripts\python.exe -m pip install --upgrade pip pyinstaller
.build-venv\Scripts\python.exe -m PyInstaller --onefile --name AShareTSignalMonitor --clean desktop_monitor.py

echo Built: %cd%\dist\AShareTSignalMonitor.exe
pause
