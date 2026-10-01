"""本機 HTTP 服務:Electron 渲染畫面透過這層跟 orchestrator 溝通。

只服務本機單一使用者(Electron 自己),不考慮多使用者併發存取安全性問題。
同時把 electron/renderer 靜態檔案(index.html/renderer.js)也從這個 server 生出去,
這樣 Electron 視窗直接載入 http://127.0.0.1:<port>/,不會有跨來源(CORS)問題。
"""
import json
import mimetypes
import os
import sys
import threading
import traceback
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent  # 專案根目錄
STATIC_DIR = BASE_DIR / "electron" / "renderer"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from orchestrator import Orchestrator, JobState, JobType  # noqa: E402
from batch_completion import BatchCompletion  # noqa: E402
from downloader import download_media, extract_video_id, fetch_metadata, fetch_title_only, list_entries  # noqa: E402
from yt_dlp.utils import sanitize_filename  # noqa: E402
from transcriber import Transcriber  # noqa: E402
from index_builder import build_day_index  # noqa: E402
import download_record  # noqa: E402
import ledger  # noqa: E402
import source_lookup  # noqa: E402
import source_sidecar  # noqa: E402


AUTO_RETRY_SECONDS = 6 * 60 * 60   # 被限流的下載,等 6 小時自動重試,再被限流就再等 6 小時
DEFAULT_GAP_MINUTES = 5   # 頻道/播放清單連續下載時,每支之間等幾分鐘(畫面可調,0=不等)


def today_dir() -> Path:
    d = BASE_DIR / date.today().strftime("%Y%m%d")
    (d / "downloads").mkdir(parents=True, exist_ok=True)
    (d / "uploads").mkdir(parents=True, exist_ok=True)
    (d / "transcripts").mkdir(parents=True, exist_ok=True)
    return d


def _open_folder(path):
    """跟舊版 3-4 一樣,一批做完自動用檔案總管打開對應資料夾。
    開不起來(資料夾不在、不是 Windows、檔案總管出問題)就算了,不能拖累工作本身。"""
    try:
        if hasattr(os, "startfile") and os.path.isdir(path):
            os.startfile(path)
    except Exception:
        pass


def open_result_folders(done_types):
    """只開這批「實際有成功」的那種工作對應的資料夾:有下載成功開下載資料夾,
    有轉錄成功開逐字稿資料夾(只轉錄本機檔案就只開逐字稿)。"""
    d = today_dir()
    if JobType.DOWNLOAD in done_types:
        _open_folder(d / "downloads")
    if JobType.TRANSCRIBE in done_types:
        _open_folder(d / "transcripts")


def _find_previously_downloaded_path(url: str) -> str | None:
    """影片被 yt-dlp 的 download_archive 記錄過、這次沒有真的下載時,盡量找回
    之前存在哪裡:先查 download_record(有記錄的話最快、不用打網路);查不到
    再用 video 標題(另外打一次輕量 yt-dlp 查詢,不受 archive 影響)去比對磁碟上
    實際的檔名——這條路徑涵蓋 download_record 上線前就下載過的舊資料。"""
    video_id = extract_video_id(url)
    if video_id:
        found = download_record.find_path_by_video_id(video_id)
        if found:
            return found

    title = fetch_title_only(url)
    if not title:
        return None
    return download_record.find_path_by_title(str(BASE_DIR), sanitize_filename(title))


def make_real_download_fn(orchestrator, queued_paths=None):
    """下載完成後自動接一個轉錄工作,轉錄選項(是否要字幕/跳過已存在)由呼叫端
    在 payload 裡指定,這裡原樣轉給轉錄工作,不自己決定預設值。
    build_day_index() 只掃 transcripts/*.txt,不看 downloads/,所以純下載的
    項目要有轉錄工作接手,index 才有東西可以反映(票 03)。
    metadata 隨 payload 一起轉給轉錄工作(自動接鏈當下就有,不用等查記錄檔),
    同時也存一份到 download_record,這樣使用者之後手動挑同一份檔案轉錄
    (例如下載中斷分好幾次、或只是想重新產生逐字稿)也查得回出處。

    下載前先只列清單(頻道/播放清單可能幾百支),逐支對照「總清單」(掃描所有日期資料夾):
    已有逐字稿的略過(可關)、已下載但沒轉錄的直接補排轉錄、全新的才真的下載。
    payload 的 download_only=True 代表只下載不轉錄。
    queued_paths:已經排過轉錄的檔案(各個下載工作共用):總清單只在工作開頭掃一次,
    前一個工作排的轉錄還沒做完時,同一個檔案在下一個工作看起來還是「沒有逐字稿」,
    不記住的話會重複排轉錄。"""
    queued = queued_paths if queued_paths is not None else set()

    def _download(payload, cancel_event, pause_event, report_progress):
        d = today_dir()
        handled = set()
        handled_ids = set()
        download_only = bool(payload.get("download_only", False))

        def _progress(percent):
            report_progress(percent, "下載中")

        def _refresh_summary():
            try:
                ledger.write_summary(BASE_DIR)
            except Exception:
                traceback.print_exc()  # 總清單寫不出來不能擋住下載

        def _queue_transcription(path, metadata):
            if download_only:
                return
            key = os.path.normcase(os.path.abspath(path))
            if key in queued:
                return
            queued.add(key)
            orchestrator.enqueue_transcription({
                "path": path,
                "want_srt": payload.get("want_srt", True),
                "skip_existing": payload.get("skip_existing", True),
                "metadata": metadata,
            })

        def _handle(entry):
            # 每一支完成當下就處理:寫影片旁資訊檔、存記錄、排轉錄。一個網址裡後面的影片
            # 被限流時整個 download_media 會丟例外,等它回傳才處理的話前面完成的全部遺失
            key = os.path.normcase(os.path.abspath(entry["path"]))
            # 同一支影片換了路徑也算處理過:回傳清單的「前 15 字比對」可能找到另一集的檔案
            if key in handled or (entry.get("video_id") and entry["video_id"] in handled_ids):
                return
            handled.add(key)
            if entry.get("video_id"):
                handled_ids.add(entry["video_id"])
            try:
                source_sidecar.write(entry["path"], entry)
            except Exception:
                traceback.print_exc()  # 資訊檔寫不了不能擋住轉錄
            download_record.save([entry])
            _queue_transcription(entry["path"], entry)

        try:
            entries = list_entries(payload["url"])
            if entries is None:
                # 讀不到清單:退回老做法,整個網址交給 yt-dlp 一次處理(沒有標題可以預先判斷)
                entries = [{"url": payload["url"], "title": None, "video_id": None, "channel": None, "upload_date": None}]
            known = ledger.scan(BASE_DIR)
            skip_transcribed = payload.get("skip_transcribed", True)
            gap_seconds = _gap_seconds(payload)
            skipped = 0
            downloaded_any = False

            for position, item in enumerate(entries):
                if cancel_event.is_set():
                    return
                stem = sanitize_filename(item["title"]) if item.get("title") else None
                has_transcript = bool(stem) and known.has_transcript(stem)
                existing_path = known.downloaded_path(stem) if stem else None
                if has_transcript and skip_transcribed:
                    skipped += 1
                    continue
                if existing_path:
                    skipped += 1
                    if not has_transcript and not known.downloaded_is_archived(stem):
                        # 下載過、卻沒有逐字稿(例如中途重開機、轉錄佇列沒了):補排轉錄
                        _queue_transcription(existing_path, {
                            "path": existing_path, "title": item["title"], "channel": item.get("channel"),
                            "url": item["url"], "upload_date": item.get("upload_date"),
                            "video_id": item.get("video_id")})
                    continue
                if downloaded_any and gap_seconds > 0:
                    # 連續下載很快會被 YouTube 限流:每支之間等一下(只在「下一支真的要下載」時才等,
                    # 略過的、第一支、最後一支之後都不等)。用取消旗標等待,按取消會立刻醒來
                    report_progress(100.0 * position / len(entries),
                                    f"等待 {gap_seconds / 60:g} 分鐘後下載下一支(降低被限流的機率)")
                    if cancel_event.wait(gap_seconds):
                        return
                handled_before = len(handled)
                downloaded = download_media(
                    item["url"], output_dir=str(d / "downloads"),
                    format_type=payload.get("format_type", "audio"),
                    progress_callback=_progress, stop_event=cancel_event,
                    on_item_done=_handle,
                )
                # 檔案鎖定重試救回來的檔案不會經過即時回報,這裡補處理(已處理過的會跳過)
                for entry in downloaded:
                    _handle(entry)
                # 真的有下載到東西才算(yt-dlp 因 archive 靜默略過、沒有新檔案時,下一支不必等)
                if len(handled) > handled_before:
                    downloaded_any = True

            if not handled:
                if len(entries) == 1 and skipped == 0:
                    # yt-dlp 的 download_archive 記錄過這支影片時,download_media() 會
                    # 靜默回傳空陣列(不下載、不報錯)。這裡刻意不要讓它看起來跟正常
                    # 完成一樣——沒有檔案、也沒有接轉錄工作,要讓使用者知道原因,並且
                    # 盡量把之前下載到哪裡也一併告訴使用者。
                    found_path = _find_previously_downloaded_path(entries[0]["url"])
                    if found_path:
                        report_progress(100.0, f"已下載過,略過 → {found_path}", final=True)
                    else:
                        report_progress(100.0, "已下載過,略過(找不到之前下載到哪裡,可能是舊記錄)", final=True)
                else:
                    report_progress(100.0, f"沒有新影片需要下載(略過 {skipped} 支:已下載過或已有逐字稿)", final=True)
                return
            report_progress(100.0, "下載完成", final=True)
        finally:
            _refresh_summary()   # 每個工作結束時更新一次(被限流、被取消、全部略過也一樣)
    return _download


def _gap_seconds(payload):
    """每支下載之間要等幾秒。亂填(不是數字)用預設值,負數當 0;不能讓一個壞設定讓整個工作出錯。"""
    try:
        minutes = float(payload.get("gap_minutes", DEFAULT_GAP_MINUTES))
    except (TypeError, ValueError):
        minutes = DEFAULT_GAP_MINUTES
    if minutes != minutes:   # NaN
        minutes = DEFAULT_GAP_MINUTES
    return max(0.0, minutes) * 60


def lookup_source(path):
    """使用者加入本機檔案時就查來源,按開始處理之前畫面上就看得到。
    順序:影片旁的資訊檔 → 下載記錄 → 用檔名反查 YouTube。反查的結果標成 guessed,
    畫面會填進網址欄並標「自動找到,請確認」,由使用者決定要不要用。"""
    found = source_sidecar.read(path)
    if found:
        return {"status": "sidecar", "metadata": found}
    found = download_record.lookup(path)
    if found and found.get("url"):
        return {"status": "record", "metadata": found}
    found = source_lookup.find_metadata(path)
    if found:
        return {"status": "guessed", "metadata": found}
    return {"status": "none", "metadata": None}


def _resolve_source(payload, path):
    """轉錄前決定來源(使用者定的順序):工作自帶的(下載完自動接鏈、或使用者在畫面上
    確認過的) → 使用者貼的網址 → 影片旁的資訊檔 → 下載記錄 → 都沒有就當本機檔案。
    轉錄時不自動上 YouTube 反查:那一步在加入清單時做、交給使用者確認,
    這裡再猜等於蓋過使用者「留空=本機檔案」的決定。
    找到的來源順手寫成影片旁的資訊檔,下次不用再查。"""
    existing = source_sidecar.read(path)
    metadata = payload.get("metadata")
    if not metadata and payload.get("source_url"):
        url = payload["source_url"]
        metadata = fetch_metadata(url)
        video_id = extract_video_id(url)
        if not metadata and video_id:
            # 查不到資訊(限流、網路):影片旁已有同一支影片的完整資訊就用它(貼的網址形式可能不同),
            # 別用只有網址的蓋掉
            if existing and extract_video_id(existing.get("url") or "") == video_id:
                metadata = existing
            else:
                metadata = {"title": None, "channel": None, "url": url, "upload_date": None}
        # 不是單一影片的網址(頻道、播放清單)又查不到資訊:不當成來源,往下照順序找
    if not metadata:
        metadata = existing or download_record.lookup(path)
    if metadata and metadata.get("url"):
        slim = {k: metadata.get(k) for k in ("title", "channel", "url", "upload_date")}
        if slim != existing:
            try:
                source_sidecar.write(path, metadata)
            except Exception:
                traceback.print_exc()  # 資訊檔寫不了不影響轉錄
    return metadata


def make_real_transcribe_fn():
    transcriber = Transcriber()  # 只載入一次模型,所有轉錄工作共用(載入模型本身要花時間)

    def _transcribe(payload, cancel_event, pause_event, report_progress):
        path = payload["path"]
        stem = Path(path).stem
        txt_path = today_dir() / "transcripts" / f"{stem}.txt"

        if payload.get("skip_existing", True) and (txt_path.is_file() or ledger.has_transcript_anywhere(BASE_DIR, stem)):
            report_progress(100.0, "已有逐字稿,跳過", final=True)
            return

        def _progress(percent):
            report_progress(percent, "轉錄中")

        text, segments = transcriber.transcribe(
            path, progress_callback=_progress, pause_event=pause_event, stop_event=cancel_event,
        )
        if cancel_event.is_set():
            # transcribe() 被取消時不會丟例外,只會提早結束、回傳目前算到一半的
            # 部分結果。這裡故意不存檔:半成品跟正常完成的檔案長得一模一樣
            # (同檔名、同位置),留著只會讓人誤以為轉錄完成了。
            report_progress(0.0, "已取消,不保留部分結果", final=True)
            return
        metadata = _resolve_source(payload, path)
        # .txt 集中放 transcripts/;.srt 跟原始影音檔放同一個資料夾、同檔名,
        # 這樣播放器才能自動抓到字幕,不用手動搬(沿用 3-4.Yt-down-sub 的慣例)
        transcriber.save_transcript(text, str(txt_path), metadata=metadata)
        if payload.get("want_srt", True):
            transcriber.save_srt(segments, str(Path(path).parent / f"{stem}.srt"), metadata=metadata)
        report_progress(100.0, "轉錄完成", final=True)
    return _transcribe


def build_orchestrator():
    # make_real_download_fn 需要在下載完成時回頭呼叫 orchestrator.enqueue_transcription,
    # 但 orchestrator 要等 download_fn 準備好才能建構 —— 用一個小容器延後綁定,
    # 建構完成後再把真正的 orchestrator 塞進去。
    ref = {}

    def _download(payload, cancel_event, pause_event, report_progress):
        make_real_download_fn(ref["orchestrator"], queued_paths)(payload, cancel_event, pause_event, report_progress)

    batch = BatchCompletion(
        is_settled=lambda: ref["orchestrator"].is_settled(),
        on_complete=open_result_folders,
    )

    queued_paths = set()   # 各個下載工作共用:已經排過轉錄的檔案不再重複排

    def on_job_terminal(job):
        build_day_index(str(today_dir()))
        if job.type == JobType.TRANSCRIBE:
            try:
                ledger.write_summary(BASE_DIR)   # 逐字稿剛做完,總清單的「逐字稿哪天」要跟著更新
            except Exception:
                traceback.print_exc()
        batch.on_job_terminal(job)

    orchestrator = Orchestrator(
        download_fn=_download,
        transcribe_fn=make_real_transcribe_fn(),
        on_job_terminal=on_job_terminal,
        auto_retry_seconds=AUTO_RETRY_SECONDS,
    )
    ref["orchestrator"] = orchestrator
    return orchestrator


# POST /api/jobs/<id>/<動詞> 對單一工作下指令,動詞對應 orchestrator 上同名的方法
_JOB_ACTIONS = {"cancel": "cancel", "pause": "pause", "resume": "resume"}


def _job_to_dict(job):
    # 「內容」欄位給畫面看的:下載顯示網址、轉錄顯示路徑,不是整包 payload dict
    content = job.payload.get("url") or job.payload.get("path") if isinstance(job.payload, dict) else job.payload
    return {
        "id": job.id,
        "type": job.type.value,
        "content": content,
        "state": job.state.value,
        "error_message": job.error_message,
        "progress": job.progress,
        "message": job.message,
        "paused": job.paused,
        "started_at": job.started_at,
    }


def make_handler(orchestrator):
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _serve_static(self, path):
            rel = path.lstrip("/") or "index.html"
            file_path = (STATIC_DIR / rel).resolve()
            if STATIC_DIR not in file_path.parents and file_path != STATIC_DIR:
                self.send_error(403)
                return
            if not file_path.is_file():
                self.send_error(404)
                return
            content_type, _ = mimetypes.guess_type(str(file_path))
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/jobs":
                jobs = [_job_to_dict(j) for j in orchestrator.list_jobs()]
                self._send_json(200, {"jobs": jobs, "auto_retry_at": orchestrator.auto_retry_at})
            else:
                self._serve_static(path)

        def do_POST(self):
            path = urlparse(self.path).path
            body = self._read_json()

            if path == "/api/jobs/download":
                job_id = orchestrator.enqueue_download({
                    "url": body["url"],
                    "format_type": body.get("format_type", "audio"),
                    "want_srt": body.get("want_srt", True),
                    "skip_existing": body.get("skip_existing", True),
                    "download_only": bool(body.get("download_only", False)),
                    "skip_transcribed": bool(body.get("skip_transcribed", True)),
                    "gap_minutes": body.get("gap_minutes", DEFAULT_GAP_MINUTES),
                })
                self._send_json(200, {"id": job_id})
            elif path == "/api/jobs/transcribe":
                payload = {
                    "path": body["path"],
                    "want_srt": body.get("want_srt", True),
                    "skip_existing": body.get("skip_existing", True),
                }
                # 使用者在畫面上確認過的來源(metadata)或自己貼的網址(source_url);都沒有=本機檔案
                if body.get("metadata"):
                    payload["metadata"] = body["metadata"]
                elif body.get("source_url"):
                    payload["source_url"] = body["source_url"]
                job_id = orchestrator.enqueue_transcription(payload)
                self._send_json(200, {"id": job_id})
            elif path == "/api/jobs/retry":
                orchestrator.retry_pending()
                self._send_json(200, {"ok": True})
            elif path == "/api/lookup-source":
                self._send_json(200, lookup_source(body["path"]))
            else:
                # /api/jobs/<id>/<action>,action 是下面這幾種對單一工作下指令的動詞
                job_action = next((a for a in _JOB_ACTIONS if path.startswith("/api/jobs/") and path.endswith(f"/{a}")), None)
                if job_action:
                    job_id = path[len("/api/jobs/"):-len(f"/{job_action}")]
                    getattr(orchestrator, _JOB_ACTIONS[job_action])(job_id)
                    self._send_json(200, {"ok": True})
                else:
                    self.send_error(404)

        def log_message(self, fmt, *args):
            pass  # 安靜一點,不要洗掉 Electron 主行程的 console

    return Handler


def run(port=0):
    """port=0 讓作業系統動態分配空閒 port(不寫死)。回傳 (server, 實際 port)。"""
    orchestrator = build_orchestrator()
    handler = make_handler(orchestrator)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, actual_port


if __name__ == "__main__":
    server, port = run()
    # 印出實際 port,Electron 主行程從 stdout 讀這一行來知道要連哪個 port
    print(f"BACKEND_PORT={port}", flush=True)
    try:
        threading.Event().wait()  # 掛著等 Electron 關閉這個行程
    except KeyboardInterrupt:
        server.shutdown()
