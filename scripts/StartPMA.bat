@echo off
:: StartPMA.bat — PMA Development Startup with Admin Elevation
:: Requests Administrator privileges for NTFS fast scanning

:: ── Admin Elevation Check ──
net session >nul 2>&1
if %errorLevel% == 0 goto :already_admin

echo [PMA] Requesting Administrator privileges for NTFS fast scanning...
powershell -Command "Start-Process '%~f0' -Verb RunAs"
exit /b

:already_admin

title PMA Development

echo ============================================
echo   Personal Memory Assistant - Development
echo ============================================

if exist "%~dp0..\frontend\src-tauri" (
    echo [NOTE] Tauri desktop workflow is now the primary desktop launcher.
    echo [NOTE] For the native app shell, run:
    echo [NOTE]   cd frontend ^&^& npm run tauri dev
    echo [NOTE] This script still works for backend/browser-mode development.
    echo.
)

:: 1. Define Paths & Global Vars
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_DIR=%%~fI"
set "SONAR_DIR=C:\sonarqube\bin\windows-x86-64"

if not exist "%PROJECT_DIR%" (
    echo [ERROR] Project directory not found: %PROJECT_DIR%
    pause
    exit /b 1
)

cd /d "%PROJECT_DIR%"
echo [INFO] Working directory: %CD%

:: 2. Python Environment Setup
if exist ".venv\Scripts\activate.bat" goto activate_venv

echo [INFO] Creating virtual environment with Python 3.12...
where uv >nul 2>&1
if %ERRORLEVEL% == 0 (
    uv venv --python 3.12 --clear .venv
) else (
    py -3.12 -m venv .venv 2>nul || python3.12 -m venv .venv 2>nul || python -m venv .venv
)

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Failed to create .venv. Ensure Python is installed and in PATH.
    pause
    exit /b 1
)

:activate_venv
echo [INFO] Activating environment and syncing dependencies...
call ".venv\Scripts\activate.bat"

:: Use uv sync (reads pyproject.toml/uv.lock) — requirements.txt is for CI only
where uv >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo [INFO] Syncing dependencies with uv...
    uv sync --all-extras
) else (
    echo [WARN] uv not found — falling back to pip editable install...
    pip install -e . --quiet
)

:: 2.5 Rust Native Extension Compilation
echo [INFO] Checking Rust extension (rust_core)...
:: We check if the python module was created by maturin in the venv
python -c "import rust_core" 2>nul
if %ERRORLEVEL% neq 0 (
    echo [INFO] Compiling PyO3 rust_core extension...
    pushd app\scanner\rust_core
    maturin develop --release
    popd
)

:: 3. Frontend Check and Build
if not exist "frontend\package.json" goto launch_wt

echo [INFO] Checking frontend dependencies...
pushd frontend
if not exist "node_modules" (
    echo [INFO] Running npm install...
    call npm install --silent
)
echo [INFO] Building frontend assets...
call npm run build
popd

:launch_wt
echo.
echo [OK] Environment ready. Launching Windows Terminal...

:: 3.5 SonarQube Detection
set "HAS_SONAR=0"
if exist "sonar-project.properties" (
    if exist "%SONAR_DIR%\StartSonar.bat" (
        set "HAS_SONAR=1"
    )
)

if "%HAS_SONAR%"=="1" (
    echo [INFO] Tab 1: API - Sonar - Vite
    echo [INFO] Tab 2: Virtual Environment Terminal
    echo.

    :: Force kill any zombie SonarQube/Java processes before starting
    echo [INFO] Terminating old SonarQube Java processes...
    powershell -Command "Stop-Process -Name java -Force -ErrorAction SilentlyContinue"

    :: Remove SonarQube locks if they exist from a previous bad shutdown
    if exist "C:\sonarqube\data\es8\node.lock" (
        echo [INFO] Removing orphaned SonarQube node.lock file...
        del /f /q "C:\sonarqube\data\es8\node.lock"
    )
    if exist "C:\sonarqube\data\es8\_state\write.lock" (
        echo [INFO] Removing orphaned Elasticsearch write.lock file...
        del /f /q "C:\sonarqube\data\es8\_state\write.lock"
    )
) else (
    echo [INFO] SonarQube not found or not configured for this project. Skipping...
    echo [INFO] Tab 1: API - Vite
    echo [INFO] Tab 2: Virtual Environment Terminal
    echo.
)

:: Generate random per-session local access token
if not exist "data" mkdir "data"
for /f "usebackq tokens=*" %%T in (`powershell -NoProfile -Command "[guid]::NewGuid().ToString('N')"`) do set "PMA_DEV_TOKEN=%%T"
if "%PMA_DEV_TOKEN%"=="" set "PMA_DEV_TOKEN=dev_token_%RANDOM%_%RANDOM%"
echo %PMA_DEV_TOKEN%> "data\.dev_token"

:: Windows Terminal Command Construction
set "API_CMD=title PMA API && cd /d %PROJECT_DIR% && call .venv\Scripts\activate.bat && set X_LOCAL_ACCESS_TOKEN=%PMA_DEV_TOKEN%&& uvicorn app.main:app --reload --port 8000"
set "SONAR_CMD=title SonarQube && cd /d %SONAR_DIR% && call StartSonar.bat"
set "VITE_CMD=title Vite Frontend && cd /d %PROJECT_DIR%\frontend && set VITE_DEV_TOKEN=%PMA_DEV_TOKEN%&& npm run dev"
set "TERM_CMD=title PMA Terminal && cd /d %PROJECT_DIR% && call .venv\Scripts\activate.bat"

:: Launch WT with appropriate tabs:
if "%HAS_SONAR%"=="1" (
    :: Tab 1: API (pane 0), Sonar (pane 1, right), Vite (pane 2, bottom of API)
    wt ^
      new-tab -p "Command Prompt" --title "PMA API" cmd /k "%API_CMD%" ^
      ; split-pane -V -p "Command Prompt" -s 0.3 --title "SonarQube" cmd /k "%SONAR_CMD%" ^
      ; focus-pane -t 0 ^
      ; split-pane -H -p "Command Prompt" -s 0.3 --title "Vite Frontend" cmd /k "%VITE_CMD%" ^
      ; new-tab -p "Command Prompt" --title "PMA Terminal" cmd /k "%TERM_CMD%"
) else (
    :: Tab 1: API (pane 0), Vite (pane 1, bottom of API)
    wt ^
      new-tab -p "Command Prompt" --title "PMA API" cmd /k "%API_CMD%" ^
      ; split-pane -H -p "Command Prompt" -s 0.3 --title "Vite Frontend" cmd /k "%VITE_CMD%" ^
      ; new-tab -p "Command Prompt" --title "PMA Terminal" cmd /k "%TERM_CMD%"
)

echo ============================================
echo All systems launched in Windows Terminal!
echo API: http://localhost:8000
echo Vite: http://localhost:5173
echo ============================================
echo.
pause
