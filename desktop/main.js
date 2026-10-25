// Electron 桌面壳：拉起 Python 后端，再加载本地 UI。
// 依赖只有 electron；后端用仓库自带的 run.py（无需打包 Python）。
const { app, BrowserWindow, dialog, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const ROOT = path.resolve(__dirname, '..');
const PORT = Number(process.env.EF_PORT || 8778);

let py = null;
let win = null;

function pythonCmd() {
  // 优先用环境变量，其次 PATH 里的 python / py
  if (process.env.EF_PYTHON) return { cmd: process.env.EF_PYTHON, args: [] };
  if (process.platform === 'win32') return { cmd: 'python', args: [] };
  return { cmd: 'python3', args: [] };
}

function startBackend() {
  const { cmd, args } = pythonCmd();
  py = spawn(cmd, [...args, path.join(ROOT, 'run.py'), 'serve',
                   '--port', String(PORT), '--no-browser'], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });
  py.stdout.on('data', (d) => process.stdout.write('[py] ' + d));
  py.stderr.on('data', (d) => process.stderr.write('[py] ' + d));
  py.on('exit', (code) => {
    if (win && !win.isDestroyed() && code !== 0) {
      dialog.showErrorBox('后端已退出',
        `Python 服务退出码 ${code}。请先在项目根目录运行：\n\npython run.py build`);
    }
  });
}

function waitForServer(tries = 60) {
  return new Promise((resolve, reject) => {
    const tick = (n) => {
      http.get({ host: '127.0.0.1', port: PORT, path: '/api/meta', timeout: 1500 },
        (res) => { res.resume(); resolve(true); })
        .on('error', () => n <= 0 ? reject(new Error('后端未就绪'))
                                 : setTimeout(() => tick(n - 1), 500));
    };
    tick(tries);
  });
}

async function createWindow() {
  win = new BrowserWindow({
    width: 1360, height: 900,
    backgroundColor: '#0e1116',
    title: '终末地配队模拟',
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  win.setMenuBarVisibility(false);
  try {
    await waitForServer();
    await win.loadURL(`http://127.0.0.1:${PORT}/`);
  } catch (e) {
    await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(
      `<body style="background:#0e1116;color:#e6edf3;font:14px sans-serif;padding:40px">
       <h2>后端没起来</h2>
       <p>请先在项目根目录执行：</p>
       <pre style="background:#161b22;padding:12px;border-radius:8px">python run.py build</pre>
       <p>错误：${String(e)}</p></body>`));
  }
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url); return { action: 'deny' };
  });
}

app.whenReady().then(() => { startBackend(); createWindow(); });

app.on('window-all-closed', () => { if (py) py.kill(); app.quit(); });
app.on('before-quit', () => { if (py) py.kill(); });
