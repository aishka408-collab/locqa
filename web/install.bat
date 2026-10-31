@echo off
REM Qazaq Loc QA - initial setup (Windows)
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Please install Python 3.12+.
    pause
    exit /b 1
)

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

echo Creating required directories...
if not exist "qazaq_loc_qa\backend\uploads" mkdir "qazaq_loc_qa\backend\uploads"
if not exist "qazaq_loc_qa\backend\state" mkdir "qazaq_loc_qa\backend\state"
if not exist "qazaq_loc_qa\backend\state\backups" mkdir "qazaq_loc_qa\backend\state\backups"
if not exist "qazaq_loc_qa\backend\static" mkdir "qazaq_loc_qa\backend\static"

echo Setup complete. Run start.bat to launch.
pause
endlocal
