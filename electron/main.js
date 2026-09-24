const { app, BrowserWindow, ipcMain, dialog, Menu } = require("electron");
const path = require("path");
const { spawn } = require("child_process");

let backendProcess = null;
let mainWindow = null;

// Python 執行檔怎麼找,依序:
//   1. PYTHON_EXE 環境變數(手動指定,開發除錯用)
//   2. 專案根目錄下 venv\Scripts\python.exe(票 10 的 bootstrap 會建立這個可攜式環境)
//   3. PATH 上的 python(開發機器本來就有裝的情況)
function resolvePythonExe() {
  if (process.env.PYTHON_EXE) return process.env.PYTHON_EXE;
  const venvPython = path.join(__dirname, "..", "venv", "python.exe");
  if (require("fs").existsSync(venvPython)) return venvPython;
  return "python";
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const pythonExe = resolvePythonExe();
    const serverScript = path.join(__dirname, "..", "backend", "server.py");
    // windowsHide 故意不開:這個黑色主控台視窗使用者要留著當「系統還在跑」的提示
    backendProcess = spawn(pythonExe, [serverScript], { cwd: path.join(__dirname, "..") });

    let resolved = false;
    backendProcess.stdout.on("data", (data) => {
      const text = data.toString();
      console.log(`[backend] ${text.trim()}`);
      const match = text.match(/BACKEND_PORT=(\d+)/);
      if (match && !resolved) {
        resolved = true;
        resolve(parseInt(match[1], 10));
      }
    });
    backendProcess.stderr.on("data", (data) => {
      console.error(`[backend][stderr] ${data.toString().trim()}`);
    });
    backendProcess.on("exit", (code) => {
      console.log(`[backend] 行程結束,代碼 ${code}`);
      if (!resolved) reject(new Error(`Python 後端啟動失敗(結束代碼 ${code})`));
    });
  });
}

function stopBackend() {
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill();
    backendProcess = null;
  }
}

ipcMain.handle("pick-files", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "選擇本機影片檔",
    properties: ["openFile", "multiSelections"],
    filters: [{ name: "影音檔", extensions: ["mp4", "mp3", "m4a", "wav", "webm"] }],
  });
  if (result.canceled) return [];
  return result.filePaths; // 使用者電腦上的真實磁碟路徑,不是上傳的位元組
});

// Electron 內建的預設選單是英文(File/Edit/View/Window)。這裡用同樣的項目
// (role 保留 Electron 內建行為,label 換成中文)重建一份,選單功能不變,只是看得懂。
function buildChineseMenu() {
  const template = [
    {
      label: "檔案",
      submenu: [{ role: "quit", label: "結束" }],
    },
    {
      label: "編輯",
      submenu: [
        { role: "undo", label: "復原" },
        { role: "redo", label: "取消復原" },
        { type: "separator" },
        { role: "cut", label: "剪下" },
        { role: "copy", label: "複製" },
        { role: "paste", label: "貼上" },
        { role: "selectAll", label: "全選" },
      ],
    },
    {
      label: "檢視",
      submenu: [
        { role: "reload", label: "重新載入" },
        { role: "forceReload", label: "強制重新載入" },
        { role: "toggleDevTools", label: "切換開發者工具" },
        { type: "separator" },
        { role: "resetZoom", label: "實際大小" },
        { role: "zoomIn", label: "放大" },
        { role: "zoomOut", label: "縮小" },
        { type: "separator" },
        { role: "togglefullscreen", label: "切換全螢幕" },
      ],
    },
    {
      label: "視窗",
      submenu: [
        { role: "minimize", label: "最小化" },
        { role: "zoom", label: "縮放" },
        { role: "close", label: "關閉視窗" },
      ],
    },
  ];
  return Menu.buildFromTemplate(template);
}

app.whenReady().then(async () => {
  Menu.setApplicationMenu(buildChineseMenu());
  try {
    const port = await startBackend();
    mainWindow = new BrowserWindow({
      width: 1000,
      height: 700,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    await mainWindow.loadURL(`http://127.0.0.1:${port}/`);
  } catch (err) {
    dialog.showErrorBox("啟動失敗", String(err));
    app.quit();
  }
});

app.on("window-all-closed", () => {
  stopBackend();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", stopBackend);
