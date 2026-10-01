import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import downloader  # noqa: E402

FFMPEG = Path(__file__).resolve().parent.parent / "bin" / "ffmpeg.exe"


def _serve(data, seen_ranges):
    """支援 Range 的最小 HTTP 伺服器:yt-dlp 續傳時會用 Range 要求「從第幾個位元組開始」。"""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, with_body):
            rng = self.headers.get("Range")
            start = 0
            if rng:
                seen_ranges.append(rng)
                start = int(re.match(r"bytes=(\d+)-", rng).group(1))
            body = data[start:]
            self.send_response(206 if rng else 200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(body)))
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            self.end_headers()
            if with_body:
                self.wfile.write(body)

        def do_GET(self):
            self._send(True)

        def do_HEAD(self):
            self._send(False)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@unittest.skipUnless(FFMPEG.is_file(), "需要 backend/bin/ffmpeg.exe")
class ResumeAcrossDaysTest(unittest.TestCase):
    """昨天被限流/中斷留下的未完成檔(.part),今天(輸出到另一個日期資料夾)要接得上,
    完成的檔案放在今天的資料夾。做法:未完成的暫存檔固定放在同一個資料夾,不跟著日期走。"""

    def test_a_partial_file_left_by_yesterday_is_resumed_and_the_result_lands_in_todays_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / "source.wav"
            subprocess.run([str(FFMPEG), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(src)],
                           capture_output=True, check=True)
            data = src.read_bytes()
            half = len(data) // 2
            partial_dir = base / "partial"
            partial_dir.mkdir()
            (partial_dir / "續傳測試.wav.part").write_bytes(data[:half])     # 「昨天」下載到一半的
            today = base / "20261001" / "downloads"
            seen = []
            srv = _serve(data, seen)
            url = f"http://127.0.0.1:{srv.server_address[1]}/{quote('續傳測試.wav')}"
            try:
                with mock.patch.object(downloader, "PARTIAL_DIR", str(partial_dir)), \
                     mock.patch.object(downloader, "ARCHIVE_FILE", str(base / "archive.txt")):
                    result = downloader.download_media(url, output_dir=str(today), format_type="audio")
            finally:
                srv.shutdown()
                srv.server_close()
            self.assertIn(f"bytes={half}-", seen)                           # 真的是從一半的位置續傳,不是從頭
            self.assertEqual(len(result), 1)
            self.assertEqual(Path(result[0]["path"]).parent, today)         # 完成的檔案在今天的資料夾
            self.assertEqual(list(partial_dir.glob("*.part")), [])          # 暫存檔用完就沒了
            self.assertEqual(list(today.glob("*.part")), [])

    def test_a_fresh_download_works_when_the_partial_folder_does_not_exist_yet(self):
        # 第一次用的時候暫存資料夾還不存在,要自己建出來
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / "source.wav"
            subprocess.run([str(FFMPEG), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(src)],
                           capture_output=True, check=True)
            srv = _serve(src.read_bytes(), [])
            url = f"http://127.0.0.1:{srv.server_address[1]}/{quote('全新.wav')}"
            partial_dir = base / "不存在的暫存資料夾"
            try:
                with mock.patch.object(downloader, "PARTIAL_DIR", str(partial_dir)), \
                     mock.patch.object(downloader, "ARCHIVE_FILE", str(base / "archive.txt")):
                    result = downloader.download_media(url, output_dir=str(base / "20261001" / "downloads"),
                                                       format_type="audio")
            finally:
                srv.shutdown()
                srv.server_close()
            self.assertEqual(len(result), 1)
            self.assertTrue(Path(result[0]["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
