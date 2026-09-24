const { app, BrowserWindow, ipcMain, dialog } = require("electron");
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

app.whenReady().then(async () => {
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
