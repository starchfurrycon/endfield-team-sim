// 终末地配队模拟 · Electron 桌面壳
// 开发：加载仓库里的 run.py serve；打包：加载随包的 endfield-server.exe
// 启动状态机：starting → server_up → page_loaded（失败 → failed）
const { app, BrowserWindow, dialog, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');
const net = require('net');

const PACKAGED = app.isPackaged;
const APP_HOME = process.env.EF_HOME
  ? path.resolve(process.env.EF_HOME)
  : (PACKAGED ? path.dirname(process.execPath) : path.resolve(__dirname, '..'));
const DATA_DIR = path.join(APP_HOME, 'data');
const LOG_FILE = path.join(DATA_DIR, 'logs', 'server.log');
const SERVER_EXE = path.join(process.resourcesPath || APP_HOME, 'server', 'endfield-server.exe');
const ICON = path.join(__dirname, 'assets', 'icon.png');

let win = null;
let serverProc = null;
let serverPort = 0;
let exiting = false;
const logTail = [];

function log(...args) {
  const line = args.map(String).join(' ');
  console.log(line);
  logTail.push(line);
  if (logTail.length > 200) logTail.shift();
  try {
    fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
    fs.appendFileSync(LOG_FILE, line + '\n');
  } catch (_) { /* 忽略日志写失败 */ }
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
  });
}

function pythonCmd() {
  if (process.env.EF_PYTHON) return [process.env.EF_PYTHON, []];
  return process.platform === 'win32' ? ['python', []] : ['python3', []];
}

function startServer(port) {
  const env = {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',
    EF_HOME: APP_HOME,
    EF_PORT: String(port),
  };
  let cmd, args, cwd;
  if (PACKAGED) {
    cmd = SERVER_EXE;
    args = [String(port)];
    cwd = APP_HOME;
  } else {
    [cmd, args] = pythonCmd();
    args = args.concat([path.join(APP_HOME, 'run.py'), 'serve',
                        '--port', String(port), '--no-browser']);
    cwd = APP_HOME;
  }
  log(`[shell] start ${cmd} ${args.join(' ')} (cwd=${cwd})`);
  const proc = spawn(cmd, args, { cwd, env, windowsHide: true,
                                  stdio: ['ignore', 'pipe', 'pipe'] });
  const onData = (d) => log('[server] ' + String(d).trimEnd());
  proc.stdout.on('data', onData);
  proc.stderr.on('data', onData);
  proc.on('error', (e) => log('[shell] spawn error: ' + e.message));
  proc.on('exit', (code) => {
    log(`[shell] server exit code=${code}`);
    if (!exiting && win && !win.isDestroyed()) {
      showFailure(`后端服务已退出（code=${code}）。\n\n最近日志：\n${logTail.slice(-8).join('\n')}`);
    }
  });
  return proc;
}

function ping(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/api/status',
                           timeout: 1200 }, (res) => {
      res.resume();
      resolve(true);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

async function waitForServer(port, timeoutMs = 180000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await ping(port)) return true;
    if (serverProc && serverProc.exitCode !== null) return false;
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

function splashHtml(msg) {
  const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  html,body{height:100%;margin:0;background:#060708;color:#e9eef2;
    font-family:Bahnschrift,"Segoe UI","Microsoft YaHei UI",sans-serif;
    display:flex;align-items:center;justify-content:center}
  .box{width:min(520px,86vw);border:1px solid rgba(158,178,192,.28);padding:26px 28px;
    position:relative;background:linear-gradient(180deg,#0d1114,#090b0d)}
  .box:before,.box:after{content:"";position:absolute;width:12px;height:12px;
    border:1px solid #ffd23f}
  .box:before{left:-1px;top:-1px;border-right:0;border-bottom:0}
  .box:after{right:-1px;bottom:-1px;border-left:0;border-top:0}
  .lat{font-family:Consolas,monospace;font-size:10px;letter-spacing:.3em;
    color:#ffd23f;text-transform:uppercase}
  h1{font-size:16px;margin:8px 0 16px;font-weight:600}
  .bar{height:6px;background:rgba(255,255,255,.07);border:1px solid rgba(158,178,192,.2)}
  .bar i{display:block;height:100%;width:38%;background:
    repeating-linear-gradient(90deg,#ffd23f 0 8px,rgba(255,210,63,.2) 8px 10px);
    animation:run 1.1s linear infinite}
  @keyframes run{from{transform:translateX(-100%)}to{transform:translateX(340%)}}
  .msg{margin-top:12px;font-family:Consolas,monospace;font-size:11px;color:#8b9aa6}
  </style></head><body><div class="box">
  <div class="lat">Endfield // Squad Simulator</div>
  <h1>正在启动本地服务</h1>
  <div class="bar"><i></i></div>
  <div class="msg">${msg.replace(/[<>&]/g, '')}</div>
  </div></body></html>`;
  return 'data:text/html;charset=utf-8,' + encodeURIComponent(html);
}

function showFailure(detail) {
  if (!win || win.isDestroyed()) return;
  const safe = String(detail || '').replace(/[<>&]/g, '');
  win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(
    `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
     body{background:#060708;color:#e9eef2;font-family:Bahnschrift,"Microsoft YaHei UI",sans-serif;
     padding:44px;line-height:1.7}
     pre{background:#0d1114;border:1px solid rgba(158,178,192,.25);padding:12px;
     white-space:pre-wrap;font:11px/1.6 Consolas,monospace;color:#8b9aa6}
     b{color:#ffd23f;font-family:Consolas,monospace;letter-spacing:.2em;font-size:11px}
     </style></head><body><b>ENDFIELD // FAILED</b>
     <h2>后端没有启动成功</h2><pre>${safe}</pre>
     <p style="color:#8b9aa6;font-size:12px">可尝试：删除 data 目录后重启；或查看 data/logs/server.log</p>
     </body></html>`));
}

function createWindow() {
  win = new BrowserWindow({
    width: 1500, height: 940,
    minWidth: 1180, minHeight: 720,
    backgroundColor: '#060708',
    title: '终末地配队模拟',
    icon: fs.existsSync(ICON) ? ICON : undefined,
    show: true,
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#060708', symbolColor: '#c9d4dc', height: 40 },
    webPreferences: { contextIsolation: true, nodeIntegration: false,
                      sandbox: true, spellcheck: false },
  });
  win.setMenuBarVisibility(false);
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  win.on('closed', () => { win = null; });
}

async function boot() {
  createWindow();
  win.loadURL(splashHtml('正在拉起后端…'));

  serverPort = Number(process.env.EF_PORT) || await freePort();
  serverProc = startServer(serverPort);

  const ok = await waitForServer(serverPort);
  if (!ok) {
    showFailure(`后端未就绪（端口 ${serverPort}）。\n\n最近日志：\n${logTail.slice(-10).join('\n')}`);
    return;
  }
  log(`[shell] server ready on ${serverPort}`);
  await win.loadURL(`http://127.0.0.1:${serverPort}/?shell=1`);
  log('[shell] page loaded');
}

function killServer() {
  if (!serverProc || serverProc.exitCode !== null) return;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/PID', String(serverProc.pid), '/T', '/F'],
            { windowsHide: true, stdio: 'ignore' });
    } else {
      serverProc.kill('SIGTERM');
    }
    log('[shell] server stopped');
  } catch (e) {
    log('[shell] kill failed: ' + e.message);
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) { if (win.isMinimized()) win.restore(); win.focus(); }
  });
  app.whenReady().then(boot).catch((e) => {
    log('[shell] boot failed: ' + e.stack);
    showFailure(String(e));
  });
  app.on('window-all-closed', () => { exiting = true; killServer(); app.quit(); });
  app.on('before-quit', () => { exiting = true; killServer(); });
}
