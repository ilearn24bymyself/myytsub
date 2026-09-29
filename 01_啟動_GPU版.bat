@echo off
chcp 65001 > nul
echo =========================================
echo  YT Down Sub Electron - GPU Mode
echo =========================================
echo.
set MODE=GPU
:: Keep this file pure ASCII. cmd.exe reads batch files in blocks, and a UTF-8
:: multibyte character that straddles a block boundary gets garbled, so comment
:: text turns into bogus commands. Where that happens depends on exact byte
:: positions and even on the folder path length, so any non-ASCII byte is a risk.
:: User-facing Chinese text lives in bootstrap.ps1, which has a BOM.
:: bootstrap.ps1 decides what needs installing: the Python packages for THIS
:: flavor, Node.js, Electron, ffmpeg. Do not pre-check "does venv exist" here.
:: After a CPU install that would skip setup and run with the wrong packages.
echo [System] Checking runtime environment...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"
:: Both checks must pass: the bootstrap.ps1 exit code, because any failed step
:: exits 1, AND venv present. venv alone is not enough, since the Python step
:: runs before the Electron step. Paths use %~dp0 - this file's own folder -
:: never the directory it happened to be started from.
set SETUP_FAILED=0
if errorlevel 1 set SETUP_FAILED=1
if not exist "%~dp0venv\python.exe" set SETUP_FAILED=1
if "%SETUP_FAILED%"=="1" (
    echo.
    echo [ERROR] Automatic setup failed. Check your internet connection, then run this file again.
    echo [ERROR] The lines marked [ERROR] above show the reason. A brief network drop usually clears up if you just double-click this file again.
    pause
    exit /b 1
)

echo [System] Starting Electron app...
echo.
set PYTHON_EXE=%~dp0venv\python.exe
"%~dp0node_portable\node.exe" "%~dp0node_modules\electron\cli.js" "%~dp0." 2>> "%~dp0startup.log"
echo.
echo [System] App closed.
pause
