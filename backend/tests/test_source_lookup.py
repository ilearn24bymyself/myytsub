import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import source_lookup  # noqa: E402


class GuessMetadataTest(unittest.TestCase):
    """使用者實測發現的情境:下載中途被 YouTube 限流,yt-dlp 當下沒拿到
    metadata,之後轉錄只能顯示「本機上傳」。這個函式用檔名反查 YouTube,
    但只有反查到的 video_id 真的出現在 download_archive.txt(下載當時
    確實記錄過)才採信,避免比對錯誤把不相關的影片當成出處寫進逐字稿——
    這條護欄是使用者實測回填舊資料時踩到教訓後加的(比對到但不在
    archive 裡的那支,真的是錯誤比對)。"""

    def test_returns_none_when_search_finds_nothing(self):
        with mock.patch.object(source_lookup, "_flat_search", return_value=None), \
             mock.patch.object(source_lookup, "_load_archive_ids", return_value=set()):
            self.assertIsNone(source_lookup.guess_metadata("C:/fake/video.mp4"))

    def test_returns_none_when_matched_id_not_in_archive(self):
        candidate = {"video_id": "notindexed", "title": "某影片", "channel": "某頻道", "url": "https://x"}
        with mock.patch.object(source_lookup, "_flat_search", return_value=candidate), \
             mock.patch.object(source_lookup, "_load_archive_ids", return_value={"other-id"}):
            self.assertIsNone(source_lookup.guess_metadata("C:/fake/video.mp4"))

    def test_returns_metadata_and_persists_it_when_id_confirmed_in_archive(self):
        candidate = {"video_id": "abc123", "title": "某影片", "channel": "某頻道",
                     "url": "https://youtube.com/watch?v=abc123"}
        with mock.patch.object(source_lookup, "_flat_search", return_value=candidate), \
             mock.patch.object(source_lookup, "_load_archive_ids", return_value={"abc123"}), \
             mock.patch.object(source_lookup, "download_record") as fake_record:
            result = source_lookup.guess_metadata("C:/fake/video.mp4")

        self.assertEqual(result["url"], "https://youtube.com/watch?v=abc123")
        fake_record.save.assert_called_once_with([{
            "path": "C:/fake/video.mp4", "title": "某影片", "channel": "某頻道",
            "url": "https://youtube.com/watch?v=abc123", "upload_date": None, "video_id": "abc123",
        }])

    def test_search_failure_returns_none_instead_of_raising(self):
        with mock.patch.object(source_lookup, "_flat_search", side_effect=Exception("network down")):
            self.assertIsNone(source_lookup.guess_metadata("C:/fake/video.mp4"))


if __name__ == "__main__":
    unittest.main()
