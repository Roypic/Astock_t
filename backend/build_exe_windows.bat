@echo off
setlocal
cd /d %~dp0

if not exist .build-venv\Scripts\python.exe python -m venv .build-venv
.build-venv\Scripts\python.exe -m pip install --upgrade pip pyinstaller certifi
.build-venv\Scripts\python.exe -m PyInstaller --onefile --windowed --name AShareTSignalMonitor --add-data "models;models" --hidden-import certifi --hidden-import next_day_predict --hidden-import rolling_optimize --collect-data certifi --clean gui_monitor.py

echo Built: %cd%\dist\AShareTSignalMonitor.exe
pause
