@echo off
setlocal
cd /d %~dp0

if not exist .build-venv\Scripts\python.exe python -m venv .build-venv
.build-venv\Scripts\python.exe -m pip install --upgrade pip pyinstaller certifi
.build-venv\Scripts\python.exe -m PyInstaller --onefile --windowed --name AShareTSignalMonitor --add-data "models;models" --hidden-import certifi --collect-data certifi --clean gui_monitor.py

echo Built: %cd%\dist\AShareTSignalMonitor.exe
pause
