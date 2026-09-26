import os
import re
import time
import yt_dlp
from yt_dlp.utils import DownloadCancelled

from orchestrator import RateLimited

# 跟 download_media() 用同一份,也給 source_lookup.py 拿來核對反查到的
# video_id 是不是真的下載過(不是模組內部變數,別處要查就得重複算路徑)。
ARCHIVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'download_archive.txt')

# YouTube 限流/IP 鎖定的已知訊號:HTTP 429、bot-check 提示訊息。
# 跟一般性錯誤(檔案損毀、網路中斷、影片下架)區分開來 —— 這類訊號代表整個
# 來源 IP 被鎖,不是這一支影片本身的問題,不該對它逐支重試。
_RATE_LIMIT_PATTERNS = [
    re.compile(r"\b429\b"),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"sign in to confirm you're not a bot", re.IGNORECASE),
]


def is_rate_limited_error(err_str: str) -> bool:
    return any(p.search(err_str) for p in _RATE_LIMIT_PATTERNS)


def _try_rename_temp(output_dir: str, format_type: str, downloaded_files: list) -> bool:
    """
    嘗試找到 .temp.mp4 / .temp.m4a 並改名為正式檔案。
    先用獨佔模式開啟確認檔案已釋放，成功改名後回傳 True。

    這條路徑是從 WinError 鎖定重試救回來的檔案，yt-dlp 的 info 在這個分支
    通常沒有成功取得（extract_info 本身就是拋例外的那個），所以救回來的
    檔案沒有 title/channel/url 這些 metadata 可以附，dict 裡對應欄位就是 None。
    """
    ext = "mp3" if format_type == "audio" else "mp4"
    existing_paths = {d["path"] for d in downloaded_files}
    for f in os.listdir(output_dir):
        if f.endswith('.temp.mp4') or f.endswith('.temp.m4a'):
            temp_path = os.path.join(output_dir, f)
            # 驗證檔案是否已被系統釋放
            try:
                with open(temp_path, 'a+b'):
                    pass
            except OSError:
                return False  # 仍在鎖定中

            base = f.replace('.temp.mp4', '').replace('.temp.m4a', '')
            final_path = os.path.join(output_dir, f"{base}.{ext}")
            try:
                os.rename(temp_path, final_path)
                if final_path not in existing_paths:
                    downloaded_files.append({
                        "path": final_path, "title": base, "channel": None,
                        "url": None, "upload_date": None, "video_id": None,
                    })
                return True
            except OSError:
                return False
    return False


def _format_upload_date(raw: str | None) -> str | None:
    """yt-dlp 的 upload_date 是 'YYYYMMDD' 字串，轉成好讀的 'YYYY-MM-DD'。"""
    if not raw or len(raw) != 8:
        return raw
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def _entry_to_meta(entry: dict, path: str) -> dict:
    return {
        "path": path,
        "title": entry.get("title"),
        "channel": entry.get("channel") or entry.get("uploader"),
        "url": entry.get("webpage_url") or entry.get("original_url"),
        "upload_date": _format_upload_date(entry.get("upload_date")),
        "video_id": entry.get("id"),
    }


def download_media(url: str, output_dir: str, format_type: str = "audio",
                   status_callback=None, progress_callback=None, stop_event=None):
    """
    使用 yt-dlp 下載 YouTube 影片或播放清單。
    format_type     : 'audio' (mp3) 或 'video' (mp4)
    status_callback : fn(str) — 傳送狀態文字給 UI 顯示
    progress_callback: fn(float) — 傳送 0.0~100.0 進度給 UI 更新進度條
    stop_event      : threading.Event — 背景執行緒下載中途若被設定，會在下一次
                       yt-dlp 進度回呼時中斷這支影片的下載(拋出 DownloadCancelled)。
    回傳下載完成的清單，每個元素是 dict：
        {"path": str, "title": str|None, "channel": str|None,
         "url": str|None, "upload_date": str|None, "video_id": str|None}
    metadata 抓不到時(例如 WinError 鎖定重試救回來的檔案)對應欄位是 None，
    呼叫端要能處理缺 metadata 的情況。
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    ffmpeg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin')
    archive_file = ARCHIVE_FILE

    def _progress_hook(d):
        if stop_event is not None and stop_event.is_set():
            raise DownloadCancelled("使用者已停止")
        if not progress_callback:
            return
        if d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                progress_callback(min((downloaded / total) * 100.0, 99.0))
        elif d.get('status') == 'finished':
            progress_callback(100.0)

    ydl_opts = {
        'ffmpeg_location': ffmpeg_dir,
        'download_archive': archive_file,
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'ignoreerrors': False,
        'no_warnings': True,
        'quiet': False,
        'progress_hooks': [_progress_hook],
    }

    if format_type == "audio":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        ydl_opts['merge_output_format'] = 'mp4'

    downloaded_files = []
    info = None

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)

        except Exception as e:
            err_str = str(e)
            is_winlock = (
                "WinError 32" in err_str
                or "being used by another process" in err_str
                or "無法存取" in err_str
                or "[WinError" in err_str
            )

            if is_winlock:
                # ── 重試迴圈：10 秒最多 3 次 → 5 分鐘最多 3 次 ──
                long_retries = 0
                renamed_ok = False

                while long_retries < 3 and not renamed_ok:
                    short_retries = 0
                    while short_retries < 3 and not renamed_ok:
                        short_retries += 1
                        msg = f"⚠️ 檔案鎖定中（第 {short_retries}/3 次），等待 10 秒後重試..."
                        if status_callback:
                            status_callback(msg)
                        else:
                            print(msg)
                        time.sleep(10)
                        renamed_ok = _try_rename_temp(output_dir, format_type, downloaded_files)

                    if not renamed_ok:
                        long_retries += 1
                        if long_retries < 3:
                            msg = f"🔴 短暫重試全部失敗，第 {long_retries}/3 次深度等待，暫停 5 分鐘..."
                            if status_callback:
                                status_callback(msg)
                            else:
                                print(msg)
                            time.sleep(300)
                            renamed_ok = _try_rename_temp(output_dir, format_type, downloaded_files)
                        else:
                            # 3 次 5 分鐘都失敗，放棄
                            msg = f"❌ 嚴重錯誤：檔案鎖定無法解除（已等待超過 15 分鐘），此影片已跳過。錯誤：{err_str[:120]}"
                            if status_callback:
                                status_callback(msg)
                            else:
                                print(msg)
                            return []
            elif is_rate_limited_error(err_str):
                # 整個來源 IP 被限流/鎖定,不是這支影片本身的問題 —— 拋出
                # RateLimited 讓 orchestrator 跳過同批次剩餘項目,不逐支重試
                raise RateLimited(err_str) from e
            else:
                # 其他一般性錯誤，直接拋出讓 orchestrator 標記這一項失敗
                raise

        # 如果透過重試已經拿到檔案，直接返回(依 path 去重，dict 不能丟進 set)
        if downloaded_files:
            seen = {}
            for d in downloaded_files:
                seen[d["path"]] = d
            return list(seen.values())

        if info is None:
            return []

        # ── 從 yt-dlp info 解析最終檔案路徑 ──
        def get_all_entries(info_dict):
            entries = []
            if not info_dict:
                return entries
            if 'entries' in info_dict:
                for entry in info_dict['entries']:
                    if entry:
                        entries.extend(get_all_entries(entry))
            else:
                entries.append(info_dict)
            return entries

        all_entries = get_all_entries(info)
        seen_paths = set()

        for entry in all_entries:
            if not entry.get('title'):
                continue

            safe_title = yt_dlp.utils.sanitize_filename(entry['title'])
            ext = "mp3" if format_type == "audio" else "mp4"
            filepath = os.path.join(output_dir, f"{safe_title}.{ext}")

            if os.path.exists(filepath):
                if filepath not in seen_paths:
                    seen_paths.add(filepath)
                    downloaded_files.append(_entry_to_meta(entry, filepath))
            else:
                # 備用找法：前 15 字比對
                for f in os.listdir(output_dir):
                    if f.endswith(f'.{ext}'):
                        full_f = os.path.join(output_dir, f)
                        compare_len = min(len(safe_title), 15)
                        if compare_len > 0 and safe_title[:compare_len] in f and full_f not in seen_paths:
                            seen_paths.add(full_f)
                            downloaded_files.append(_entry_to_meta(entry, full_f))
                            break

    return downloaded_files


_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")


def extract_video_id(url: str) -> str | None:
    """從常見的 YouTube 網址格式(watch?v=、youtu.be/、shorts/)解析出 video ID。
    解析不出來就回傳 None,呼叫端要能處理拿不到 ID 的情況。"""
    m = _VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def fetch_title_only(url: str) -> str | None:
    """只要標題,不下載、不受 download_archive 影響(archive 只在下載階段生效)。
    用途:影片已經被 archive 記錄過,download_media() 拿不到任何 info 時
    (yt-dlp 這時候 extract_info(download=True) 回傳 None),用這個另外查
    標題,才能拿標題去比對磁碟上實際的檔名找回路徑。"""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None
    if not info:
        return None
    return info.get("title")


if __name__ == "__main__":
    test_url = "https://www.youtube.com/watch?v=BaW_C-CGtQc"
    print("Testing download...")
