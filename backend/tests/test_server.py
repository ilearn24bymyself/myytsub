import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402


class DownloadChainsToTranscriptionTest(unittest.TestCase):
    """票 03 的驗收條件:下載完成後 index 要能反映這筆項目。build_day_index() 只掃
    transcripts/*.txt,不看 downloads/,所以下載完成後必須自動接一個轉錄工作,
    index 才有東西可以反映(這也是 spec.md 裡「transcription lane 含
    post-download transcription jobs」這句話的具體實作)。"""

    def test_completed_download_enqueues_transcription_for_each_downloaded_file(self):
        fake_orchestrator = mock.Mock()

        with mock.patch.object(server, "download_media") as fake_download_media, \
             mock.patch.object(server, "today_dir", return_value=Path("C:/fake/20260924")):
            fake_download_media.return_value = [
                {"path": "C:/fake/20260924/downloads/影片一.mp4"},
                {"path": "C:/fake/20260924/downloads/影片二.mp4"},
            ]
            download_fn = server.make_real_download_fn(fake_orchestrator)
            download_fn("https://youtube.com/watch?v=x", mock.Mock())

        fake_orchestrator.enqueue_transcription.assert_has_calls([
            mock.call("C:/fake/20260924/downloads/影片一.mp4"),
            mock.call("C:/fake/20260924/downloads/影片二.mp4"),
        ])


if __name__ == "__main__":
    unittest.main()
