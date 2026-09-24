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
from downloader import download_media  # noqa: E402
from transcriber import Transcriber  # noqa: E402
from index_builder import build_day_index  # noqa: E402


def today_dir() -> Path:
    d = BASE_DIR / date.today().strftime("%Y%m%d")
    (d / "downloads").mkdir(parents=True, exist_ok=True)
    (d / "uploads").mkdir(parents=True, exist_ok=True)
    (d / "transcripts").mkdir(parents=True, exist_ok=True)
    return d


def make_real_download_fn(orchestrator):
    """下載完成後自動接一個轉錄工作。build_day_index() 只掃 transcripts/*.txt,
    不看 downloads/,所以純下載的項目要有轉錄工作接手,index 才有東西可以反映
    (票 03:下載完成後 index 要即時反映這筆項目)。"""
    def _download(url, cancel_event):
        d = today_dir()
        downloaded = download_media(url, output_dir=str(d / "downloads"), stop_event=cancel_event)
        for entry in downloaded:
            orchestrator.enqueue_transcription(entry["path"])
    return _download


def make_real_transcribe_fn():
    transcriber = Transcriber()  # 只載入一次模型,所有轉錄工作共用(載入模型本身要花時間)

    def _transcribe(path, cancel_event):
        d = today_dir()
        text, segments = transcriber.transcribe(path, stop_event=cancel_event)
        stem = Path(path).stem
        out_dir = d / "transcripts"
        transcriber.save_transcript(text, str(out_dir / f"{stem}.txt"))
        transcriber.save_srt(segments, str(out_dir / f"{stem}.srt"))
    return _transcribe


def build_orchestrator():
    # make_real_download_fn 需要在下載完成時回頭呼叫 orchestrator.enqueue_transcription,
    # 但 orchestrator 要等 download_fn 準備好才能建構 —— 用一個小容器延後綁定,
    # 建構完成後再把真正的 orchestrator 塞進去。
    ref = {}

    def _download(url, cancel_event):
        make_real_download_fn(ref["orchestrator"])(url, cancel_event)

    def on_job_terminal(job):
        build_day_index(str(today_dir()))

    orchestrator = Orchestrator(
        download_fn=_download,
        transcribe_fn=make_real_transcribe_fn(),
        on_job_terminal=on_job_terminal,
    )
    ref["orchestrator"] = orchestrator
    return orchestrator


def _job_to_dict(job):
    return {
        "id": job.id,
        "type": job.type.value,
        "payload": job.payload,
        "state": job.state.value,
        "error_message": job.error_message,
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
                job_id = orchestrator.enqueue_download(body["url"])
                self._send_json(200, {"id": job_id})
            elif path == "/api/jobs/transcribe":
                job_id = orchestrator.enqueue_transcription(body["path"])
                self._send_json(200, {"id": job_id})
            elif path == "/api/jobs/retry":
                orchestrator.retry_pending()
                self._send_json(200, {"ok": True})
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path[len("/api/jobs/"):-len("/cancel")]
                orchestrator.cancel(job_id)
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
