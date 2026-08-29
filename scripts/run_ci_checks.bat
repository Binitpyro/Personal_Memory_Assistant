@echo off
setlocal EnableDelayedExpansion

:: Detect if the script was launched by double-clicking (interactively)
set "INTERACTIVE=0"
echo %cmdcmdline% | find /i "%~0" >nul
if not errorlevel 1 set "INTERACTIVE=1"

:: Change to the project root directory
cd /d "%~dp0\.."

echo ============================================================
echo Running CI/CD checks for Personal Memory Assistant
echo ============================================================

:: Virtual environment check
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment missing. Run StartPMA.bat first or manually create it with 'uv venv'.
    goto :error
)
call ".venv\Scripts\activate.bat"

echo.
echo [0/4] Syncing dependencies...
call uv sync --all-extras
if %ERRORLEVEL% NEQ 0 (
    echo Dependency sync failed!
    goto :error
)

echo.
echo [0.5/4] Compiling Rust extension...
cd app\scanner\rust_core
call uv run maturin develop --release
if %ERRORLEVEL% NEQ 0 (
    cd ..\..\..
    echo Maturin develop failed!
    goto :error
)
cd ..\..\..

echo.
echo [1/4] Running Ruff (Lint ^& Format Check)...
call uv run ruff check . --output-format=concise --output-file=ruff-report.txt
call uv run ruff check .
if %ERRORLEVEL% NEQ 0 (
    echo Ruff check failed!
    goto :error
)
call uv run ruff format --check .
if %ERRORLEVEL% NEQ 0 (
    echo Ruff format check failed! Run 'uv run ruff format .' to fix.
    goto :error
)

echo.
echo [2/4] Running MyPy (Type Checks)...
call uv run mypy .
if %ERRORLEVEL% NEQ 0 (
    echo MyPy check failed!
    goto :error
)

echo.
echo [3/4] Running Fast-Path Golden Tests...
call uv run pytest tests/ -v --basetemp=.pytest_temp --junitxml=pytest-report.xml
if %ERRORLEVEL% NEQ 0 (
    echo Pytest failed!
    goto :error
)

echo.
echo [4/6] Running Bandit (Python Security)...
REM No -lll. It reported HIGH only, and there are no HIGH findings, so this
REM step could never fail locally while .github/workflows/ci.yml runs bandit
REM at its default severity and fails on MEDIUM. Same command both sides now;
REM accepted findings are suppressed per-site with `# nosec B###`.
call uv run bandit -r app -f json -o bandit-report.json
if %ERRORLEVEL% NEQ 0 (
    echo Bandit security check failed!
    goto :error
)

echo.
echo [5/6] Running ESLint, frontend build, and the utility diff...
cd frontend
call npx eslint . --format json -o eslint-report.json
if %ERRORLEVEL% NEQ 0 (
    cd ..
    echo ESLint security check failed!
    goto :error
)
call npm run build
if %ERRORLEVEL% NEQ 0 (
    cd ..
    echo Frontend build failed!
    goto :error
)
REM Tailwind emits NOTHING for a class it does not know - no build error, no
REM lint warning, no failing test. The UI/UX audit found 58 such usages across
REM 22 names, several on error text. This is the guard that keeps it at zero.
REM Must stay in step with .github/workflows/ci.yml - section 13 records the
REM local gate and CI drifting apart twice already.
call node scripts/check-utilities.mjs
if %ERRORLEVEL% NEQ 0 (
    cd ..
    echo Utility diff failed: a class used in src/ produces no CSS!
    goto :error
)
cd ..

echo.
echo [6/6] Running Rust Security Checks...

echo   - Checking Extraction Core (app\scanner\rust_core)
cd app\scanner\rust_core
call cargo clippy --message-format=json > sonar-issues.json
if %ERRORLEVEL% NEQ 0 (
    cd ..\..\..
    echo Clippy check failed for rust_core!
    goto :error
)
call cargo deny check
if %ERRORLEVEL% NEQ 0 (
    cd ..\..\..
    echo Cargo deny check failed for rust_core!
    goto :error
)
call cargo audit
if %ERRORLEVEL% NEQ 0 (
    cd ..\..\..
    echo Cargo audit check failed for rust_core!
    goto :error
)
cd ..\..\..

echo   - Checking Desktop Shell (frontend\src-tauri)
cd frontend\src-tauri
call cargo clippy --message-format=json > sonar-issues.json
if %ERRORLEVEL% NEQ 0 (
    cd ..\..
    echo Clippy check failed for src-tauri!
    goto :error
)
call cargo deny check
if %ERRORLEVEL% NEQ 0 (
    cd ..\..
    echo Cargo deny check failed for src-tauri!
    goto :error
)
:: Ignores live in frontend\src-tauri\.cargo\audit.toml, which is the only path
:: cargo-audit reads. No --ignore flags here: a developer running `cargo audit`
:: by hand must see exactly what CI sees.
call cargo audit
if %ERRORLEVEL% NEQ 0 (
    cd ..\..
    echo Cargo audit check failed for src-tauri!
    goto :error
)
cd ..\..

echo.
echo ============================================================
echo All CI/CD checks passed successfully!
echo ============================================================
if "%INTERACTIVE%"=="1" pause
exit /b 0

:error
if "%INTERACTIVE%"=="1" pause
exit /b 1
