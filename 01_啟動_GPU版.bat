@echo off
chcp 65001 > nul
echo =========================================
echo  YT Down Sub Electron - GPU Mode
echo =========================================
echo.
set MODE=GPU

:: 環境檢查/安裝一律交給 bootstrap.ps1 自己判斷(它會分別檢查 Python 套件是否
:: 裝的是「這次要求的 flavor」、Node.js、Electron、ffmpeg/ffprobe 是否齊全)。
:: 不在這裡用「檔案存不存在」自己先攔一次——之前這樣寫,GPU 版遇到之前用
:: CPU 版裝過的環境時,檔案都在就直接跳過安裝,結果拿 CPU 套件硬跑 GPU 模式。
:: 全都裝好時 bootstrap.ps1 自己幾秒內就會判斷完畢跳過,不會拖慢啟動。
echo [System] 檢查執行環境...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"
if not exist "venv\python.exe" (
    echo.
    echo [錯誤] 自動安裝失敗，請檢查網路連線後重新執行這個檔案。
    pause
    exit /b
)

echo [System] Starting Electron app...
echo.
set PYTHON_EXE=%~dp0venv\python.exe
"%~dp0node_portable\node.exe" "%~dp0node_modules\electron\cli.js" "%~dp0." 2>> startup.log
echo.
echo [System] App closed.
pause
