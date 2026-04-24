const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn, execSync } = require('child_process');
const fs = require('fs');
const { getTopMovers, searchCards, getCardDetail, getScraperHealth, getRecentSignals } = require('./db-queries.cjs');

app.setName('FCPriceMaster');

const isDev = process.env.NODE_ENV !== 'production';
const PROJECT_ROOT = path.join(__dirname, '..', '..');
const DB_PATH = path.join(PROJECT_ROOT, 'data', 'fcpricemaster.db');
const SETTINGS_PATH = path.join(PROJECT_ROOT, 'data', 'settings.json');

// ---------------------------------------------------------------------------
// Settings helpers
// ---------------------------------------------------------------------------
function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(SETTINGS_PATH, 'utf8'));
  } catch {
    return { autoStartBackend: true };
  }
}

function writeSettings(settings) {
  try {
    fs.mkdirSync(path.dirname(SETTINGS_PATH), { recursive: true });
    fs.writeFileSync(SETTINGS_PATH, JSON.stringify(settings, null, 2));
  } catch (e) {
    console.error('Failed to write settings:', e.message);
  }
}

// ---------------------------------------------------------------------------
// DB handle (main process owns it; opened once on ready, closed on quit)
// ---------------------------------------------------------------------------
let _db = null;

function openDb() {
  if (_db) return _db;
  const Database = require('better-sqlite3');
  _db = new Database(DB_PATH, { readonly: true });
  _db.pragma('journal_mode = WAL');
  return _db;
}

app.on('will-quit', () => {
  if (_db) { try { _db.close(); } catch {} _db = null; }
});

// ---------------------------------------------------------------------------
// Backend process management
// ---------------------------------------------------------------------------
let backendProc = null;
let discordProc = null;

function resolveUv() {
  const candidates = [
    process.env.UV_EXE,
    path.join(process.env.USERPROFILE || '', '.local', 'bin', 'uv.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'uv', 'bin', 'uv.exe'),
  ].filter(Boolean);
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch {}
  }
  return 'uv';
}

function startBackend() {
  const settings = readSettings();
  if (!settings.autoStartBackend) {
    console.log('[main] autoStartBackend=false — skipping backend spawn');
    return;
  }
  const uvExe = resolveUv();
  const backendDir = path.join(PROJECT_ROOT, 'backend');
  console.log('[main] Starting backend with:', uvExe);
  backendProc = spawn(uvExe, ['run', 'python', '-m', 'src.workers.scheduler'], {
    cwd: backendDir,
    stdio: 'pipe',
    windowsHide: true,
    shell: false,
  });
  backendProc.stdout.on('data', d => process.stdout.write('[backend] ' + d));
  backendProc.stderr.on('data', d => process.stderr.write('[backend] ' + d));
  backendProc.on('exit', code => console.log('[backend] exited', code));
}

function stopBackend() {
  if (!backendProc) return;
  try {
    if (process.platform === 'win32') {
      execSync(`taskkill /F /T /PID ${backendProc.pid}`, { stdio: 'ignore' });
    } else {
      backendProc.kill('SIGTERM');
    }
  } catch {}
  backendProc = null;
}

function startDiscordIngest() {
  const settings = readSettings();
  const enabled = settings.enableDiscordIngest !== false;  // default true
  if (!enabled) {
    console.log('[main] enableDiscordIngest=false — skipping discord ingest spawn');
    return;
  }
  const uvExe = resolveUv();
  const backendDir = path.join(PROJECT_ROOT, 'backend');
  console.log('[discord] Starting Discord ingest worker with:', uvExe);
  discordProc = spawn(uvExe, ['run', 'python', '-m', 'src.workers.discord_ingest'], {
    cwd: backendDir,
    stdio: 'pipe',
    windowsHide: true,
    shell: false,
  });
  discordProc.stdout.on('data', d => process.stdout.write('[discord] ' + d));
  discordProc.stderr.on('data', d => process.stderr.write('[discord] ' + d));
  discordProc.on('exit', code => console.log('[discord] exited', code));
}

function stopDiscordIngest() {
  if (!discordProc) return;
  try {
    if (process.platform === 'win32') {
      execSync(`taskkill /F /T /PID ${discordProc.pid}`, { stdio: 'ignore' });
    } else {
      discordProc.kill('SIGTERM');
    }
  } catch {}
  discordProc = null;
}

// ---------------------------------------------------------------------------
// IPC handlers — DB queries (main process owns DB, preload just relays)
// ---------------------------------------------------------------------------
ipcMain.handle('db:getTopMovers', (_e, opts) => getTopMovers(openDb(), opts));
ipcMain.handle('db:searchCards',  (_e, opts) => searchCards(openDb(), opts));
ipcMain.handle('db:getCardDetail', (_e, opts) => getCardDetail(openDb(), opts));
ipcMain.handle('db:getScraperHealth', (_e, opts) => getScraperHealth(openDb(), opts));
ipcMain.handle('db:getRecentSignals', (_e, opts) => getRecentSignals(openDb(), opts));

// ---------------------------------------------------------------------------
// IPC handlers — settings + backend control
// ---------------------------------------------------------------------------
ipcMain.handle('get-settings', () => readSettings());
ipcMain.handle('set-setting', (_e, key, value) => {
  const s = readSettings();
  s[key] = value;
  writeSettings(s);
  return s;
});
ipcMain.handle('restart-backend', () => { stopBackend(); startBackend(); });
ipcMain.handle('stop-backend', () => stopBackend());
ipcMain.handle('backend-running', () => !!backendProc && !backendProc.exitCode);

// ---------------------------------------------------------------------------
// Self-test mode  (electron . --selftest)
// ---------------------------------------------------------------------------
const isSelfTest = process.argv.slice(2).includes('--selftest');

if (isSelfTest) {
  app.whenReady().then(() => {
    try {
      const db = openDb();

      const topMovers    = getTopMovers(db,     { platform: 'pc', hoursBack: 24, limit: 5 });
      const cards        = searchCards(db,      { query: 'Mbappe' });
      const cardDetail   = getCardDetail(db,    { cardKey: 'mbappe-toty-fc26', platform: 'pc' });
      const health       = getScraperHealth(db, {});
      const signals      = getRecentSignals(db, { hoursBack: 168, limit: 10 });

      const result = {
        selftest: true,
        db_path: DB_PATH,
        handlers: {
          getTopMovers:    { platform: 'pc', count: topMovers.length,  rows: topMovers },
          searchCards:     { query: 'Mbappe', count: cards.length,     rows: cards },
          getCardDetail:   { card_key: 'mbappe-toty-fc26', snapshots: cardDetail?.snapshots?.length ?? 0, attrs: cardDetail?.attrs?.length ?? 0 },
          getScraperHealth:{ count: health.length, rows: health },
          getRecentSignals:{ count: signals.length, rows: signals },
        },
      };
      process.stdout.write(JSON.stringify(result, null, 2) + '\n');
      app.exit(0);
    } catch (err) {
      process.stderr.write('SELFTEST ERROR: ' + err.stack + '\n');
      app.exit(1);
    }
  });
  return;
}

// ---------------------------------------------------------------------------
// Normal app launch
// ---------------------------------------------------------------------------
function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    title: 'FCPriceMaster',
    backgroundColor: '#0f172a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  win.setTitle('FCPriceMaster');

  if (isDev) {
    win.loadURL('http://localhost:5173');
  } else {
    win.loadFile(path.join(__dirname, '../dist/index.html'));
  }
}

app.whenReady().then(() => {
  startBackend();
  startDiscordIngest();
  createWindow();
});

app.on('window-all-closed', () => {
  stopBackend();
  stopDiscordIngest();
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
