# 03: YouTube 下載，端到端（單一網址）

**What to build:** 使用者輸入一個 YouTube 網址，送進下載佇列（orchestrator 的下載線），真的呼叫 `downloader.py`（從舊專案複製過來的 yt-dlp 封裝）完成下載；下載完成後，當日 index 頁即時更新反映這筆項目。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 已把 `downloader.py` 從 `3-4.Yt-down-sub` 複製進本專案（可直接沿用，或做最小調整以配合新的佇列介面）
- [ ] 輸入一個 YouTube 網址並觸發下載，畫面上看得到這筆工作的狀態變化（pending → running → done）
- [ ] 下載完成後，當天日期資料夾（`YYYYMMDD/downloads/`）底下真的產生影片檔案
- [ ] 下載完成後，當日 `index.html` 立即反映這筆新完成的項目
- [ ] Orchestrator 的協調層有對應的 `unittest` 測試：用假的（fake）下載函式取代真的 `downloader.py`，驗證「一項下載工作完成後，觸發一次 index 重建」這個行為，不需要真的連線 YouTube
