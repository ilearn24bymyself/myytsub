"""總清單:掃描專案根目錄底下所有「日期資料夾」(YYYYMMDD),認出哪些影片下載過、哪些轉錄過。

比對用「檔名(去副檔名)」:yt-dlp 存檔名是標題經 sanitize_filename 處理過的,轉錄的逐字稿
沿用同一個檔名,所以拿頻道清單上的標題 sanitize 後就能直接對上(不靠下載記錄檔,
手邊只有逐字稿、或是舊版下載的檔案也認得)。
每次需要時重新掃描,不另外存一份會跟實際檔案對不上的記錄。

使用者把整理好的資料搬到專案外的「歸檔資料夾」(結構不固定)後,也要繼續認得:
專案根目錄的 歸檔資料夾.txt 一行寫一個路徑(# 開頭是註解),連子資料夾一起掃描。
"""
import os
import re
from pathlib import Path

SUMMARY_NAME = "處理總清單.txt"
ARCHIVE_CONFIG_NAME = "歸檔資料夾.txt"
_DAY_RE = re.compile(r"^\d{8}$")
_DATE_IN_LABEL_RE = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})")
_ARCHIVE_SUFFIX = "(歸檔)"
# 下載完成的影片/音訊檔;.part、.ytdl、.source.json、.srt、.txt 都不算
MEDIA_EXTS = {".mp3", ".mp4", ".m4a", ".webm", ".mkv", ".wav", ".opus", ".flac", ".aac", ".mov"}


def _day_dirs(base):
    base = Path(base)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and _DAY_RE.match(p.name))


def archive_roots(base):
    """讀 歸檔資料夾.txt:一行一個路徑;空行、# 註解、不存在的資料夾都略過。"""
    config = Path(base) / ARCHIVE_CONFIG_NAME
    try:
        lines = config.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    roots = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path = Path(line)
        if path.is_dir():
            roots.append(path)
    return roots


def _date_key(label):
    """標籤排序用的日期(YYYYMMDD):日期資料夾本身就是;歸檔資料夾名稱裡有日期(2026-09-30 或 20260930)就取出來;
    都沒有就排最後。"""
    m = _DATE_IN_LABEL_RE.search(label)
    return "".join(m.groups()) if m else "00000000"


class Ledger:
    def __init__(self):
        self.downloads = {}    # 檔名 -> [(標籤, 路徑)],日期早的在前
        self.transcripts = {}  # 檔名 -> [標籤]
        self.archived_labels = set()   # 屬於歸檔資料夾的標籤(顯示時加「(歸檔)」,補排轉錄時不碰)

    def downloaded_path(self, stem):
        found = self.downloads.get(stem)
        return found[0][1] if found else None

    def downloaded_day(self, stem):
        found = self.downloads.get(stem)
        return found[0][0] if found else None

    def downloaded_is_archived(self, stem):
        return self.downloaded_day(stem) in self.archived_labels

    def has_transcript(self, stem):
        return stem in self.transcripts

    def transcript_day(self, stem):
        found = self.transcripts.get(stem)
        return found[0] if found else None

    def display_label(self, label):
        return label + _ARCHIVE_SUFFIX if label in self.archived_labels else label


def _scan_files(folder, label, result, media, text):
    """把 folder 底下(含子資料夾)的影片檔記成下載、.txt 記成逐字稿。"""
    for dirpath, _dirs, files in os.walk(folder):
        for name in sorted(files):
            p = Path(dirpath) / name
            suffix = p.suffix.lower()
            if media and suffix in MEDIA_EXTS:
                result.downloads.setdefault(p.stem, []).append((label, str(p)))
            elif text and suffix == ".txt":
                result.transcripts.setdefault(p.stem, []).append(label)


def scan(base, archives=None):
    """archives=None:讀 歸檔資料夾.txt。測試或特殊情況可以直接傳路徑清單。"""
    result = Ledger()
    for day in _day_dirs(base):
        downloads = day / "downloads"
        if downloads.is_dir():
            _scan_files(downloads, day.name, result, media=True, text=False)
        transcripts = day / "transcripts"
        if transcripts.is_dir():
            _scan_files(transcripts, day.name, result, media=False, text=True)
    for root in (archive_roots(base) if archives is None else archives):
        root = Path(root)
        # 歸檔資料夾底下第一層的名稱當標籤(例如「2026-09-30 卡魯鴨」);直接放在根目錄的用根目錄名稱
        groups = [(child.name, child) for child in sorted(root.iterdir()) if child.is_dir()] if root.is_dir() else []
        groups.append((root.name, None))
        for label, folder in groups:
            result.archived_labels.add(label)
            if folder is None:
                for p in sorted(root.iterdir()):   # 根目錄下直接放的檔案
                    if p.is_file():
                        suffix = p.suffix.lower()
                        if suffix in MEDIA_EXTS:
                            result.downloads.setdefault(p.stem, []).append((label, str(p)))
                        elif suffix == ".txt":
                            result.transcripts.setdefault(p.stem, []).append(label)
            else:
                _scan_files(folder, label, result, media=True, text=True)
    return result


def has_transcript_anywhere(base, stem):
    """轉錄前的快速檢查:任何一天(或歸檔資料夾)的逐字稿裡有沒有這個檔名。
    日期資料夾直接組路徑檢查,不用 glob(標題裡的 [ ] * ? 會被當成萬用字元)。"""
    if any((day / "transcripts" / f"{stem}.txt").is_file() for day in _day_dirs(base)):
        return True
    for root in archive_roots(base):
        for _dirpath, _dirs, files in os.walk(root):
            if f"{stem}.txt" in files:
                return True
    return False


def write_summary(base):
    """把總清單寫成人看得懂的文字檔放在專案根目錄:先是每天的數量,再是逐支影片
    (標題、哪一天下載、哪一天轉錄),新的在上面。要反查哪一支做過沒有直接搜標題。"""
    base = Path(base)
    result = scan(base)
    days = {}
    for items in result.downloads.values():
        for label, _path in items:
            days.setdefault(label, [0, 0])[0] += 1
    for items in result.transcripts.values():
        for label in items:
            days.setdefault(label, [0, 0])[1] += 1

    def newest(stem):
        keys = [_date_key(result.downloaded_day(stem) or ""), _date_key(result.transcript_day(stem) or "")]
        return max(keys)

    stems = sorted(set(result.downloads) | set(result.transcripts))
    stems.sort(key=lambda s: newest(s), reverse=True)   # 穩定排序:同一天的維持標題順序
    ordered_days = sorted(days, key=lambda label: (_date_key(label), label), reverse=True)

    lines = [
        "處理總清單(程式每次下載、轉錄後自動重新產生,請勿手動修改;新的在上面)",
        f"共 {len(stems)} 支影片:有下載檔 {len(result.downloads)} 支、有逐字稿 {len(result.transcripts)} 支",
        "",
        "【每天的數量】",
    ]
    for label in ordered_days:
        d, t = days[label]
        lines.append(f"{result.display_label(label)}  下載 {d} 支  逐字稿 {t} 份")
    lines += ["", "【逐支明細】(標題 | 下載哪天 | 逐字稿哪天;依日期新到舊)"]
    for stem in stems:
        dl, tx = result.downloaded_day(stem), result.transcript_day(stem)
        lines.append(f"{stem} | 下載 {result.display_label(dl) if dl else '無'} | "
                     f"逐字稿 {result.display_label(tx) if tx else '無'}")
    out = base / SUMMARY_NAME
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
