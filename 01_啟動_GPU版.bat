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
:: 兩道都要過才往下:bootstrap.ps1 的結束代碼(它任何一步失敗都會 exit 1),
:: 以及 venv 真的在。只看 venv 不夠——Python 那步成功、Electron 那步失敗時 venv 還是在。
:: 路徑一律用 %~dp0(這個 .bat 自己的位置),不依賴「從哪個資料夾執行」。
set SETUP_FAILED=0
if errorlevel 1 set SETUP_FAILED=1
if not exist "%~dp0venv\python.exe" set SETUP_FAILED=1
if "%SETUP_FAILED%"=="1" (
    echo.
    echo [錯誤] 自動安裝失敗，請檢查網路連線後重新執行這個檔案。
    echo [錯誤] 上面最後幾行標示 [ERROR] 的就是失敗原因，網路瞬斷時重新雙擊一次通常就會成功。
    pause
    exit /b 1
)

echo [System] Starting Electron app...
echo.
set PYTHON_EXE=%~dp0venv\python.exe
"%~dp0node_portable\node.exe" "%~dp0node_modules\electron\cli.js" "%~dp0." 2>> startup.log
echo.
echo [System] App closed.
pause
