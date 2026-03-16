@echo off
REM Mix Robo GUI Launcher for Windows
REM This script opens the configuration GUI for Mix Robo

cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
	echo [!] venv nao encontrado. Execute recreate_venv.bat primeiro.
	pause
	exit /b 1
)

if exist "venv\pyvenv.cfg" (
	for /f "tokens=2 delims==" %%A in ('findstr /b "home" "venv\pyvenv.cfg"') do set "PY_HOME=%%A"
)

if defined PY_HOME set "PY_HOME=%PY_HOME:~1%"

if not defined PY_HOME if exist "C:\Program Files\Python313\python.exe" set "PY_HOME=C:\Program Files\Python313"

if defined PY_HOME (
	if exist "%PY_HOME%\tcl\tcl8.6\init.tcl" set "TCL_LIBRARY=%PY_HOME%\tcl\tcl8.6"
	if exist "%PY_HOME%\tcl\tk8.6\tk.tcl" set "TK_LIBRARY=%PY_HOME%\tcl\tk8.6"
)

echo.
echo ====================================
echo     Mix Robo - Configuration GUI
echo ====================================
echo.

venv\Scripts\python.exe config_gui.py

pause
