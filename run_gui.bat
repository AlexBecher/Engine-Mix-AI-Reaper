@echo off
REM Mix Robo GUI Launcher for Windows
REM This script opens the configuration GUI for Mix Robo

cd /d "%~dp0"

echo.
echo ====================================
echo     Mix Robo - Configuration GUI
echo ====================================
echo.

.\.venv\Scripts\python config_gui.py

pause
