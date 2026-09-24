# 07: 偵測限流／IP 鎖定，剩餘項目標記待重試

**What to build:** 在 `downloader.py` 裡加入對限流/IP 鎖定訊號（例如 YouTube 的 429、bot-check 相關錯誤文字）的辨識，跟一般性下載失敗區分開來。Orchestrator 偵測到這個訊號時，不會只讓「那一項」失敗，而是立刻把該批次裡**剩下所有還沒開始**的 YouTube 下載項目標記為 `pending-retry`（不是 `error`），完全不嘗試它們——因為整個來源 IP 被鎖，逐項嘗試只會延長鎖定。已經完成的項目維持原狀不受影響。

**Blocked by:** 06

**Status:** ready-for-agent

- [ ] `downloader.py` 能辨識出限流/bot-check 類型的錯誤，跟其他一般錯誤（例如網路中斷、檔案損毀）區分開來（有對應的 `unittest`，用假造的錯誤文字/例外驗證判斷邏輯，不需要真的觸發 YouTube 限流）
- [ ] Orchestrator 的 `unittest` 測試：一批多個下載項目中，第 N 項觸發限流訊號，驗證：第 1 到 N-1 項維持它們原本的終態（`done` 或 `error`），第 N 項與其後全部項目變成 `pending-retry`，且第 N+1 項之後的假下載函式**完全沒有被呼叫過**（沒有嘗試就標記，不是嘗試後失敗才標記）
- [ ] 沒有任何自動重試或退避（backoff）邏輯在這個情境下被觸發
- [ ] 這批次結束後，index 反映所有項目最新狀態（含 `pending-retry` 的項目）
