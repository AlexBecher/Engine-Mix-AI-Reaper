@echo off
REM Remove old venv if exists
echo Removing old venv (if exists)...
if exist venv rmdir /s /q venv

REM Create new venv
echo Creating new virtual environment...
python -m venv venv

REM Activate venv and install requirements
echo Installing requirements...
call venv\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt

echo Virtual environment recreated and requirements installed.
