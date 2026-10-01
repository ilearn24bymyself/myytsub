"""總清單:掃描專案根目錄底下所有「日期資料夾」(YYYYMMDD),認出哪些影片下載過、哪些轉錄過。

比對用「檔名(去副檔名)」:yt-dlp 存檔名是標題經 sanitize_filename 處理過的,轉錄的逐字稿
沿用同一個檔名,所以拿頻道清單上的標題 sanitize 後就能直接對上(不靠下載記錄檔,
手邊只有逐字稿、或是舊版下載的檔案也認得)。
每次需要時重新掃描,不另外存一份會跟實際檔案對不上的記錄。
"""
import re
from pathlib import Path

SUMMARY_NAME = "處理總清單.txt"
_DAY_RE = re.compile(r"^\d{8}$")
# 下載完成的影片/音訊檔;.part、.ytdl、.source.json、.srt、.txt 都不算
MEDIA_EXTS = {".mp3", ".mp4", ".m4a", ".webm", ".mkv", ".wav", ".opus", ".flac", ".aac", ".mov"}


def _day_dirs(base):
    base = Path(base)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and _DAY_RE.match(p.name))


class Ledger:
    def __init__(self):
        self.downloads = {}    # 檔名 -> [(日期, 路徑)],日期早的在前
        self.transcripts = {}  # 檔名 -> [日期]

    def downloaded_path(self, stem):
        found = self.downloads.get(stem)
        return found[0][1] if found else None

    def downloaded_day(self, stem):
        found = self.downloads.get(stem)
        return found[0][0] if found else None

    def has_transcript(self, stem):
        return stem in self.transcripts

    def transcript_day(self, stem):
        found = self.transcripts.get(stem)
        return found[0] if found else None


def scan(base):
    result = Ledger()
    for day in _day_dirs(base):
        downloads = day / "downloads"
        if downloads.is_dir():
            for p in sorted(downloads.iterdir()):
                if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                    result.downloads.setdefault(p.stem, []).append((day.name, str(p)))
        transcripts = day / "transcripts"
        if transcripts.is_dir():
            for p in sorted(transcripts.iterdir()):
                if p.is_file() and p.suffix.lower() == ".txt":
                    result.transcripts.setdefault(p.stem, []).append(day.name)
    return result


def has_transcript_anywhere(base, stem):
    """轉錄前的快速檢查:任何一天的逐字稿資料夾裡有沒有這個檔名。直接組路徑檢查,
    不用 glob(標題裡的 [ ] * ? 會被當成萬用字元)。"""
    return any((day / "transcripts" / f"{stem}.txt").is_file() for day in _day_dirs(base))


def write_summary(base):
    """把總清單寫成人看得懂的文字檔放在專案根目錄:先是每天的數量,再是逐支影片
    (標題、哪一天下載、哪一天轉錄),要反查哪一支做過沒有直接搜標題。"""
    base = Path(base)
    result = scan(base)
    days = {}
    for stem, items in result.downloads.items():
        for day, _path in items:
            days.setdefault(day, [0, 0])[0] += 1
    for stem, items in result.transcripts.items():
        for day in items:
            days.setdefault(day, [0, 0])[1] += 1
    stems = sorted(set(result.downloads) | set(result.transcripts))

    lines = [
        "處理總清單(程式每次下載、轉錄後自動重新產生,請勿手動修改)",
        f"共 {len(stems)} 支影片:有下載檔 {len(result.downloads)} 支、有逐字稿 {len(result.transcripts)} 支",
        "",
        "【每天的數量】",
    ]
    for day in sorted(days):
        d, t = days[day]
        lines.append(f"{day}  下載 {d} 支  逐字稿 {t} 份")
    lines += ["", "【逐支明細】(標題 | 下載哪天 | 逐字稿哪天)"]
    for stem in stems:
        lines.append(f"{stem} | 下載 {result.downloaded_day(stem) or '無'} | 逐字稿 {result.transcript_day(stem) or '無'}")
    out = base / SUMMARY_NAME
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
