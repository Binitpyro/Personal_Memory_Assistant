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

echo [INFO] Starting backend on http://127.0.0.1:8000
start "PMA Backend" cmd /k "cd /d %PROJECT_DIR% && call .venv\Scripts\activate.bat && python -m uvicorn app.main:app --reload --port 8000"

echo [INFO] Starting Vite frontend on http://127.0.0.1:5173
start "PMA Frontend" cmd /k "cd /d %PROJECT_DIR%\frontend && npm run dev"

echo [OK] Browser-mode development started.
echo [OK] Backend:  http://127.0.0.1:8000
echo [OK] Frontend: http://127.0.0.1:5173
