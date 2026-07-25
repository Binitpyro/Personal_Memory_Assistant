@echo off
setlocal EnableDelayedExpansion
title PMA - Run Tests

:: Detect if the script was launched by double-clicking (interactively)
set "INTERACTIVE=0"
echo %cmdcmdline% | find /i "%~0" >nul
if not errorlevel 1 set "INTERACTIVE=1"

echo ============================================================
echo   PMA v0.0.71  -  Full Test Suite
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
echo [1/4] Running Python tests...
call pytest tests/ --cov=app --cov-report=xml:coverage.xml --cov-report=term-missing --tb=short -q --basetemp=.pytest_temp
set PYTHON_EXIT=%ERRORLEVEL%

:: Phase 2: Rust unit tests
echo.
echo [2/4] Running Rust unit tests...
where cargo >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   [INFO] Running app/scanner/rust_core tests...
    call cargo test --lib --manifest-path app\scanner\rust_core\Cargo.toml
    set RUST_CORE_EXIT=!ERRORLEVEL!

    echo.
    echo   [INFO] Running frontend/src-tauri tests...
    set "OLD_PATH=!PATH!"
    set "PATH=%CD%\frontend\src-tauri\target\debug\deps;!PATH!"
    call cargo test --manifest-path frontend\src-tauri\Cargo.toml
    set RUST_TAURI_EXIT=!ERRORLEVEL!
    set "PATH=!OLD_PATH!"

    echo.
    echo   [INFO] Running Miri for Undefined Behavior detection...
    call cargo +nightly miri test --lib --manifest-path app\scanner\rust_core\Cargo.toml
    set MIRI_EXIT=!ERRORLEVEL!
) else (
    echo   [WARNING] cargo not found, skipping Rust tests.
    set RUST_CORE_EXIT=0
    set RUST_TAURI_EXIT=0
    set MIRI_EXIT=0
)

:: Phase 3: TypeScript / Frontend tests
echo.
echo [3/4] Running TypeScript tests...
if exist "frontend\node_modules\.bin\vitest" (
    pushd frontend
    call npm run test:coverage
    set TS_EXIT=!ERRORLEVEL!
    popd
) else (
    set TS_EXIT=0
)

:: Phase 4: Playwright E2E Integration tests
echo.
echo [4/4] Running Playwright E2E Integration tests...
if exist "frontend\node_modules\@playwright\test" (
    pushd frontend
    call npm run test:e2e
    set E2E_EXIT=!ERRORLEVEL!
    popd
) else (
    echo   [WARNING] Playwright not found, skipping E2E tests.
    set E2E_EXIT=0
)

echo.
echo ============================================================
echo   RESULTS SUMMARY
echo ============================================================
if "%PYTHON_EXIT%"=="0" (echo   [PASS] Python tests) else (echo   [FAIL] Python tests)
if "%RUST_CORE_EXIT%"=="0" (echo   [PASS] Rust Core tests) else (echo   [FAIL] Rust Core tests)
if "%RUST_TAURI_EXIT%"=="0" (echo   [PASS] Rust Tauri tests) else (echo   [FAIL] Rust Tauri tests)
if "%TS_EXIT%"=="0" (echo   [PASS] TypeScript tests) else (echo   [FAIL] TypeScript tests)
if "%E2E_EXIT%"=="0" (echo   [PASS] Playwright E2E tests) else (echo   [FAIL] Playwright E2E tests)
echo ============================================================
echo.

if "!INTERACTIVE!"=="1" pause

:: Propagate failures
if not "%PYTHON_EXIT%"=="0" exit /b %PYTHON_EXIT%
if not "%RUST_CORE_EXIT%"=="0" exit /b %RUST_CORE_EXIT%
if not "%RUST_TAURI_EXIT%"=="0" exit /b %RUST_TAURI_EXIT%
if not "%TS_EXIT%"=="0" exit /b %TS_EXIT%
if not "%E2E_EXIT%"=="0" exit /b %E2E_EXIT%

exit /b 0
