# YT Down Sub Electron

YouTube 影片 / 本機影音檔下載 + 轉逐字稿工具。Electron 桌面殼 + Python 後端，全自動安裝，不需要事先裝 Python、Node.js 或任何開發工具。

## 下載

用 `git clone` 這個 repo，或在 GitHub 頁面按「Code → Download ZIP」解壓縮。

## 第一次執行

雙擊：

- `01_啟動_GPU版.bat`：有 NVIDIA 獨立顯卡就用這個，語音辨識會快很多
- `02_啟動_CPU版.bat`：沒有獨顯，或不確定，用這個

第一次執行會自動偵測缺什麼、自動下載安裝，不需要手動做任何事，只要有網路、耐心等：

1. 可攜式 Python 執行環境，含 yt-dlp、faster-whisper 等套件（CPU 版約 1GB；GPU 版含 PyTorch，約 5GB）
2. 可攜式 Node.js（約 33MB）
3. Electron（`npm install`，約 100MB 以上）
4. ffmpeg / ffprobe（約 170MB）

全部裝完會自動開啟視窗。之後每次執行都會跳過這一步，幾秒內直接開視窗。

**GPU 版偵測不到 CUDA 顯卡時會自動改用 CPU 模式**，不會直接壞掉。

## 第一次按「轉錄」時，還有一次獨立下載

第一次真正執行轉錄工作時，會另外自動下載語音辨識模型 **Breeze-ASR-25**（繁體中文/中英夾雜強化版，約 3GB）。這步跟開頭的環境安裝是分開的，只有第一次轉錄才會觸發，那一次會等比較久。

## 磁碟空間 / 網路需求估算

| 模式 | 環境安裝 | 語音模型（首次轉錄） | 合計 |
|---|---|---|---|
| CPU | 約 1.3GB | 約 3GB | 約 4.3GB |
| GPU | 約 5.3GB | 約 3GB | 約 8GB+ |

全程都需要網路連線（下載來源：nuget.org、nodejs.org、npm、GitHub Releases、Hugging Face）。

## 功能

- 貼 YouTube 網址下載，或用原生檔案視窗選本機影音檔轉錄
- 下載格式（音訊/影片）、是否產生字幕、已存在則跳過，逐筆設定
- 下載與轉錄是兩條獨立背景佇列，互不卡住
- 下載被 YouTube 限流時自動標記待重試，不逐支硬試
- 轉錄支援暫停/繼續；可勾選多筆項目一次取消
- 逐字稿/字幕自動附上來源資訊（頻道、網址、上傳日期）；找不到時會用檔名反查 YouTube 並跟下載記錄比對，避免比對錯誤
- 每天的下載/轉錄結果自動彙整成 `index.html` 總覽頁

## 已知限制

- 只在 Windows 10/11 測試過（`bootstrap.ps1` 用的是 Windows 內建 PowerShell）
- 專案資料夾路徑太深（超過約 110 字元）時，PyTorch 安裝可能因 Windows 長路徑限制失敗；出現異常時把整個資料夾移到路徑較短的地方（例如 `C:\ytelectron`）重新執行
- 打包成安裝檔（.exe 安裝程式）目前還沒做，現況是「解壓縮＋雙擊 bat」

## 開發者

原始碼結構、後端 API、測試方式見 `backend/`、`electron/` 底下的程式碼與 `docs/diagrams/` 的 UML 說明圖。
