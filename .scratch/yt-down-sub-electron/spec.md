Status: ready-for-agent

# YouTube / 本機影音下載轉逐字稿工具 — Electron 重寫版（候選 3）

## Problem Statement

使用者有一個個人工具（`3-4.Yt-down-sub`，Streamlit 架構）：輸入 YouTube 網址或上傳本機影音檔，下載/整理後轉成逐字稿與字幕，並產生當日總覽 index 頁。實際使用時遇到三個問題：

1. **本機檔案上傳很慢**：選擇「上傳本機影音檔」時，即使影片檔案本來就在自己電腦上，還是要等瀏覽器把整批檔案內容傳輸完成才能繼續（實測過一次選 22 個檔案、單檔最大到 0.8GB）。使用者手動把檔案放進當天的 `uploads` 資料夾也沒用，系統目前沒有替代輸入方式，逼你重新走一次瀏覽器上傳。
2. **限流時整批當日總覽不會產生**：一次可能下載幾十支影片，如果中途被 YouTube 限流（實際上是整個來源 IP 被鎖，不只是那一支影片失敗），當天的 index 總覽頁完全不會產生，即使前面已經有好幾支成功下載/轉錄完成。
3. **一個任務卡住，其他任務也做不了**：被限流時 yt-dlp 內部會自己重試、遲遲不丟出錯誤，這段期間畫面上的執行狀態一直停在「執行中」。使用者重新整理前端、改做「本機影片轉逐字稿」，因為狀態鎖沒解除，按下開始沒有任何反應。

調查後確認的根本原因：Streamlit 的瀏覽器上傳元件**本質上拿不到用戶端本地檔案的真實路徑**（瀏覽器沙盒限制，非 Streamlit 特有），所以「偵測到檔案已在目標資料夾就跳過複製」這件事在目前的上傳元件上做不到；同時整個應用程式的任務執行是單一記憶體內狀態鎖（`JobRunner.status`），跨任務類型共用同一把鎖，下載迴圈裡也沒有逐項的錯誤隔離，任何一項失敗會讓整批（包含已成功的部分）都拿不到 index 更新。

## Solution

用 Electron 打造一個原生桌面視窗殼取代瀏覽器介面：

- 選擇本機影片改用作業系統原生的「選擇檔案」對話框，直接拿到硬碟上的真實路徑，完全跳過「整批傳輸到伺服器」這一步。
- 背景任務執行方式從「單一狀態鎖」改成具備下列行為的排隊機制：
  - 同類型工作（例如多支 YouTube 下載）循序執行，避免同時對 YouTube 發太多請求
  - 不同類型工作（下載 vs. 本機轉錄）可以同時進行，兩者用的資源本來就不衝突（網路 vs. CPU/GPU）
  - 下載佇列中任何一項失敗，不會讓佇列裡其他項目跟著失敗
  - 偵測到限流 / IP 鎖定訊號時，立即跳過該批次剩餘所有 YouTube 下載項目，標記為「待重試」（不是「失敗」），不做自動重試（自動重試在 IP 被鎖期間只會繼續發請求、可能讓鎖定拖更久）；已完成的項目與非 YouTube 的工作不受影響，繼續進行
- 當日總覽 index 改成每完成一項就即時更新，不再要求整批全部成功才產生
- 一鍵啟動流程：使用者的電腦即使沒有預先安裝 Python、Node.js，啟動時也會自動下載可攜式版本到專案資料夾內、安裝相依套件、準備好模型與 ffmpeg/ffprobe（沿用既有專案已經驗證過的可攜式 Python 下載模式，比照擴充到 Node.js），最後啟動 Python 背景服務與 Electron 視窗

這次先求「本機能穩定跑、能一直改一直測」，不建置安裝檔/exe（見「Out of Scope」）。

## User Stories

1. As a user, I want to pick local video files through my operating system's native file picker, so that I don't have to wait for the entire batch to upload to a server process before processing can start.
2. As a user, I want to select many local files at once (tens of files, each up to a few GB), so that I can batch-process a day's worth of recordings without waiting for each one individually.
3. As a user, I want the app to receive the real filesystem path of each selected file, so that no copy/transfer step is needed for files already on my machine.
4. As a user, I want multiple YouTube download URLs to be processed one at a time, so that I don't trigger rate-limiting by hammering YouTube with parallel requests.
5. As a user, I want to be able to start a local-file transcription job while a YouTube download batch is still running, so that one slow/stuck operation doesn't block unrelated work.
6. As a user, I want a single failed download in a batch to not abort the rest of the batch, so that the other videos still get processed.
7. As a user, I want the app to detect when YouTube has rate-limited/blocked my connection, so that it can stop hammering YouTube instead of retrying and making the lockout worse.
8. As a user, when rate-limiting is detected, I want all remaining queued YouTube downloads in that batch to be marked "待重試" (pending retry) rather than "失敗" (failed), so that I know they simply weren't attempted yet, not that something is wrong with them.
9. As a user, I want to manually retry the "待重試" items once I believe YouTube's rate limit has cleared, so that I control when to try again instead of the app hammering YouTube automatically.
10. As a user, I want already-succeeded downloads/transcriptions in a batch to be unaffected when a later item in that batch fails or gets rate-limited, so that I don't lose completed work.
11. As a user, I want the day's index overview page to update incrementally as each video finishes, so that I can check progress at any time instead of waiting for the whole batch to finish (or never seeing it if something fails).
12. As a user, I want to see the current status of every queued/running/done/pending-retry/failed item, so that I know what's happening without guessing.
13. As a user, I want to cancel a download that's currently in progress, so that I can stop wasting time/bandwidth on something I no longer want.
14. As a user opening this project for the first time on a machine without Python installed, I want the launcher to automatically fetch a self-contained Python runtime into the project folder, so that I don't have to manually install Python myself.
15. As a user opening this project for the first time on a machine without Node.js installed, I want the launcher to automatically fetch a self-contained Node.js runtime into the project folder, so that I don't have to manually install Node.js myself.
16. As a user, I want the launcher to automatically fetch ffmpeg/ffprobe if missing, so that video/audio processing works without manual setup (this already works in the old tool; carry it forward).
17. As a user, I want the launcher to reuse an already-downloaded transcription model file if I copy one in from the old project, so that I don't have to re-download ~3GB again.
18. As a user, I want a single double-clickable entry point (a `.bat` file) that checks everything is ready and launches the app, so that starting the tool is a one-click action, consistent with the old tool's convention.
19. As a user, I want to choose GPU or CPU mode at launch (mirroring the old tool's `01_啟動_GPU版.bat` / `02_啟動_CPU版.bat`), so that I can use my GPU when available and fall back to CPU otherwise.
20. As a user, I want each day's downloads, transcripts, and index page organized into a dated folder (`YYYYMMDD/...`), so that the output structure stays consistent with how I've been organizing files in the old tool.
21. As a developer iterating on this app locally, I want to run and test changes without building an installer/executable every time, so that the bug-fix/iterate loop stays fast.
22. As a user, I only need this to run on Windows, so that I don't need to spend effort on macOS/Linux compatibility right now.
23. As a future recipient of this tool via GitHub, I want to get it running by cloning the repo and running one script, without needing Python or Node.js already installed on my machine, so that non-technical people can also use it (this repo ships source + the auto-setup launcher; a built installer/exe is a separate future effort, see Out of Scope).
24. As a user, I want this new project to be completely independent of the old `3-4.Yt-down-sub` project folder (separate git repo, separate copies of any reused files/models), so that I can keep using the old tool unaffected while this one is under active development.
25. As a user, I want the existing long-Windows-path warning behavior (from the old tool's bootstrap script) preserved, so that if this folder ends up nested too deep, I get a clear early warning instead of a confusing pip/PyTorch install failure 10+ minutes in.

## Implementation Decisions

- **Orchestrator module** (replaces `runner.py`'s single `JobRunner` lock): a queue-based coordinator with two lanes — a **download lane** (YouTube URL jobs, processed sequentially, one at a time) and a **transcription lane** (local-file and post-download transcription jobs, processed sequentially within its own lane). The two lanes run concurrently with each other. Each job in the download lane is wrapped in its own per-item error handling so one failure doesn't abort the rest of the lane's queue.
  - Job states: `pending` → `running` → `done` | `error` | `pending-retry`. `pending-retry` is used specifically for download jobs skipped after a rate-limit/IP-lock signal was detected; distinct from `error` (which means that specific item itself failed for some other reason).
  - Rate-limit/IP-lock detection: extend the existing rate-limit-unaware exception handling in the download path (today it treats any non-Windows-file-lock exception as an immediate hard failure) to recognize a rate-limit/bot-check signal specifically, and on detecting it, mark every *remaining not-yet-started* download job in the current batch as `pending-retry` without attempting them, rather than letting each one individually fail (or hang) against a still-locked connection.
  - No automatic retry/backoff for rate-limited items — user-initiated retry only (an orchestrator method to re-enqueue `pending-retry` jobs).
- **Index generation**: `index_builder.py`'s day-index build is invoked after *each* job (download or transcription) reaches a terminal state (`done`, `error`, or `pending-retry`), not only at full-batch completion. The existing "rebuild fully from disk each time" approach is kept (already designed to stay correct across multiple app launches on the same day); it's just called more often.
- **Local HTTP layer**: a small local-only HTTP server (backend) exposing the orchestrator's operations — enqueue download URL(s), enqueue local-file transcription(s) by real filesystem path, query current job/queue status, retry `pending-retry` jobs, cancel a running job. The Electron renderer talks to this server over `http://127.0.0.1:<dynamic-port>` (port chosen dynamically at startup, not hardcoded, per this workspace's existing port convention).
- **Electron main process**: opens the native OS file-picker dialog for local-file selection (multi-select) and hands the resulting real paths to the backend via the HTTP layer; spawns and manages the Python backend subprocess's lifecycle; opens the app window pointing at a local static frontend (plain HTML/JS, no framework decided yet — kept minimal) that renders queue/job status and calls the backend over HTTP.
- **Reused modules from `3-4.Yt-down-sub`** (copied into this repo, adapted where noted): `downloader.py` (yt-dlp wrapper — needs the rate-limit-signal detection added), `transcriber.py` (faster-whisper/Breeze-ASR-25 wrapper — usable largely as-is), `index_builder.py` (needs to be callable incrementally, not just as a one-shot full-batch-end call).
- **Bootstrap script**: extends the existing `bootstrap.ps1` pattern (portable Python via a NuGet-hosted embeddable distribution, downloaded into the project's own folder, no admin rights/system install needed) with an equivalent step for a portable Node.js (official Windows ZIP distribution, extracted locally into the project folder). ffmpeg/ffprobe bootstrap step is carried over unchanged. The long-path-length warning (Windows `MAX_PATH` risk for deeply-nested PyTorch installs) is carried over unchanged.
- **Data layout**: dated folders (`YYYYMMDD/downloads`, `YYYYMMDD/uploads`, `YYYYMMDD/transcripts`, `YYYYMMDD/index.html`) — same convention as the old tool, for familiarity, but this repo starts with no historical data (fresh folder, not migrated from the old tool).
- **Distribution model for this iteration**: GitHub repo with source code + the `.bat`/bootstrap launcher; a recipient runs the launcher directly. No built installer/exe in this spec — see Out of Scope.
- No login, no private-data storage beyond the user's own local files, no cloud AI calls (transcription stays 100% local via faster-whisper/Breeze-ASR-25), no API keys — unchanged from the old tool, confirmed via code exploration of `3-4.Yt-down-sub`.
- Windows-only target for this spec.

## Testing Decisions

- Good tests here exercise the **orchestrator's public interface** with fake/injected download and transcription callables — never real network calls, real ffmpeg/whisper execution, or internal orchestrator state. A test should be phrased as an observable scenario ("given a 3-item download batch where item 2 signals rate-limiting, item 1 stays `done`, items 2's remaining siblings become `pending-retry`, and item 3 is never attempted") rather than asserting on internal fields no caller outside the module would touch.
- Modules under test: the new orchestrator/queue module (concurrency-lane behavior, per-item error isolation, rate-limit-skip behavior, retry re-enqueue) and the incremental index-triggering behavior (that a terminal job state triggers an index rebuild call). The rate-limit *detection* itself (recognizing yt-dlp's specific error text/exception shape for a 429/bot-check) is tested at the `downloader.py` seam with a fake/injected process response, separately from the orchestrator-level "what happens once detected" behavior.
- Not unit-tested the same way: the Electron main process (native file dialog, window lifecycle) and the thin HTTP layer wrapping the orchestrator — verified by actually running the app end-to-end (manual smoke run), consistent with how this workspace has handled UI-heavy verification elsewhere (e.g. headless-browser screenshot verification in the sibling `3-8 texttouse` project) rather than introducing a UI test framework for a personal-scale tool.
- Test runner: Python's built-in `unittest` — this environment has no `pytest` installed (confirmed absent), and the sibling project `3-8 texttouse/_tools/tests` already established the `unittest` convention for this workspace; follow it here too.
- Prior art: `3-8 texttouse/_tools/tests/test_scaffold.py` and `test_verify.py` — plain `unittest.TestCase`, preferring real behavior over mocked internals wherever the cost is reasonable (e.g. `verify.py`'s tests actually invoke headless Chrome rather than mocking it), and only injecting fakes at the boundary where a real dependency would be slow, non-deterministic, or destructive (here: real YouTube/network calls and real GPU-bound transcription).

## Out of Scope

- Building a distributable installer or single-file executable (e.g. via `electron-builder`, PyInstaller) — this spec covers a locally-runnable development build only. Packaging is an explicit future step once the app is stable.
- Candidate 1 (the pywebview-based rewrite) — a separate spec, to be written after this candidate ships, per the user's stated build order ("先做候選 3 再回來做候選 1").
- macOS / Linux support.
- Migrating historical downloaded videos, transcripts, or index pages from the old `3-4.Yt-down-sub` project — this is a fresh project with no inherited data.
- Any modification to the old `3-4.Yt-down-sub` (Streamlit) project — it remains untouched and independently usable.
- Automatic retry/backoff of rate-limited downloads — explicitly rejected in favor of skip-and-mark-pending + manual user-initiated retry.
- Any cloud AI / external API integration for transcription or content analysis — stays 100% local, unchanged from the old tool's approach.
- Choosing a frontend UI framework/visual design — the frontend is a minimal placeholder for this spec; visual design work (style reference, UI polish) is a separate future step once the underlying queue/backend behavior is working.

## Further Notes

- **Reuse-from-existing-project inventory** (to copy into this new repo as a starting point): `downloader.py`, `transcriber.py`, `index_builder.py`, `requirements_GPU.txt` / `requirements_CPU.txt`, `models/breeze-asr-25/` (~2.9GB — copying avoids a fresh ~3GB download; note this project's copy will be independent of the old project's copy, per the user's "資料夾都各自獨立使用" instruction, so disk usage is duplicated by design), `bin/ffmpeg.exe` / `bin/ffprobe.exe` if present, and `bootstrap.ps1` as the starting point for this project's bootstrap script (extended with a Node.js step).
- **Why Electron over Tauri** (user's decision, recorded here for future reference): given the project is still under active bug-fixing/iteration and the user is not deeply technical, Electron's much larger ecosystem of prior art/troubleshooting resources outweighed Tauri's smaller output size for now. This can be revisited later since it doesn't lock in anything about the Python backend or orchestrator design.
- **Why the browser-upload problem can't be patched in the old tool**: confirmed via code exploration that Streamlit's `st.file_uploader` never exposes the client's local filesystem path (browser sandboxing) — this is true of any browser-rendered UI, not a Streamlit-specific shortcoming. Solving it required moving to a native desktop shell (Electron here) with a real OS file dialog.
- **User's stated end goal**: eventually share this on GitHub so other people can get it running by cloning + running the launcher, with no manual Python/Node install required on their end. This spec's bootstrap work directly serves that goal; only the "build a polished installer" step is deferred.
- Candidate 1 and Candidate 3 folders are fully independent (separate git repos, no shared code via symlink) per the user's explicit instruction, even though they solve the identical underlying problem via a different UI shell.
