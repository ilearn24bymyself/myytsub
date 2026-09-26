@echo off
chcp 65001 > nul
echo =========================================
echo  YT Down Sub Electron - GPU Mode
echo =========================================
echo.
set MODE=GPU

:: 檢查 Python、Node.js、Electron、ffmpeg/ffprobe 是否齊全，缺什麼就自動跑 bootstrap.ps1 補齊
if not exist "venv\python.exe" goto :need_setup
if not exist "node_portable\node.exe" goto :need_setup
if not exist "node_modules\electron" goto :need_setup
if not exist "backend\bin\ffmpeg.exe" goto :need_setup
if not exist "backend\bin\ffprobe.exe" goto :need_setup
goto :setup_done

:need_setup
echo [System] 首次執行，開始自動安裝執行環境(視網速可能要10-30分鐘，請耐心等候)...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"
if not exist "venv\python.exe" (
    echo.
    echo [錯誤] 自動安裝失敗，請檢查網路連線後重新執行這個檔案。
    pause
    exit /b
)

:setup_done
echo [System] Starting Electron app...
echo.
set PYTHON_EXE=%~dp0venv\python.exe
"%~dp0node_portable\node.exe" "%~dp0node_modules\electron\cli.js" "%~dp0." 2>> startup.log
echo.
echo [System] App closed.
pause
