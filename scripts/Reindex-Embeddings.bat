@echo off
setlocal
cd "%~dp0.."
if not exist ".venv" (
    echo [ERROR] Virtual environment not found. Please activate it first.
    exit /b 1
)
call ".venv\Scripts\activate.bat"
python scripts\reindex_embeddings.py
endlocal
pause
