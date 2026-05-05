@echo off
:: Build-Exe.bat — Builds PMA.exe via PyInstaller
:: Run from anywhere; always executes from the project root.

:: ── Enforce project-root context ──────────────────────────────────────────
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%"
echo [INFO] Working directory: %CD%

:: ── Verify virtual environment ────────────────────────────────────────────
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] .venv not found. Run StartPMA.bat or 'uv sync' first.
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"

:: ── Build React frontend (sidecar needs static assets) ────────────────────
if exist "frontend\package.json" (
    echo [1/3] Building React frontend...
    pushd frontend
    call npm run build
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Frontend build failed.
        popd
        pause
        exit /b 1
    )
    popd
    echo [OK] Frontend built.
) else (
    echo [WARN] No frontend/package.json found — skipping frontend build.
)

:: ── Install / upgrade PyInstaller ─────────────────────────────────────────
echo [2/3] Ensuring PyInstaller is available...
uv pip install pyinstaller --quiet 2>nul || pip install pyinstaller --quiet

:: ── Run PyInstaller ───────────────────────────────────────────────────────
echo [3/3] Building PMA.exe with PyInstaller...

pyinstaller ^
    --onedir ^
    --distpath dist\sidecar ^
    --name PMA ^
    --noconfirm ^
    --clean ^
    --hidden-import=app ^
    --hidden-import=app.main ^
    --hidden-import=app.config ^
    --hidden-import=app.api ^
    --hidden-import=uvicorn ^
    --hidden-import=uvicorn.logging ^
    --hidden-import=uvicorn.loops ^
    --hidden-import=uvicorn.loops.auto ^
    --hidden-import=uvicorn.protocols ^
    --hidden-import=uvicorn.protocols.http ^
    --hidden-import=uvicorn.protocols.http.auto ^
    --hidden-import=uvicorn.protocols.websockets ^
    --hidden-import=uvicorn.protocols.websockets.auto ^
    --hidden-import=uvicorn.lifespan ^
    --hidden-import=uvicorn.lifespan.on ^
    --hidden-import=aiosqlite ^
    --hidden-import=lancedb ^
    --hidden-import=sentence_transformers ^
    --collect-data=sentence_transformers ^
    --collect-data=tokenizers ^
    --collect-data=transformers ^
    --add-data "app;app" ^
    --add-data "static;static" ^
    --add-data ".env;." ^
    __main__.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   PMA.exe built successfully!
echo   Output: %PROJECT_DIR%\dist\sidecar\PMA\PMA.exe
echo ============================================================
echo.

echo [Optional] Creating standalone ZIP...
if exist "dist_readme.txt" copy "dist_readme.txt" "dist\sidecar\PMA\README.txt"
powershell -Command "Compress-Archive -Path 'dist\sidecar\PMA\*' -DestinationPath 'dist\PMA-sidecar.zip' -Force"

:: Detect if run interactively (double-click) vs headless
echo %CMDCMDLINE% | findstr /i "/c" >nul 2>&1
if %ERRORLEVEL% == 0 pause
