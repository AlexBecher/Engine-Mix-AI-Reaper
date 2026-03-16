@echo off

REM Ativa o ambiente virtual venv
call "%~dp0venv\Scripts\activate.bat"

REM Lê o Python base usado pelo venv e configura Tcl/Tk para o tkinter.
if exist "%~dp0venv\pyvenv.cfg" (
	for /f "tokens=2 delims==" %%A in ('findstr /b "home" "%~dp0venv\pyvenv.cfg"') do set "PY_HOME=%%A"
)

if defined PY_HOME set "PY_HOME=%PY_HOME:~1%"

if not defined PY_HOME if exist "C:\Program Files\Python313\python.exe" set "PY_HOME=C:\Program Files\Python313"

if defined PY_HOME (
	if exist "%PY_HOME%\tcl\tcl8.6\init.tcl" set "TCL_LIBRARY=%PY_HOME%\tcl\tcl8.6"
	if exist "%PY_HOME%\tcl\tk8.6\tk.tcl" set "TK_LIBRARY=%PY_HOME%\tcl\tk8.6"
)

echo VENV ativo: %VIRTUAL_ENV%
if defined TCL_LIBRARY echo TCL_LIBRARY=%TCL_LIBRARY%
if defined TK_LIBRARY echo TK_LIBRARY=%TK_LIBRARY%
