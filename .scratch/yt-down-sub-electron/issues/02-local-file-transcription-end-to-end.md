# 02: 本機檔案轉逐字稿，端到端

**What to build:** 使用者用票 01 的原生選檔功能挑一個本機影片檔，送進轉錄佇列（orchestrator 的轉錄線），真的呼叫 `transcriber.py`（從舊專案 `3-4.Yt-down-sub` 複製過來的 faster-whisper / Breeze-ASR-25 封裝）跑完轉錄，產生逐字稿檔案；轉錄完成後，當日 index 頁即時更新，反映這筆新完成的項目——不用等「整批」都做完。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 已把 `transcriber.py` 從 `3-4.Yt-down-sub` 複製進本專案（可直接沿用，或做最小調整以配合新的佇列介面）
- [ ] 選一個本機影片檔（用票 01 的路徑）並觸發轉錄，畫面上看得到這筆工作的狀態變化（例如 pending → running → done）
- [ ] 轉錄完成後，當天日期資料夾（`YYYYMMDD/transcripts/`）底下真的產生逐字稿檔案
- [ ] 轉錄完成後（不需要有其他工作一起跑），當日 `index.html` 立即反映這筆新完成的項目，不需要重啟程式或手動觸發
- [ ] Orchestrator 的協調層有對應的 `unittest` 測試：用假的（fake）轉錄函式取代真的 `transcriber.py`，驗證「一項轉錄工作完成後，觸發一次 index 重建」這個行為，不需要真的跑 GPU/CPU 推論
