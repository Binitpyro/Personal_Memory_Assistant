@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_DIR=%%~fI"

cd /d "%PROJECT_DIR%"

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating Python virtual environment...
    py -3.12 -m venv .venv 2>nul || python -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Could not create .venv.
    exit /b 1
)

if not exist "frontend\node_modules" (
    echo [INFO] Installing frontend dependencies...
    pushd frontend
    call npm install
    popd
)

REM Browser mode on :5173 gets no token injected by the backend (only :8000 does), so
REM share one per-session token: the backend enforces it, Vite bakes it into api.ts.
for /f "usebackq tokens=*" %%T in (`powershell -NoProfile -Command "[guid]::NewGuid().ToString('N')"`) do set "PMA_DEV_TOKEN=%%T"
if "%PMA_DEV_TOKEN%"=="" set "PMA_DEV_TOKEN=dev_token_%RANDOM%_%RANDOM%"

echo [INFO] Starting backend on http://127.0.0.1:8000
start "PMA Backend" cmd /k "cd /d %PROJECT_DIR% && call .venv\Scripts\activate.bat && set X_LOCAL_ACCESS_TOKEN=%PMA_DEV_TOKEN%&& python -m uvicorn app.main:app --reload --reload-dir app --reload-exclude tests --reload-exclude data --reload-exclude frontend --reload-exclude .pytest_cache --port 8000"

echo [INFO] Starting Vite frontend on http://127.0.0.1:5173
start "PMA Frontend" cmd /k "cd /d %PROJECT_DIR%\frontend && set VITE_DEV_TOKEN=%PMA_DEV_TOKEN%&& npm run dev"

echo [OK] Browser-mode development started.
echo [OK] Backend:  http://127.0.0.1:8000
echo [OK] Frontend: http://127.0.0.1:5173
