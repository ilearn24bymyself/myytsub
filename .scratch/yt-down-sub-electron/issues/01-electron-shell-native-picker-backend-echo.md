# 01: Electron 骨架 + 原生選檔 + Python 後端連通

**What to build:** 一個可以雙擊/指令啟動的 Electron 視窗，畫面上有一個按鈕會叫出作業系統原生的「選擇檔案」對話框（支援多選）。選完後，Electron 主行程把每個檔案的真實磁碟路徑，透過本機 HTTP 呼叫送給同時啟動、常駐執行的 Python 後端；後端原樣把收到的路徑清單回傳，畫面上列出來。這張票不含任何真的下載/轉錄邏輯，純粹證明「Electron 視窗 ↔ 本機 HTTP ↔ Python 後端」這條路徑通了，且拿到的是磁碟真實路徑（不是瀏覽器上傳那種只有檔名+內容位元組）。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] `npm start`（或等效指令）能開出一個原生 Electron 視窗，不是瀏覽器分頁
- [ ] 視窗上的按鈕會叫出 Windows 原生的多選檔案對話框
- [ ] 選檔後，畫面顯示的是選到檔案的完整磁碟路徑（例如 `C:\Users\...\video.mp4`），不是只有檔名
- [ ] Python 後端在本機動態取得的 port 上常駐執行（不寫死 port），Electron 主行程負責啟動與結束它的生命週期
- [ ] Electron 把選到的路徑清單以 HTTP 呼叫送給 Python 後端，後端回傳同一份清單，畫面上顯示出後端回傳的內容（證明資料真的走了一趟後端，不是純前端本地顯示）
- [ ] 關閉 Electron 視窗時，Python 後端行程一併結束，不留下殘留行程
