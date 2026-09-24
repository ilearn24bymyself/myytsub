"""
每次JobRunner跑完一個批次就呼叫一次build_day_index()，把當天資料夾裡所有
.txt逐字稿開頭的來源資訊區塊掃出來，重新產生一份index.html。

刻意設計成「每次整份重建」而不是增量寫入——使用者同一天常常分好幾次、不連續地
開程式跑批次(例如早上跑一批、下午跑另一批)，整份重建可以確保index.html永遠
反映資料夾此刻的實際內容，不會因為程式中途關閉、或分批次執行而漏掉/算重。
"""
import html
import os
import re

_HEADER_RE = re.compile(
    r"^─+\n(.*?)\n─+", re.DOTALL
)
_KV_RE = re.compile(r"^(來源|頻道|標題|上傳日期|下載日期): (.*)$")


def _parse_txt_header(txt_path: str) -> dict:
    """讀.txt檔開頭的來源資訊區塊，解析回structured dict。讀不到就回傳空dict。"""
    try:
        with open(txt_path, "r", encoding="utf-8") as f:
            head = f.read(2000)
    except OSError:
        return {}

    m = _HEADER_RE.match(head)
    if not m:
        return {}

    info = {}
    for line in m.group(1).splitlines():
        kv = _KV_RE.match(line.strip())
        if kv:
            info[kv.group(1)] = kv.group(2)
    return info


def _find_sibling_srt(stem: str, today_dir: str) -> str | None:
    """.srt跟原始影音檔放在downloads/或uploads/，用同檔名去找對應的字幕檔。"""
    for sub in ("downloads", "uploads"):
        candidate = os.path.join(today_dir, sub, f"{stem}.srt")
        if os.path.exists(candidate):
            return os.path.join(sub, f"{stem}.srt")
    return None


def build_day_index(today_dir: str) -> str | None:
    """掃today_dir/transcripts/*.txt，重建today_dir/index.html。回傳寫出的路徑，
    沒有任何逐字稿時回傳None(不產生空的index，避免誤導)。"""
    transcripts_dir = os.path.join(today_dir, "transcripts")
    if not os.path.isdir(transcripts_dir):
        return None

    txt_files = [f for f in os.listdir(transcripts_dir) if f.endswith(".txt")]
    if not txt_files:
        return None

    # 依最後修改時間新到舊排序，最近處理的排最前面，方便使用者找「剛剛跑的那批」
    txt_files.sort(key=lambda f: os.path.getmtime(os.path.join(transcripts_dir, f)), reverse=True)

    records = []
    for f in txt_files:
        stem = f[:-4]
        txt_path = os.path.join(transcripts_dir, f)
        info = _parse_txt_header(txt_path)
        srt_rel = _find_sibling_srt(stem, today_dir)
        records.append({
            "title": info.get("標題") or stem,
            "channel": info.get("頻道"),
            "url": info.get("來源"),
            "upload_date": info.get("上傳日期"),
            "download_date": info.get("下載日期"),
            "txt_rel": os.path.join("transcripts", f),
            "srt_rel": srt_rel,
        })

    day_label = os.path.basename(today_dir.rstrip("\\/"))
    rows_html = []
    for r in records:
        title_esc = html.escape(r["title"] or "")
        if r["url"] and r["url"].startswith("http"):
            title_cell = f'<a href="{html.escape(r["url"])}" target="_blank">{title_esc}</a>'
        else:
            title_cell = title_esc
        channel_cell = html.escape(r["channel"] or "—")
        upload_cell = html.escape(r["upload_date"] or "—")
        download_cell = html.escape(r["download_date"] or "—")
        txt_href = r["txt_rel"].replace("\\", "/")
        txt_cell = f'<a href="{html.escape(txt_href)}">逐字稿</a>'
        if r["srt_rel"]:
            srt_href = r["srt_rel"].replace("\\", "/")
            srt_cell = f'<a href="{html.escape(srt_href)}">字幕</a>'
        else:
            srt_cell = "—"
        rows_html.append(
            "<tr>"
            f"<td>{title_cell}</td><td>{channel_cell}</td><td>{upload_cell}</td>"
            f"<td>{download_cell}</td><td>{txt_cell}</td><td>{srt_cell}</td>"
            "</tr>"
        )

    page = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>{day_label} 下載總覽</title>
<style>
  body {{ font-family: "Microsoft JhengHei", "Segoe UI", sans-serif; background: #1a1d24; color: #e6e8ec; margin: 0; padding: 32px; }}
  h1 {{ font-size: 20px; font-weight: 600; margin: 0 0 4px; }}
  .sub {{ color: #8b93a5; font-size: 13px; margin-bottom: 20px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
  th, td {{ text-align: left; padding: 9px 12px; border-bottom: 1px solid #2b2f3a; }}
  th {{ color: #8b93a5; font-weight: 500; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
  tr:hover {{ background: #21252f; }}
  a {{ color: #6fb8ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>{day_label} 下載總覽</h1>
<div class="sub">共 {len(records)} 支影片 · 每次批次跑完自動重建</div>
<table>
<thead><tr><th>標題</th><th>頻道</th><th>上傳日期</th><th>下載日期</th><th>逐字稿</th><th>字幕</th></tr></thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>
</body>
</html>
"""
    out_path = os.path.join(today_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    return out_path
