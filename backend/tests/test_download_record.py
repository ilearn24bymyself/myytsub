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


class FindPathByVideoIdTest(unittest.TestCase):
    """使用者實測踩到的情境:網址對應的影片已經下載過,想在「已下載過」的
    訊息裡順便告訴使用者實際存在哪裡。記錄裡沒存 video_id 本身(不需要,
    url 裡就有),用 video_id 是不是這個 url 的子字串來比對。"""

    def test_returns_path_when_a_record_url_contains_the_video_id(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            record_file = str(Path(tmp) / "download_metadata.json")
            with mock.patch.object(download_record, "_RECORD_FILE", record_file):
                download_record.save([{
                    "path": str(Path(tmp) / "video.mp4"), "title": "T", "channel": "C",
                    "url": "https://www.youtube.com/watch?v=pfGg0Uris1w", "upload_date": None,
                }])
                result = download_record.find_path_by_video_id("pfGg0Uris1w")
                self.assertEqual(result, str(Path(tmp) / "video.mp4"))

    def test_returns_none_when_no_record_matches(self):
        with mock.patch.object(download_record, "_RECORD_FILE", "C:/fake/nonexistent.json"):
            self.assertIsNone(download_record.find_path_by_video_id("someid"))


class FindPathByTitleTest(unittest.TestCase):
    """download_metadata.json 沒記錄到的舊資料(這個記錄機制上線前下載的檔案)
    只能靠標題比對實際磁碟上的檔名——yt-dlp 的 outtmpl 是 %(title)s.%(ext)s,
    檔名幾乎一定就是標題本身。掃 <root>/<日期資料夾>/downloads 或 /uploads。"""

    def test_finds_file_in_downloads_subfolder_matching_title_exactly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day_dir = root / "20260920" / "downloads"
            day_dir.mkdir(parents=True)
            target = day_dir / "測試影片標題.mp4"
            target.write_bytes(b"fake")
            result = download_record.find_path_by_title(str(root), "測試影片標題")
            self.assertEqual(result, str(target))

    def test_also_checks_uploads_subfolder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day_dir = root / "20260921" / "uploads"
            day_dir.mkdir(parents=True)
            target = day_dir / "另一支影片.mp3"
            target.write_bytes(b"fake")
            result = download_record.find_path_by_title(str(root), "另一支影片")
            self.assertEqual(result, str(target))

    def test_returns_none_when_no_matching_filename_exists(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = download_record.find_path_by_title(tmp, "查無此標題")
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
