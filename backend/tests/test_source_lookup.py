import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import source_lookup  # noqa: E402

# 使用者實測的真實檔名(yt-dlp 存檔時把標題的 "/" 換成 "⧸"、"|" 換成 "｜")
REAL_STEM = "【NFE】NFE天機指標復盤！7⧸25盈利3R ｜ 比特幣BTC日內交易完整教學"
REAL_TITLE = "【NFE】NFE天機指標復盤！7/25盈利3R | 比特幣BTC日內交易完整教學"
NEXT_EPISODE_TITLE = "【NFE】NFE天機指標復盤！7/27盈利24R | 比特幣BTC日內交易完整教學"


def _cand(video_id, title):
    return {"video_id": video_id, "title": title, "channel": "某頻道",
            "url": f"https://www.youtube.com/watch?v={video_id}"}


class GuessMetadataTest(unittest.TestCase):
    """手動挑本機檔案轉錄、又查不到下載記錄時,用檔名反查 YouTube 補回出處。
    採信條件:候選影片的標題,套用跟存檔時一樣的換字規則後,要跟檔名「完全相同」。
    錯誤比對比「不知道出處」更糟,所以寧可放棄也不猜。"""

    def _guess(self, candidates, archive=(), stem=REAL_STEM):
        with mock.patch.object(source_lookup, "_search_candidates", return_value=candidates) as fake_search, \
             mock.patch.object(source_lookup, "_load_archive_ids", return_value=set(archive)):
            result = source_lookup.find_metadata(f"C:/fake/{stem}.mp4")
        return result, fake_search, None

    def test_finds_the_users_real_file_whose_title_contained_a_slash(self):
        # 使用者實測:檔名裡的 "⧸" 讓原本的搜尋回傳 0 筆,所以顯示「無法取得來源」
        result, fake_search, _ = self._guess([_cand("FfN6As_5doQ", REAL_TITLE)])
        self.assertEqual(result["url"], "https://www.youtube.com/watch?v=FfN6As_5doQ")
        queried = fake_search.call_args.args[0]
        self.assertNotIn("⧸", queried)   # 搜尋字串要把存檔時換掉的字換回來
        self.assertEqual(result, {"title": REAL_TITLE, "channel": "某頻道",
                                  "url": "https://www.youtube.com/watch?v=FfN6As_5doQ", "upload_date": None})

    def test_rejects_a_different_episode_even_if_it_is_in_the_archive(self):
        # 整個系列都下載過時,搜到相鄰的另一集也會「在 archive 裡」——不能因此採信
        result, _search, _ = self._guess([_cand("other", NEXT_EPISODE_TITLE)], archive={"other"})
        self.assertIsNone(result)

    def test_accepts_an_exact_title_match_even_if_not_in_the_archive(self):
        # 用別的工具下載的檔案不在本工具的 archive 裡;標題完全相同已經足以確認
        result, _search, _record = self._guess([_cand("FfN6As_5doQ", REAL_TITLE)], archive=set())
        self.assertIsNotNone(result)

    def test_picks_the_exact_match_among_several_results(self):
        result, _search, _record = self._guess(
            [_cand("other", NEXT_EPISODE_TITLE), _cand("FfN6As_5doQ", REAL_TITLE)])
        self.assertEqual(result["url"], "https://www.youtube.com/watch?v=FfN6As_5doQ")

    def test_several_exact_matches_prefers_the_one_in_the_archive(self):
        result, _search, _record = self._guess(
            [_cand("reupload", REAL_TITLE), _cand("FfN6As_5doQ", REAL_TITLE)], archive={"FfN6As_5doQ"})
        self.assertEqual(result["url"], "https://www.youtube.com/watch?v=FfN6As_5doQ")

    def test_several_exact_matches_none_in_archive_gives_up_instead_of_guessing(self):
        result, _search, _ = self._guess(
            [_cand("a", REAL_TITLE), _cand("b", REAL_TITLE)], archive=set())
        self.assertIsNone(result)

    def test_returns_none_when_search_finds_nothing(self):
        result, _search, _record = self._guess([])
        self.assertIsNone(result)

    def test_search_failure_returns_none_instead_of_raising(self):
        with mock.patch.object(source_lookup, "_search_candidates", side_effect=Exception("network down")):
            self.assertIsNone(source_lookup.find_metadata(f"C:/fake/{REAL_STEM}.mp4"))


class SearchQueryTest(unittest.TestCase):
    def test_restores_characters_that_were_replaced_when_saving(self):
        self.assertEqual(source_lookup._search_query("A⧸B⧹C"), "A/B\\C")

    def test_a_word_starting_with_minus_is_not_sent_as_an_exclusion(self):
        # YouTube 搜尋把「空白後的 -字」當成「排除這個字」:使用者實測的「ETH -3R」
        # 等於叫它找不含 3R 的結果,永遠 0 筆
        q = source_lookup._search_query("市場級別分離 ｜ 8⧸23-24 ETH -3R ｜ 3m單級別")
        self.assertNotIn(" -", q)
        self.assertFalse(q.startswith("-"))
        self.assertIn("8/23-24", q)   # 字中間的 - 不是排除語法,要保留

    def test_leaves_an_ordinary_title_unchanged(self):
        self.assertEqual(source_lookup._search_query("普通標題 ｜ 123"), "普通標題 ｜ 123")


if __name__ == "__main__":
    unittest.main()
