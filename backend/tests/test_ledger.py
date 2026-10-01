import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ledger  # noqa: E402


def _touch(base, day, kind, name):
    p = Path(base) / day / kind / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    return p


class ScanTest(unittest.TestCase):
    """總清單:掃描所有日期資料夾,用「檔名(去副檔名)」認出哪些影片下載過、哪些轉錄過。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_finds_downloads_and_transcripts_across_all_day_folders(self):
        video = _touch(self.base, "20260924", "downloads", "影片甲.mp4")
        _touch(self.base, "20260929", "transcripts", "影片甲.txt")
        _touch(self.base, "20261001", "transcripts", "只有逐字稿.txt")
        result = ledger.scan(self.base)
        self.assertEqual(result.downloaded_path("影片甲"), str(video))
        self.assertTrue(result.has_transcript("影片甲"))
        self.assertTrue(result.has_transcript("只有逐字稿"))
        self.assertIsNone(result.downloaded_path("只有逐字稿"))
        self.assertFalse(result.has_transcript("沒做過"))

    def test_unfinished_and_side_files_do_not_count_as_downloaded(self):
        # 殘留的 .part、.ytdl、資訊檔、字幕都不是「已下載完成的影片」
        for name in ("影片乙.mp4.part", "影片乙.f620.mp4.ytdl", "影片乙.source.json", "影片乙.srt", "影片乙.txt"):
            _touch(self.base, "20260930", "downloads", name)
        self.assertIsNone(ledger.scan(self.base).downloaded_path("影片乙"))

    def test_folders_that_are_not_day_folders_are_ignored(self):
        _touch(self.base, "backend", "downloads", "不是日期.mp4")
        _touch(self.base, "20261001", "downloads", "日期內.mp3")
        result = ledger.scan(self.base)
        self.assertIsNone(result.downloaded_path("不是日期"))
        self.assertIsNotNone(result.downloaded_path("日期內"))

    def test_a_missing_base_folder_gives_an_empty_ledger(self):
        result = ledger.scan(self.base / "不存在")
        self.assertFalse(result.has_transcript("任何"))

    def test_find_transcript_checks_every_day_folder(self):
        _touch(self.base, "20260924", "transcripts", "舊逐字稿 [特殊].txt")   # 檔名含 glob 會誤判的字元
        self.assertTrue(ledger.has_transcript_anywhere(self.base, "舊逐字稿 [特殊]"))
        self.assertFalse(ledger.has_transcript_anywhere(self.base, "別的"))


class SummaryTest(unittest.TestCase):
    def test_summary_file_lists_per_day_counts_and_every_video_with_its_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _touch(base, "20260908", "downloads", "甲.mp4")
            _touch(base, "20260908", "downloads", "乙.mp4")
            _touch(base, "20260915", "transcripts", "甲.txt")
            _touch(base, "20260915", "transcripts", "丙.txt")
            out = ledger.write_summary(base)
            text = out.read_text(encoding="utf-8")
        self.assertEqual(out.name, "處理總清單.txt")
        self.assertIn("20260908  下載 2 支  逐字稿 0 份", text)
        self.assertIn("20260915  下載 0 支  逐字稿 2 份", text)
        self.assertIn("甲 | 下載 20260908 | 逐字稿 20260915", text)
        self.assertIn("乙 | 下載 20260908 | 逐字稿 無", text)
        self.assertIn("丙 | 下載 無 | 逐字稿 20260915", text)


if __name__ == "__main__":
    unittest.main()
