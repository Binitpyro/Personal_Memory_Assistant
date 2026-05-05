@echo off
setlocal EnableDelayedExpansion
title PMA - Run Tests

:: Detect if the script was launched by double-clicking (interactively)
set "INTERACTIVE=0"
echo %cmdcmdline% | find /i "%~0" >nul
if not errorlevel 1 set "INTERACTIVE=1"

echo ============================================================
echo   PMA v0.0.69  -  Full Test Suite
echo ============================================================
echo.

cd /d "%~dp0\.."

:: Virtual environment check
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment missing. Run StartPMA.bat first.
    if "!INTERACTIVE!"=="1" pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"

:: Phase 1: Python tests
echo [1/3] Running Python tests...
call pytest tests/ --cov=app --cov-report=xml:coverage.xml --cov-report=term-missing --tb=short -q --basetemp=.pytest_temp
set PYTHON_EXIT=%ERRORLEVEL%

:: Phase 2: Rust unit tests
echo.
echo [2/3] Running Rust unit tests...
where cargo >nul 2>&1
if %ERRORLEVEL% equ 0 (
    call cargo test --lib --manifest-path app\scanner\rust_core\Cargo.toml
)
set RUST_EXIT=%ERRORLEVEL%

:: Phase 3: TypeScript / Frontend tests
echo.
echo [3/3] Running TypeScript tests...
if exist "frontend\node_modules\.bin\vitest" (
    pushd frontend
    call npm run test:coverage
    set TS_EXIT=!ERRORLEVEL!
    popd
) else (
    set TS_EXIT=0
)

echo.
echo ============================================================
echo   RESULTS SUMMARY
echo ============================================================
if "%PYTHON_EXIT%"=="0" (echo   [PASS] Python tests) else (echo   [FAIL] Python tests)
if "%RUST_EXIT%"=="0" (echo   [PASS] Rust tests) else (echo   [FAIL] Rust tests)
if "%TS_EXIT%"=="0" (echo   [PASS] TypeScript tests) else (echo   [FAIL] TypeScript tests)
echo ============================================================
echo.

if "!INTERACTIVE!"=="1" pause
exit /b 0
