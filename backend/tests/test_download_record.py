import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import download_record  # noqa: E402


class SaveAndLookupRoundTripTest(unittest.TestCase):
    """核心用途:下載時存一筆 path→metadata 的記錄,之後使用者手動挑選同一份
    檔案轉錄時能查回出處,不再顯示「本機上傳」。用暫存檔案位置隔開真正的
    backend/download_metadata.json,不會互相汙染、也不會留下測試垃圾。"""

    def test_lookup_returns_none_when_path_never_recorded(self):
        with mock.patch.object(download_record, "_RECORD_FILE", "C:/fake/nonexistent.json"):
            self.assertIsNone(download_record.lookup("C:/fake/video.mp4"))

    def test_saved_entry_can_be_looked_up_by_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            record_file = str(Path(tmp) / "download_metadata.json")
            with mock.patch.object(download_record, "_RECORD_FILE", record_file):
                download_record.save([{
                    "path": str(Path(tmp) / "video.mp4"),
                    "title": "測試標題", "channel": "測試頻道",
                    "url": "https://youtube.com/watch?v=abc123", "upload_date": "2026-09-24",
                    "video_id": "abc123",
                }])
                result = download_record.lookup(str(Path(tmp) / "video.mp4"))
                self.assertEqual(result["url"], "https://youtube.com/watch?v=abc123")
                self.assertEqual(result["title"], "測試標題")

    def test_save_accumulates_instead_of_overwriting_previous_entries(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            record_file = str(Path(tmp) / "download_metadata.json")
            with mock.patch.object(download_record, "_RECORD_FILE", record_file):
                download_record.save([{"path": str(Path(tmp) / "a.mp4"), "title": "A", "channel": None,
                                        "url": "https://x/a", "upload_date": None, "video_id": None}])
                download_record.save([{"path": str(Path(tmp) / "b.mp4"), "title": "B", "channel": None,
                                        "url": "https://x/b", "upload_date": None, "video_id": None}])
                self.assertIsNotNone(download_record.lookup(str(Path(tmp) / "a.mp4")))
                self.assertIsNotNone(download_record.lookup(str(Path(tmp) / "b.mp4")))


if __name__ == "__main__":
    unittest.main()
