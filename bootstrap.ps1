# First-run auto-installer for 3-4-1.yt-down-sub-electron
# 由 01/02_啟動.bat 在缺 venv\python.exe、node_portable\node.exe 或
# backend\bin\ffmpeg.exe/ffprobe.exe 任一項時自動呼叫。
# 只需要 Windows 10/11 內建的 PowerShell,使用者電腦不需要事先裝 Python 或 Node.js。
# 每個步驟只在真的缺東西時才動作,所以每次啟動都呼叫這支腳本做檢查是安全、快速的。
#
# 沿用 3-4.Yt-down-sub 的 bootstrap.ps1 可攜式 Python 下載模式,新增可攜式 Node.js 那一步。

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
. (Join-Path $root "bootstrap_helpers.ps1")

# PyTorch 在 site-packages\torch\include\...\*.h 底下有很深的巢狀路徑,
# 加上 venv\Lib\site-packages\torch\include\... 這段前綴,最長的檔名組合起來
# 容易超過 Windows 傳統 260 字元的 MAX_PATH 限制,導致 pip install 在安裝到一半時
# 用一個看不懂的 OSError 默默失敗。提早警告,不要讓使用者等到 10 分鐘後才發現。
if ($root.Length -gt 110) {
    Write-Host "[Setup][WARNING] This folder's path is $($root.Length) characters long:"
    Write-Host "  $root"
    Write-Host "  PyTorch installation can fail on Windows if the path is too deep (long-path limit)."
    Write-Host "  If setup fails below, move this whole folder somewhere shorter (e.g. C:\ytelectron) and run it again."
    Write-Host ""
}

function Invoke-DownloadWithProgress {
    param(
        [Parameter(Mandatory)] [string]$Uri,
        [Parameter(Mandatory)] [string]$OutFile,
        [Parameter(Mandatory)] [string]$Label
    )
    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $request.AllowAutoRedirect = $true
    $response = $request.GetResponse()
    $totalBytes = $response.ContentLength
    $responseStream = $response.GetResponseStream()
    $fileStream = [System.IO.File]::Create($OutFile)

    $buffer = New-Object byte[] 65536
    $totalRead = 0
    $lastPrintedPercent = -1
    try {
        while ($true) {
            $read = $responseStream.Read($buffer, 0, $buffer.Length)
            if ($read -le 0) { break }
            $fileStream.Write($buffer, 0, $read)
            $totalRead += $read
            if ($totalBytes -gt 0) {
                $percent = [math]::Floor(($totalRead / $totalBytes) * 100)
                if ($percent -ne $lastPrintedPercent) {
                    $mbRead = [math]::Round($totalRead / 1MB, 1)
                    $mbTotal = [math]::Round($totalBytes / 1MB, 1)
                    Write-Host -NoNewline "`r[Setup] $Label : $percent% ($mbRead MB / $mbTotal MB)   "
                    $lastPrintedPercent = $percent
                }
            }
        }
    } finally {
        $fileStream.Close()
        $responseStream.Close()
        $response.Close()
    }
    Write-Host ""
}

# ============================================================
# 步驟 1/3:可攜式 Python(給 backend 用:yt-dlp、faster-whisper)
# ============================================================
$venvDir = Join-Path $root "venv"
$pythonExe = Join-Path $venvDir "python.exe"
$flavorMarker = Join-Path $venvDir "_installed_flavor.txt"
$requestedFlavor = if ($env:FORCE_CPU -eq "1") { "CPU" } else { "GPU" }
$requirementsFile = Join-Path $root "backend\requirements_$requestedFlavor.txt"

Write-Host "[Setup] 步驟1/3:安裝Python執行環境($requestedFlavor 模式)"

if (-not (Test-Path $pythonExe)) {
    Write-Host "[Setup] Runtime not found, installing portable Python (about 30MB)..."
    if (Test-Path $venvDir) { Remove-Item -Recurse -Force $venvDir }

    $pyZip = Join-Path $root "python_3.10.11.zip"
    $pyUrl = "https://www.nuget.org/api/v2/package/python/3.10.11"
    $ok = Invoke-WithRetry -Label "Python download" -Attempt {
        Invoke-DownloadWithProgress -Uri $pyUrl -OutFile $pyZip -Label "下載Python執行環境" | Out-Null
        $true
    }
    if (-not $ok) {
        Write-Host "[Setup][ERROR] Python download failed after retries. Check your internet connection and run this again."
        exit 1
    }

    $extractDir = Join-Path $root "_py_extract_tmp"
    if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
    Expand-Archive -Path $pyZip -DestinationPath $extractDir -Force

    Move-Item -Path (Join-Path $extractDir "tools") -Destination $venvDir
    Remove-Item -Recurse -Force $extractDir
    Remove-Item -Force $pyZip

    $pthFile = Join-Path $venvDir "python310._pth"
    if (Test-Path $pthFile) {
        (Get-Content $pthFile) -replace '^#import site$', 'import site' | Set-Content $pthFile
    }
    Write-Host "[Setup] Python執行檔已就緒。"
} else {
    Write-Host "[Setup] Python執行檔已存在,略過解壓縮這步。"
}

# 用「關鍵套件資料夾在不在 + flavor 是否吻合」判斷有沒有裝好,而不是只看 python.exe
# 在不在——pip install 半途被中斷(斷網、防毒、使用者提早關視窗)時 python.exe 會留下來
# 但套件是空的,下次啟動不能被誤判成「已裝好」。
$fasterWhisperMarker = Join-Path $venvDir "Lib\site-packages\faster_whisper"
$hasMarker = Test-Path $flavorMarker
$installedFlavor = if ($hasMarker) { (Get-Content $flavorMarker -Raw).Trim() } else { "" }
$packagesOk = (Test-Path $fasterWhisperMarker) -and ($installedFlavor -eq $requestedFlavor)

if (-not $packagesOk) {
    if (Test-Path $fasterWhisperMarker) {
        Write-Host "[Setup] 偵測到目前環境是用「$installedFlavor」模式安裝的,但這次啟動要求「$requestedFlavor」模式,重新安裝對應套件..."
    } else {
        Write-Host "[Setup] 套件尚未安裝完成(可能是上次安裝中途中斷),開始安裝..."
    }
    $sizeLabel = if ($requestedFlavor -eq "CPU") { "約1GB,不含PyTorch" } else { "約5GB,含PyTorch" }
    Write-Host "[Setup] 正在安裝套件($sizeLabel,這是整個安裝過程最花時間的部分,請耐心等候)..."
    & $pythonExe -m pip install --upgrade pip -q
    $ok = Invoke-WithRetry -Label "pip install" -Attempt {
        & $pythonExe -m pip install -r $requirementsFile | Out-Host
        ($LASTEXITCODE -eq 0) -and (Test-Path $fasterWhisperMarker)
    }
    if (-not $ok) {
        Write-Host "[Setup][ERROR] Package install failed after retries. Check your internet connection and run this again."
        exit 1
    }
    Set-Content -Path $flavorMarker -Value $requestedFlavor
    Write-Host "[Setup] 套件安裝完成。"
} else {
    Write-Host "[Setup] 套件已安裝完成($requestedFlavor 模式),略過。"
}

# ============================================================
# 步驟 2/3:可攜式 Node.js(給 Electron 殼用)
# ============================================================
Write-Host "[Setup] 步驟2/3:安裝Node.js執行環境"

$nodeDir = Join-Path $root "node_portable"
$nodeExe = Join-Path $nodeDir "node.exe"
$npmCmd = Join-Path $nodeDir "npm.cmd"

if (-not (Test-Path $nodeExe)) {
    Write-Host "[Setup] Node.js not found, installing portable Node.js (about 33MB)..."
    if (Test-Path $nodeDir) { Remove-Item -Recurse -Force $nodeDir }

    $nodeVersion = "22.11.0"
    $nodeZip = Join-Path $root "node_$nodeVersion.zip"
    $nodeUrl = "https://nodejs.org/dist/v$nodeVersion/node-v$nodeVersion-win-x64.zip"
    $ok = Invoke-WithRetry -Label "Node.js download" -Attempt {
        Invoke-DownloadWithProgress -Uri $nodeUrl -OutFile $nodeZip -Label "下載Node.js執行環境" | Out-Null
        $true
    }
    if (-not $ok) {
        Write-Host "[Setup][ERROR] Node.js download failed after retries. Check your internet connection and run this again."
        exit 1
    }

    $extractDir = Join-Path $root "_node_extract_tmp"
    if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
    Expand-Archive -Path $nodeZip -DestinationPath $extractDir -Force

    $innerFolder = Get-ChildItem -Path $extractDir -Directory | Select-Object -First 1
    Move-Item -Path $innerFolder.FullName -Destination $nodeDir
    Remove-Item -Recurse -Force $extractDir
    Remove-Item -Force $nodeZip
    Write-Host "[Setup] Node.js執行檔已就緒。"
} else {
    Write-Host "[Setup] Node.js執行檔已存在,略過解壓縮這步。"
}

$electronDir = Join-Path $root "node_modules\electron"
# 標記用 dist\electron.exe(真正的執行檔),不能只看 node_modules\electron 資料夾在不在:
# 執行檔是 npm 安裝後期才另外下載的,下載失敗時資料夾可能已經在了,但裡面沒有執行檔。
$electronMarker = Join-Path $electronDir "dist\electron.exe"
if (-not (Test-Path $electronMarker)) {
    Write-Host "[Setup] 正在安裝 Electron 及相依套件(第一次會下載約100MB以上,請耐心等候)..."
    $env:PATH = "$nodeDir;$env:PATH"
    $ok = Invoke-WithRetry -Label "npm install (Electron)" -Attempt {
        # 上一次失敗留下的半成品先清掉,否則 npm 可能判斷「已安裝」而不重新下載執行檔
        if (Test-Path $electronDir) { Remove-Item -Recurse -Force $electronDir }
        & $npmCmd install --prefix $root | Out-Host
        ($LASTEXITCODE -eq 0) -and (Test-Path $electronMarker)
    }
    if (-not $ok) {
        Write-Host "[Setup][ERROR] npm install failed after retries. Check your internet connection and run this again."
        exit 1
    }
    Write-Host "[Setup] Electron 安裝完成。"
} else {
    Write-Host "[Setup] Electron 已安裝完成,略過。"
}

# ============================================================
# 步驟 3/3:ffmpeg / ffprobe
# ============================================================
Write-Host "[Setup] 步驟3/3:下載ffmpeg/ffprobe"

$binDir = Join-Path $root "backend\bin"
$ffmpegExe = Join-Path $binDir "ffmpeg.exe"
$ffprobeExe = Join-Path $binDir "ffprobe.exe"

if (-not (Test-Path $ffmpegExe) -or -not (Test-Path $ffprobeExe)) {
    Write-Host "[Setup] ffmpeg/ffprobe not found, downloading (about 160MB)..."
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null

    $ffZip = Join-Path $root "ffmpeg_tmp.zip"
    $ffUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    $ok = Invoke-WithRetry -Label "ffmpeg download" -Attempt {
        Invoke-DownloadWithProgress -Uri $ffUrl -OutFile $ffZip -Label "下載ffmpeg" | Out-Null
        $true
    }
    if (-not $ok) {
        Write-Host "[Setup][ERROR] ffmpeg download failed after retries. Check your internet connection and run this again."
        exit 1
    }

    $ffExtractDir = Join-Path $root "_ffmpeg_extract_tmp"
    if (Test-Path $ffExtractDir) { Remove-Item -Recurse -Force $ffExtractDir }
    Expand-Archive -Path $ffZip -DestinationPath $ffExtractDir -Force

    $foundFfmpeg = Get-ChildItem -Path $ffExtractDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    $foundFfprobe = Get-ChildItem -Path $ffExtractDir -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
    if (-not $foundFfmpeg -or -not $foundFfprobe) {
        Write-Host "[Setup][ERROR] ffmpeg.exe/ffprobe.exe not found inside the downloaded archive. Install failed."
        exit 1
    }
    Copy-Item $foundFfmpeg.FullName $ffmpegExe -Force
    Copy-Item $foundFfprobe.FullName $ffprobeExe -Force

    Remove-Item -Recurse -Force $ffExtractDir
    Remove-Item -Force $ffZip
    Write-Host "[Setup] ffmpeg/ffprobe installed."
} else {
    Write-Host "[Setup] ffmpeg/ffprobe already present, skipping."
}

Write-Host "[Setup] All done!"
