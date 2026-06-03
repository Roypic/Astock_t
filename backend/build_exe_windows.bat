@echo off
setlocal
cd /d %~dp0

if not exist .build-venv\Scripts\python.exe python -m venv .build-venv
.build-venv\Scripts\python.exe -m pip install --upgrade pip pyinstaller certifi PySide6
.build-venv\Scripts\python.exe -m PyInstaller --onefile --windowed --name AShareTSignalMonitor --add-data "models;models" --hidden-import certifi --hidden-import PySide6.QtCore --hidden-import PySide6.QtGui --hidden-import PySide6.QtWidgets --collect-data certifi --collect-all PySide6 --clean qt_monitor.py

echo Built: %cd%\dist\AShareTSignalMonitor.exe
pause
