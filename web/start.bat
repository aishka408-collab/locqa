@echo off
REM Qazaq Loc QA - local start script (Windows)
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Please install Python 3.12+ from https://www.python.org/
    pause
    exit /b 1
)

python -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Dependencies not installed. Run install.bat first.
    pause
    exit /b 1
)

set PORT=8000
python -m uvicorn qazaq_loc_qa.backend.api.main:app --host 127.0.0.1 --port %PORT%
if errorlevel 1 (
    echo [ERROR] Failed to start server. Port %PORT% may be in use.
    pause
)
endlocal
