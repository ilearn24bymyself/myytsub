import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import source_sidecar  # noqa: E402

META = {"title": "某影片 7/25", "channel": "某頻道", "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "upload_date": "2026-07-25"}


class SourceSidecarTest(unittest.TestCase):
    """使用者的想法:每支影片下載完就把來源資訊存在影片旁邊。影片搬到哪(換資料夾、換電腦、
    ZIP 版另開資料夾),資訊就跟到哪;手動挑檔轉錄時先看同一個資料夾有沒有。"""

    def test_sidecar_sits_next_to_the_media_file_with_the_same_name(self):
        p = source_sidecar.sidecar_path(r"C:\d\downloads\某影片 7⧸25.mp4")
        self.assertEqual(p, Path(r"C:\d\downloads\某影片 7⧸25.source.json"))

    def test_write_then_read_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "某影片.mp4"
            media.write_bytes(b"")
            source_sidecar.write(media, dict(META, path=str(media), video_id="abcdefghijk"))
            self.assertEqual(source_sidecar.read(media), META)

    def test_the_file_is_small_readable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "某影片.mp3"
            source_sidecar.write(media, META)
            data = json.loads(source_sidecar.sidecar_path(media).read_text(encoding="utf-8"))
            self.assertEqual(data["url"], META["url"])
            self.assertIn("某頻道", source_sidecar.sidecar_path(media).read_text(encoding="utf-8"))  # 中文不轉成 \\u

    def test_read_returns_none_when_there_is_no_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(source_sidecar.read(Path(tmp) / "沒有資訊.mp4"))

    def test_read_returns_none_for_a_broken_or_urlless_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "壞掉.mp4"
            source_sidecar.sidecar_path(media).write_text("{not json", encoding="utf-8")
            self.assertIsNone(source_sidecar.read(media))
            source_sidecar.sidecar_path(media).write_text('{"title": "x"}', encoding="utf-8")
            self.assertIsNone(source_sidecar.read(media))

    def test_write_skips_metadata_without_a_url(self):
        # 沒有網址的資訊(例如救回來的檔案)寫了也沒用,反而會擋掉之後的查找
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "某影片.mp4"
            source_sidecar.write(media, {"title": "x", "channel": None, "url": None, "upload_date": None})
            self.assertFalse(source_sidecar.sidecar_path(media).exists())


if __name__ == "__main__":
    unittest.main()
