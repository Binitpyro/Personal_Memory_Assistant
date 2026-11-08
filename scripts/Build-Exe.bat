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
        echo [1/4] Building React frontend...
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
echo [2/4] Ensuring PyInstaller is available...
uv pip install pyinstaller --quiet 2>nul || pip install pyinstaller --quiet

:: ── Stage the bundled models (first run needs no network) ─────────────────
echo [2b/4] Staging bundled models from the HuggingFace cache...
:: The PowerShell lives at the end of this file, after `exit /b`, so cmd never runs it.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = [IO.File]::ReadAllText('%~f0'); iex $s.Substring($s.LastIndexOf('#STAGE_MODELS#'))"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Model staging failed: a pinned model file is missing or fails its digest.
    if "%IS_CI%"=="0" pause
    exit /b 1
)

:: ── Run PyInstaller ───────────────────────────────────────────────────────
echo [3/4] Building PMA.exe with PyInstaller...

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
    --add-data "app/assets;app/assets" ^
    --add-data "app/storage/schema.sql;app/storage" ^
    --add-data "static;static" ^
    --add-data "demo_data;demo_data" ^
    --add-data "app/ocr/worker;app/ocr/worker" ^
    --add-data "app/ocr/protocol.py;app/ocr" ^
    --add-data "app/embeddings/gpu/worker.py;app/embeddings/gpu" ^
    --add-data "models.lock.json;." ^
    --add-data "dist/bundled/models;models" ^
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

:: ── Build the desktop app and copy it into dist ───────────────────────────
:: CI runs `tauri build` as its own workflow step (release.yml), so skip it there.
if "%IS_CI%"=="1" goto :desktop_done
echo [4/4] Building the desktop app (Tauri)...
pushd frontend
call npx tauri build --bundles msi --config src-tauri/tauri.bundle.conf.json
set "TAURI_EXIT=%ERRORLEVEL%"
popd
if not "%TAURI_EXIT%"=="0" (
    echo [ERROR] Tauri build failed.
    pause
    exit /b 1
)
:: The unbundled exe looks for its sidecar at python\PMA-sidecar.zip beside itself.
set "TAURI_OUT=frontend\src-tauri\target\release"
if not exist "dist\app\python" mkdir "dist\app\python"
copy /y "%TAURI_OUT%\pma-desktop.exe" "dist\app\" >nul || goto :copy_failed
copy /y "dist\PMA-sidecar.zip" "dist\app\python\" >nul || goto :copy_failed
copy /y "%TAURI_OUT%\bundle\msi\*.msi" "dist\" >nul || goto :copy_failed
echo.
echo   Desktop app: %PROJECT_DIR%\dist\app\pma-desktop.exe
echo   Installer:   %PROJECT_DIR%\dist\*.msi
goto :desktop_done
:copy_failed
echo [ERROR] Copying the desktop app into dist failed. Is dist\app\pma-desktop.exe still running?
pause
exit /b 1
:desktop_done

:: Detect if run interactively (double-click) vs headless
if "%IS_CI%"=="0" (
    echo %CMDCMDLINE% | findstr /i "/c" >nul 2>&1
    if %ERRORLEVEL% == 0 (
        echo Closing in 30 seconds. Press any key to close now.
        timeout /t 30
    )
)
exit /b 0

#STAGE_MODELS# - PowerShell, run by step [2b/4] above; cmd never reaches it.
# Copies the embedder and reranker INT8 ONNX + tokenizer at the revisions pinned in
# models.lock.json from the local HuggingFace cache into dist\bundled\models\<name, / as _>\,
# which PyInstaller adds to the sidecar as <bundle>\models (model_integrity.bundled_model_dir).
# Every file is checked against its pinned SHA-256; a missing file or mismatch exits non-zero.
# Never downloads: the cache must already hold the pinned revision (CI fills it first, see
# .github/workflows/release.yml). The fp16 build is skipped - only the opt-in GPU worker reads it.
$ErrorActionPreference = 'Stop'
$root = (Get-Location).Path
$lock = (Get-Content -Raw -LiteralPath (Join-Path $root 'models.lock.json') | ConvertFrom-Json).models
$hub = if ($env:HF_HUB_CACHE) { $env:HF_HUB_CACHE }
       elseif ($env:HF_HOME) { Join-Path $env:HF_HOME 'hub' }
       else { Join-Path $env:USERPROFILE '.cache\huggingface\hub' }
$out = Join-Path $root 'dist\bundled\models'
if (Test-Path -LiteralPath $out) { Remove-Item -Recurse -Force -LiteralPath $out }
foreach ($name in 'BAAI/bge-small-en-v1.5', 'cross-encoder/ms-marco-MiniLM-L-6-v2') {
    $m = $lock.$name
    if (-not $m) { throw "$name is not pinned in models.lock.json" }
    $snap = Join-Path $hub ("models--" + ($m.repo_id -replace '/', '--') + "\snapshots\" + $m.revision)
    foreach ($f in $m.files.PSObject.Properties) {
        if ($f.Name -like '*_fp16.onnx') { continue }
        $src = Join-Path $snap $f.Name
        $dst = Join-Path $out (Join-Path ($name -replace '/', '_') $f.Name)
        if (-not (Test-Path -LiteralPath $src)) {
            throw "Missing $src - put $($m.repo_id)@$($m.revision) in the HF cache ($hub) first."
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
        Copy-Item -LiteralPath $src -Destination $dst  # follows the cache's symlinks
        # .NET, not Get-FileHash: powershell.exe launched from pwsh 7 (CI's default shell)
        # inherits pwsh's PSModulePath and cannot resolve Get-FileHash.
        $hash = [BitConverter]::ToString(
            [Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes($dst))) -replace '-', ''
        if ($hash -ne $f.Value.sha256) {  # -ne is case-insensitive; $hash is upper-case
            throw "Digest mismatch for $dst`n  got    $hash`n  pinned $($f.Value.sha256)"
        }
        Write-Host "[OK] $name/$($f.Name) ($((Get-Item -LiteralPath $dst).Length) bytes)"
    }
}
