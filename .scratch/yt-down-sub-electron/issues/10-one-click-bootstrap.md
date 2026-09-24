# 10: 一鍵啟動：自動裝 Python / Node.js / ffmpeg，GPU/CPU 模式選擇

**What to build:** 一個 `.bat` 進入點（比照舊專案 `3-4.Yt-down-sub` 的 `01_啟動_GPU版.bat` / `02_啟動_CPU版.bat` 慣例，分 GPU/CPU 兩種），雙擊後會先檢查本機是否已具備執行環境（可攜式 Python、可攜式 Node.js、ffmpeg/ffprobe），缺什麼就自動下載、解壓縮、安裝到本專案資料夾內，不需要使用者電腦事先裝好任何東西、也不需要系統管理員權限；環境就緒後啟動票 01 的 Electron 殼。沿用舊專案 `bootstrap.ps1` 裡已經驗證過的可攜式 Python 下載模式，新增等效的 Node.js 步驟。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 在一個乾淨的、還沒有 Python/Node.js/ffmpeg 的環境下（可用暫時改名/移除既有可攜式環境資料夾來模擬），雙擊 `.bat` 後會自動下載並設定好可攜式 Python，比照舊專案的下載來源與安裝方式
- [ ] 同樣情境下，自動下載並設定好可攜式 Node.js（官方 Windows 版 zip，解壓縮到本專案資料夾，不需要系統安裝）
- [ ] 同樣情境下，自動下載並設定好 ffmpeg/ffprobe（沿用舊專案既有的下載步驟）
- [ ] 全部環境就緒後，`.bat` 會接著啟動票 01 的 Electron 應用程式
- [ ] 已經具備環境時（第二次以後執行），`.bat` 只做檢查、不重新下載，啟動速度明顯比第一次快
- [ ] 提供 GPU 版與 CPU 版兩個進入點，行為與舊專案的兩個 `.bat` 對應
- [ ] 沿用舊專案 `bootstrap.ps1` 對「專案路徑太深導致 pip/PyTorch 安裝失敗」的提前警告訊息
- [ ] `.bat` 檔案本身遵守工作區規範：CRLF 換行、中文輸出前有 `chcp 65001`
