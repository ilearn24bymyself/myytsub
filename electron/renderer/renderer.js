// 這支檔案由後端 server.py 當靜態檔案伺服出去,跟後端同一個 origin,
// 所以可以直接用相對路徑打 /api/... ,不會有跨來源問題。

// ---- 待處理清單:選檔案/加網址先停在這裡,按「開始處理」才真的送進後端佇列 ----
// 選項在「加入當下」就鎖進這一項裡(不是按開始處理時才統一套用目前的下拉選單值),
// 這樣先加一筆選 audio、再把下拉選單改成 video 才加第二筆,不會讓第一筆也被改成 video。
// 每一項: { kind: "download" | "transcribe", url?: string, path?: string, opts: {...} }
let pending = [];

function currentOptions() {
  return {
    format_type: document.getElementById("format-type").value,
    want_srt: document.getElementById("want-srt").checked,
    skip_existing: document.getElementById("skip-existing").checked,
  };
}

function describeOpts(opts) {
  const parts = [opts.format_type === "video" ? "影片" : "音訊"];
  if (opts.want_srt) parts.push("含字幕");
  if (opts.skip_existing) parts.push("已存在則跳過");
  return parts.join("、");
}

// ---- 本機檔案的來源(使用者定的):先找影片旁的資訊檔、再找下載記錄;
// 都沒有就用檔名反查 YouTube,查到的先填進網址欄讓使用者確認;真的沒有才由使用者貼網址,
// 留空 = 本機檔案。加入清單的當下就查,按開始之前就看得到 ----
const SOURCE_LABELS = { sidecar: "影片旁的資訊檔", record: "下載記錄" };

function describeSource(meta) {
  if (!meta) return "";
  return [meta.channel, meta.title].filter(Boolean).join(" / ") || meta.url;
}

function renderSourceLine(item, box) {
  box.textContent = "";
  const src = item.source;
  const line = document.createElement("div");
  line.className = "source-line";
  box.appendChild(line);
  if (src.status === "checking") {
    line.textContent = "來源：查詢中…";
    return;
  }
  if (src.status === "sidecar" || src.status === "record") {
    line.textContent = `來源：${describeSource(src.metadata)}（${SOURCE_LABELS[src.status]}）`;
    line.classList.add("source-ok");
    return;
  }
  line.textContent = src.status === "guessed"
    ? `自動找到，請確認：${describeSource(src.metadata)}`
    : "找不到來源，請貼 YouTube 網址（留空＝本機檔案）";
  line.classList.add(src.status === "guessed" ? "source-guess" : "source-none");
  const input = document.createElement("input");
  input.type = "text";
  input.className = "source-input";
  input.placeholder = "https://www.youtube.com/watch?v=...";
  input.value = item.sourceUrl;
  input.addEventListener("input", () => { item.sourceUrl = input.value; });
  box.appendChild(input);
}

async function lookupSource(item) {
  try {
    const res = await fetch("/api/lookup-source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: item.path }),
    });
    item.source = await res.json();
  } catch (e) {
    item.source = { status: "none", metadata: null };
  }
  item.sourceUrl = item.source.status === "guessed" ? item.source.metadata.url : "";
  if (item.sourceBox) renderSourceLine(item, item.sourceBox);   // 只更新這一列,不打斷別列正在打的字
  updatePendingButtons();
}

// 送出轉錄時要帶的來源:確認過的資訊、使用者改貼的網址,或什麼都不帶(=本機檔案)
function sourceForSubmit(item) {
  const src = item.source || {};
  if (src.status === "sidecar" || src.status === "record") return { metadata: src.metadata };
  const url = (item.sourceUrl || "").trim();
  if (!url) return {};
  if (src.status === "guessed" && url === src.metadata.url) return { metadata: src.metadata };
  return { source_url: url };
}

function updatePendingButtons() {
  const hasItems = pending.length > 0;
  const stillChecking = pending.some((i) => i.source && i.source.status === "checking");
  const startBtn = document.getElementById("start-btn");
  startBtn.disabled = !hasItems || stillChecking;
  startBtn.title = stillChecking ? "還在查詢來源，請稍候" : "";
  document.getElementById("clear-pending-btn").disabled = !hasItems;
}

function renderPending() {
  const list = document.getElementById("pending-list");
  list.innerHTML = "";
  for (const item of pending) {
    const li = document.createElement("li");
    const what = item.kind === "download" ? `下載：${item.url}` : `轉錄：${item.path}`;
    const head = document.createElement("div");
    head.textContent = `${what}（${describeOpts(item.opts)}）`;
    li.appendChild(head);
    if (item.kind === "transcribe") {
      item.sourceBox = document.createElement("div");
      renderSourceLine(item, item.sourceBox);
      li.appendChild(item.sourceBox);
    }
    list.appendChild(li);
  }
  updatePendingButtons();
}

// ---- 跟後端溝通 ----

async function fetchJobs() {
  const res = await fetch("/api/jobs");
  const data = await res.json();
  renderJobs(data.jobs);
}

function stateLabel(state) {
  const labels = {
    pending: "排隊中", running: "執行中", done: "完成", error: "失敗",
    "pending-retry": "待重試", cancelled: "已取消",
  };
  return labels[state] || state;
}

// 勾選狀態跟著 job id 記,重新整理(輪詢)後同一筆項目的勾選不會被清掉,
// 只有項目本身消失(不在最新的 jobs 清單裡)才會跟著清掉。
const selectedJobIds = new Set();
const CANCELLABLE_STATES = new Set(["pending", "running"]);

// Whisper 是一次處理 30 秒音訊才吐出結果,百分比會停一陣子再一次跳一大段;
// 執行中多顯示「已執行 mm:ss」,持續在跳就代表還在跑,不是當掉
function formatElapsed(startedAt) {
  if (!startedAt) return "";
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
  const mm = String(Math.floor(sec / 60)).padStart(2, "0");
  const ss = String(sec % 60).padStart(2, "0");
  return ` · 已執行 ${mm}:${ss}`;
}

function renderJobs(jobs) {
  const liveIds = new Set(jobs.map((j) => j.id));
  for (const id of [...selectedJobIds]) {
    if (!liveIds.has(id)) selectedJobIds.delete(id);
  }

  const tbody = document.querySelector("#jobs-table tbody");
  tbody.innerHTML = "";
  for (const job of jobs) {
    const tr = document.createElement("tr");
    const percent = Math.round(job.progress || 0);
    const stateCell = `
      <span class="state-${job.state}">${stateLabel(job.state)}</span>
      ${job.message ? `<div style="font-size:12px;color:#666">${job.message}</div>` : ""}
      ${job.state === "running" ? `<div class="progress-bar"><div class="progress-fill" style="width:${percent}%"></div></div><div style="font-size:12px">${percent}%${formatElapsed(job.started_at)}</div>` : ""}
    `;
    const actions = [];
    if (job.state === "running") {
      actions.push(`<button data-cancel="${job.id}">取消</button>`);
      if (job.type === "transcribe") {
        actions.push(`<button data-pause="${job.id}" data-paused="${job.paused ? "1" : "0"}">${job.paused ? "繼續" : "暫停"}</button>`);
      }
    }
    const checkboxCell = CANCELLABLE_STATES.has(job.state)
      ? `<input type="checkbox" data-select="${job.id}" ${selectedJobIds.has(job.id) ? "checked" : ""}>`
      : "";
    tr.innerHTML = `
      <td>${checkboxCell}</td>
      <td>${job.id}</td>
      <td>${job.type === "download" ? "下載" : "轉錄"}</td>
      <td>${job.content ?? ""}</td>
      <td>${stateCell}</td>
      <td>${job.error_message ?? ""}</td>
      <td>${actions.join(" ")}</td>
    `;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("[data-cancel]").forEach((btn) => {
    btn.addEventListener("click", () => cancelJob(btn.dataset.cancel));
  });
  tbody.querySelectorAll("[data-pause]").forEach((btn) => {
    btn.addEventListener("click", () => pauseJob(btn.dataset.pause));
  });
  tbody.querySelectorAll("[data-select]").forEach((box) => {
    box.addEventListener("change", () => {
      if (box.checked) selectedJobIds.add(box.dataset.select);
      else selectedJobIds.delete(box.dataset.select);
      document.getElementById("cancel-selected-btn").disabled = selectedJobIds.size === 0;
    });
  });
  document.getElementById("cancel-selected-btn").disabled = selectedJobIds.size === 0;
}

async function cancelSelected() {
  const ids = [...selectedJobIds];
  for (const id of ids) {
    await fetch(`/api/jobs/${id}/cancel`, { method: "POST" });
  }
  selectedJobIds.clear();
  fetchJobs();
}

async function startProcessing() {
  for (const item of pending) {
    if (item.kind === "download") {
      await fetch("/api/jobs/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: item.url, ...item.opts }),
      });
    } else {
      await fetch("/api/jobs/transcribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: item.path, want_srt: item.opts.want_srt, skip_existing: item.opts.skip_existing,
          ...sourceForSubmit(item),
        }),
      });
    }
  }
  pending = [];
  renderPending();
  fetchJobs();
}

async function retryPending() {
  await fetch("/api/jobs/retry", { method: "POST" });
  fetchJobs();
}

async function cancelJob(id) {
  await fetch(`/api/jobs/${id}/cancel`, { method: "POST" });
  fetchJobs();
}

async function pauseJob(id) {
  // 暫停後按鈕變成「繼續」,再按一次送 resume —— 用 dataset 記目前是哪個動作
  const btn = document.querySelector(`[data-pause="${id}"]`);
  const isPaused = btn.dataset.paused === "1";
  await fetch(`/api/jobs/${id}/${isPaused ? "resume" : "pause"}`, { method: "POST" });
  fetchJobs();
}

// ---- 使用者操作:先加進待處理清單,不會立刻送到後端 ----

document.getElementById("pick-files").addEventListener("click", async () => {
  // window.electronAPI 由 preload.js 透過 contextBridge 曝露,
  // 回傳的是使用者選到的檔案在硬碟上的「真實路徑」,不是上傳的位元組內容。
  const paths = await window.electronAPI.pickFiles();
  const opts = currentOptions();
  const added = paths.map((p) => ({
    kind: "transcribe", path: p, opts, source: { status: "checking", metadata: null }, sourceUrl: "",
  }));
  pending.push(...added);
  renderPending();
  for (const item of added) {
    await lookupSource(item);   // 一支一支查,不要同時對 YouTube 發一堆搜尋
  }
});

document.getElementById("add-url").addEventListener("click", () => {
  const input = document.getElementById("yt-url");
  if (input.value.trim()) {
    pending.push({ kind: "download", url: input.value.trim(), opts: currentOptions() });
    input.value = "";
    renderPending();
  }
});

document.getElementById("start-btn").addEventListener("click", startProcessing);
document.getElementById("clear-pending-btn").addEventListener("click", () => {
  pending = [];
  renderPending();
});
document.getElementById("retry-btn").addEventListener("click", retryPending);
document.getElementById("refresh-btn").addEventListener("click", fetchJobs);
document.getElementById("cancel-selected-btn").addEventListener("click", cancelSelected);

renderPending();
fetchJobs();
setInterval(fetchJobs, 1500); // 簡單輪詢,先求能動,之後可以改成推播
