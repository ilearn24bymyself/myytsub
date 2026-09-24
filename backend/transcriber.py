import datetime
import os
import sys

# -- Inject NVIDIA DLL Path Fix --
def _inject_nvidia_dll_paths():
    """動態尋找虛擬環境中 PyTorch 附帶的 NVIDIA DLL，並加入系統 PATH。"""
    try:
        # 在虛擬環境中，site-packages 通常相對於 sys.prefix
        site_packages_dir = os.path.join(sys.prefix, "Lib", "site-packages")

        # 1. 嘗試載入 nvidia 資料夾
        nvidia_dir = os.path.join(site_packages_dir, "nvidia")
        if os.path.exists(nvidia_dir):
            for lib_name in os.listdir(nvidia_dir):
                bin_path = os.path.join(nvidia_dir, lib_name, "bin")
                if os.path.isdir(bin_path):
                    if bin_path not in os.environ.get("PATH", ""):
                        os.environ["PATH"] = bin_path + os.pathsep + os.environ.get("PATH", "")
                    if hasattr(os, "add_dll_directory"):
                        try:
                            os.add_dll_directory(bin_path)
                        except Exception:
                            pass

        # 2. 嘗試載入 torch/lib 資料夾 (解決 cublas64_12.dll 找不到的問題)
        torch_lib_dir = os.path.join(site_packages_dir, "torch", "lib")
        if os.path.exists(torch_lib_dir):
            if torch_lib_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = torch_lib_dir + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(torch_lib_dir)
                except Exception:
                    pass

    except Exception as e:
        print(f"[Warning] Failed to inject NVIDIA DLL paths: {e}")

_inject_nvidia_dll_paths()
# --------------------------------

from huggingface_hub import snapshot_download
from faster_whisper import WhisperModel

# 修復 Windows cp950 終端機輸出 emoji 崩潰的問題
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Breeze-ASR-25：MediaTek Research 針對繁體中文/中英夾雜強化的 Whisper-large-v2 微調模型，
# 這裡用社群已轉換好的 CTranslate2 版本，才能沿用 faster-whisper 的推論速度。
MODEL_REPO_MAP = {
    "breeze-asr-25": "phate334/Breeze-ASR-25-ct2",
}
DEFAULT_MODEL = "breeze-asr-25"


def _ensure_model(model_size: str) -> str:
    """確保模型已下載到本地，不使用 symlink（Windows 相容）。"""
    model_dir = os.path.join(BASE_DIR, "models", model_size)
    model_bin = os.path.join(model_dir, "model.bin")
    if os.path.exists(model_bin):
        return model_dir

    print(f"[Transcriber] 首次使用，下載模型 {model_size}（約 3GB）...")
    os.makedirs(model_dir, exist_ok=True)
    repo_id = MODEL_REPO_MAP.get(model_size, f"Systran/faster-whisper-{model_size}")
    snapshot_download(
        repo_id=repo_id,
        local_dir=model_dir,
        local_dir_use_symlinks=False,
    )
    print(f"[Transcriber] 模型下載完成：{model_dir}")
    return model_dir


def _load_model(model_path: str):
    """
    優先嘗試 GPU (CUDA)；若 CUDA DLL 不存在，自動 fallback 到 CPU。
    回傳 (model, device_used, compute_type_used)。
    """
    if os.environ.get("FORCE_CPU") == "1":
        print("[Transcriber] 收到強制指令，跳過 CUDA，直接使用 CPU 模式。")
        model = WhisperModel(model_path, device="cpu", compute_type="int8")
        return model, "cpu", "int8"

    # 嘗試 GPU — 使用 int8_float32（GTX 1080 Pascal 架構支援的最快混合精度模式）
    try:
        model = WhisperModel(model_path, device="cuda", compute_type="int8_float32")
        print("[Transcriber] GPU (CUDA) 啟動成功。(compute_type=int8_float32)")
        return model, "cuda", "int8_float32"
    except Exception as e:
        print(f"[Transcriber] GPU 不可用（{e}），改用 CPU 模式。")

    # Fallback CPU
    model = WhisperModel(model_path, device="cpu", compute_type="int8")
    print("[Transcriber] CPU 模式啟動。")
    return model, "cpu", "int8"


_META_DIVIDER = "─" * 32


def _format_txt_header(metadata: dict | None, download_date: str) -> str:
    """組.txt逐字稿開頭的來源資訊區塊。metadata是None(本機上傳、或WinError重試
    救回來沒有entry可用)時，只標記本機上傳+下載日期，不假裝有完整資訊。"""
    lines = [_META_DIVIDER]
    if metadata and metadata.get("url"):
        lines.append(f"來源: {metadata.get('url')}")
        lines.append(f"頻道: {metadata.get('channel') or '未知'}")
        lines.append(f"標題: {metadata.get('title') or '未知'}")
        lines.append(f"上傳日期: {metadata.get('upload_date') or '未知'}")
    else:
        lines.append("來源: 本機上傳或無法取得原始網址資訊")
    lines.append(f"下載日期: {download_date}")
    lines.append(_META_DIVIDER)
    return "\n".join(lines)


def _format_srt_meta_line(metadata: dict | None, download_date: str) -> str:
    """組SRT第1條metadata cue的單行內容(pipe分隔，方便之後正則表達式解析回結構化資料)。"""
    if metadata and metadata.get("url"):
        parts = [
            f"來源: {metadata.get('url')}",
            f"頻道: {metadata.get('channel') or '未知'}",
            f"標題: {metadata.get('title') or '未知'}",
            f"上傳日期: {metadata.get('upload_date') or '未知'}",
            f"下載日期: {download_date}",
        ]
    else:
        parts = ["來源: 本機上傳或無法取得原始網址資訊", f"下載日期: {download_date}"]
    return "[來源資訊] " + " | ".join(parts)


def _format_srt_timestamp(seconds: float) -> str:
    """把秒數轉成 SRT 標準時間格式 HH:MM:SS,mmm。"""
    total_ms = max(0, int(round(seconds * 1000)))
    hours, total_ms = divmod(total_ms, 3600000)
    minutes, total_ms = divmod(total_ms, 60000)
    secs, ms = divmod(total_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


class Transcriber:
    def __init__(self, model_size=DEFAULT_MODEL):
        model_path = _ensure_model(model_size)
        self.model, self.device, self.compute_type = _load_model(model_path)

    def transcribe(self, audio_path: str, progress_callback=None, pause_event=None, stop_event=None):
        """
        解析音訊，回傳 (無時間軸的純文字, segment 清單)。
        segment 清單每筆為 {"start": float, "end": float, "text": str}，供輸出 SRT 字幕使用。
        支援即時進度回報。

        pause_event/stop_event：faster-whisper 是逐個 segment 產生結果的 generator，
        每算完一個 segment 就檢查一次這兩個旗標——暫停時卡在這裡等，停止時直接
        中斷迴圈、回傳目前已經算出來的部分結果，不會硬等整個檔案跑完。
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"找不到音訊檔：{audio_path}")

        # 強制指定語言與繁體中文提示詞，讓 Whisper 輸出繁體
        segments, info = self.model.transcribe(
            audio_path,
            beam_size=5,
            language="zh",
            initial_prompt="以下是普通話的句子，請以繁體中文輸出。"
        )

        lines = []
        seg_list = []
        for segment in segments:
            if pause_event is not None:
                pause_event.wait()
            if stop_event is not None and stop_event.is_set():
                break

            text = segment.text.strip()
            if text:
                lines.append(text)
                seg_list.append({"start": segment.start, "end": segment.end, "text": text})

            # 回報進度 (0.0 ~ 100.0)
            if progress_callback and info.duration > 0:
                percent = min(100.0, (segment.end / info.duration) * 100)
                progress_callback(percent)

        return "\n".join(lines), seg_list

    def save_transcript(self, text: str, output_path: str, metadata: dict | None = None):
        """將文字結果存成 .txt 檔，開頭附上影片來源資訊(不用側車檔，打開.txt就看得到)。"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        download_date = datetime.date.today().strftime("%Y-%m-%d")
        header = _format_txt_header(metadata, download_date)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header + "\n\n" + text)

    def save_srt(self, segments: list, output_path: str, metadata: dict | None = None):
        """將 segment 清單存成標準 .srt 字幕檔。

        來源資訊塞成編號1、時長只有1毫秒(00:00:00,000-->00:00:00,001)的字幕cue，
        放在最前面——用文字編輯器打開一眼就看到，播放時只閃過1毫秒不影響觀看。
        原本的字幕條目全部往後遞增編號，時間軸本身不受影響。
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        download_date = datetime.date.today().strftime("%Y-%m-%d")
        lines = ["1", "00:00:00,000 --> 00:00:00,001", _format_srt_meta_line(metadata, download_date), ""]
        for offset, seg in enumerate(segments):
            idx = offset + 2
            lines.append(str(idx))
            lines.append(f"{_format_srt_timestamp(seg['start'])} --> {_format_srt_timestamp(seg['end'])}")
            lines.append(seg["text"])
            lines.append("")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
