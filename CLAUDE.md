# 3-4-1.yt-down-sub-electron

YouTube 影片/本機影音檔下載 + 轉逐字稿工具的重寫版本(候選 3):Electron 桌面殼 + Python 後端,取代原本 `3-4.Yt-down-sub`(Streamlit 版)。

背景與決策記錄在對話中,尚未整理進 `CONTEXT.md`——之後跑 `/domain-modeling` 或第一次寫規格時會建立。

## Agent skills

### Issue tracker

Local markdown,`.scratch/` 底下。見 `docs/agents/issue-tracker.md`。

### Triage labels

沿用預設五種角色標籤(`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`)。見 `docs/agents/triage-labels.md`。

### Domain docs

single-context(單一 `CONTEXT.md` + `docs/adr/`,都在根目錄)。見 `docs/agents/domain.md`。
