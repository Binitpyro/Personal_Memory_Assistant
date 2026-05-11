@echo off
:: Build-Exe.bat — Builds PMA.exe via PyInstaller
:: Run from anywhere; always executes from the project root.

:: ── Enforce project-root context ──────────────────────────────────────────
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%"
echo [INFO] Working directory: %CD%

:: Detect CI environment
set "IS_CI=0"
if defined GITHUB_ACTIONS set "IS_CI=1"

:: ── Verify virtual environment ────────────────────────────────────────────
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] .venv not found. Run StartPMA.bat or 'uv sync' first.
    if "%IS_CI%"=="0" pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"

:: ── Build React frontend (sidecar needs static assets) ────────────────────
if "%IS_CI%"=="1" (
    echo [INFO] CI detected — skipping redundant frontend build in batch script.
) else (
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
)

:: ── Install / upgrade PyInstaller ─────────────────────────────────────────
echo [2/3] Ensuring PyInstaller is available...
uv pip install pyinstaller --quiet 2>nul || pip install pyinstaller --quiet

:: ── Run PyInstaller ───────────────────────────────────────────────────────
echo [3/3] Building PMA.exe with PyInstaller...

set "OPTIONAL_ENV="

pyinstaller ^
    --onedir ^
    --distpath dist\sidecar ^
    --name PMA ^
    --noconfirm ^
    --clean ^
    --noconsole ^
    --hidden-import=app ^
    --hidden-import=app.main ^
    --hidden-import=app.config ^
    --hidden-import=app.api ^
    --hidden-import=app.scanner.rust_core ^
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
    --hidden-import=onnxruntime ^
    --hidden-import=tokenizers ^
    --add-data "app;app" ^
    --add-data "static;static" ^
    --add-binary "app/scanner/rust_core/target/release/rust_core.dll;app/scanner/rust_core/target/release" ^
    __main__.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] PyInstaller build failed.
    if "%IS_CI%"=="0" pause
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
:: Using native tar.exe (Windows 10+) for 10x-50x faster compression than PowerShell
tar -a -c -f "dist\PMA-sidecar.zip" -C "dist\sidecar" PMA

:: Detect if run interactively (double-click) vs headless
if "%IS_CI%"=="0" (
    echo %CMDCMDLINE% | findstr /i "/c" >nul 2>&1
    if %ERRORLEVEL% == 0 (
        echo Closing in 30 seconds. Press any key to close now.
        timeout /t 30
    )
)
