"""本機 HTTP 服務:Electron 渲染畫面透過這層跟 orchestrator 溝通。

只服務本機單一使用者(Electron 自己),不考慮多使用者併發存取安全性問題。
同時把 electron/renderer 靜態檔案(index.html/renderer.js)也從這個 server 生出去,
這樣 Electron 視窗直接載入 http://127.0.0.1:<port>/,不會有跨來源(CORS)問題。
"""
import json
import mimetypes
import sys
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent  # 專案根目錄
STATIC_DIR = BASE_DIR / "electron" / "renderer"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from orchestrator import Orchestrator, JobState  # noqa: E402
from downloader import download_media, extract_video_id, fetch_title_only  # noqa: E402
from yt_dlp.utils import sanitize_filename  # noqa: E402
from transcriber import Transcriber  # noqa: E402
from index_builder import build_day_index  # noqa: E402
import download_record  # noqa: E402
import source_lookup  # noqa: E402


def today_dir() -> Path:
    d = BASE_DIR / date.today().strftime("%Y%m%d")
    (d / "downloads").mkdir(parents=True, exist_ok=True)
    (d / "uploads").mkdir(parents=True, exist_ok=True)
    (d / "transcripts").mkdir(parents=True, exist_ok=True)
    return d


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


def make_real_download_fn(orchestrator):
    """下載完成後自動接一個轉錄工作,轉錄選項(是否要字幕/跳過已存在)由呼叫端
    在 payload 裡指定,這裡原樣轉給轉錄工作,不自己決定預設值。
    build_day_index() 只掃 transcripts/*.txt,不看 downloads/,所以純下載的
    項目要有轉錄工作接手,index 才有東西可以反映(票 03)。
    metadata 隨 payload 一起轉給轉錄工作(自動接鏈當下就有,不用等查記錄檔),
    同時也存一份到 download_record,這樣使用者之後手動挑同一份檔案轉錄
    (例如下載中斷分好幾次、或只是想重新產生逐字稿)也查得回出處。"""
    def _download(payload, cancel_event, pause_event, report_progress):
        d = today_dir()

        def _progress(percent):
            report_progress(percent, "下載中")

        downloaded = download_media(
            payload["url"], output_dir=str(d / "downloads"),
            format_type=payload.get("format_type", "audio"),
            progress_callback=_progress, stop_event=cancel_event,
        )
        if not downloaded:
            # yt-dlp 的 download_archive 記錄過這支影片時,download_media() 會
            # 靜默回傳空陣列(不下載、不報錯)。這裡刻意不要讓它看起來跟正常
            # 完成一樣——沒有檔案、也沒有接轉錄工作,要讓使用者知道原因,並且
            # 盡量把之前下載到哪裡也一併告訴使用者。
            found_path = _find_previously_downloaded_path(payload["url"])
            if found_path:
                report_progress(100.0, f"已下載過,略過 → {found_path}")
            else:
                report_progress(100.0, "已下載過,略過(找不到之前下載到哪裡,可能是舊記錄)")
            return
        download_record.save(downloaded)
        for entry in downloaded:
            orchestrator.enqueue_transcription({
                "path": entry["path"],
                "want_srt": payload.get("want_srt", True),
                "skip_existing": payload.get("skip_existing", True),
                "metadata": entry,
            })
    return _download


def make_real_transcribe_fn():
    transcriber = Transcriber()  # 只載入一次模型,所有轉錄工作共用(載入模型本身要花時間)

    def _transcribe(payload, cancel_event, pause_event, report_progress):
        path = payload["path"]
        stem = Path(path).stem
        txt_path = today_dir() / "transcripts" / f"{stem}.txt"

        if payload.get("skip_existing", True) and txt_path.is_file():
            report_progress(100.0, "已有逐字稿,跳過")
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
            report_progress(0.0, "已取消,不保留部分結果")
            return
        # metadata 優先用 payload 帶的(下載完自動接鏈的情況);使用者手動挑
        # 本機檔案轉錄時 payload 沒有 metadata,退回查 download_record;都查
        # 不到(例如下載當時被限流,yt-dlp 自己也沒拿到 metadata)才用檔名
        # 反查 YouTube,反查到、且確認在 download_archive.txt 裡才採信,
        # 查不到才誠實顯示「本機上傳」(不是本工具下載過的檔案)。
        metadata = payload.get("metadata") or download_record.lookup(path) or source_lookup.guess_metadata(path)
        # .txt 集中放 transcripts/;.srt 跟原始影音檔放同一個資料夾、同檔名,
        # 這樣播放器才能自動抓到字幕,不用手動搬(沿用 3-4.Yt-down-sub 的慣例)
        transcriber.save_transcript(text, str(txt_path), metadata=metadata)
        if payload.get("want_srt", True):
            transcriber.save_srt(segments, str(Path(path).parent / f"{stem}.srt"), metadata=metadata)
    return _transcribe


def build_orchestrator():
    # make_real_download_fn 需要在下載完成時回頭呼叫 orchestrator.enqueue_transcription,
    # 但 orchestrator 要等 download_fn 準備好才能建構 —— 用一個小容器延後綁定,
    # 建構完成後再把真正的 orchestrator 塞進去。
    ref = {}

    def _download(payload, cancel_event, pause_event, report_progress):
        make_real_download_fn(ref["orchestrator"])(payload, cancel_event, pause_event, report_progress)

    def on_job_terminal(job):
        build_day_index(str(today_dir()))

    orchestrator = Orchestrator(
        download_fn=_download,
        transcribe_fn=make_real_transcribe_fn(),
        on_job_terminal=on_job_terminal,
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
                self._send_json(200, {"jobs": jobs})
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
                })
                self._send_json(200, {"id": job_id})
            elif path == "/api/jobs/transcribe":
                job_id = orchestrator.enqueue_transcription({
                    "path": body["path"],
                    "want_srt": body.get("want_srt", True),
                    "skip_existing": body.get("skip_existing", True),
                })
                self._send_json(200, {"id": job_id})
            elif path == "/api/jobs/retry":
                orchestrator.retry_pending()
                self._send_json(200, {"ok": True})
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
