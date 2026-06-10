const electron = require('electron');
const path = require('path');
const isSelfTest = process.argv.slice(2).includes('--selftest');
if (isSelfTest && !electron.app && process.env.FC_SELFTEST_NODE_BRIDGE !== '1') {
  const { spawnSync } = require('child_process');
  const electronBin = typeof electron === 'string'
    ? electron
    : path.join(__dirname, '..', 'node_modules', '.bin', process.platform === 'win32' ? 'electron.cmd' : 'electron');
  const result = spawnSync(electronBin, ['.', '--selftest'], {
    cwd: path.join(__dirname, '..'),
    stdio: 'inherit',
    env: { ...process.env, FC_SELFTEST_NODE_BRIDGE: '1' },
    shell: false,
    windowsHide: true,
  });
  process.exit(result.status ?? 1);
}
const app = electron.app || {
  setName() {},
  on() {},
  whenReady: () => Promise.resolve(),
  exit: (code = 0) => process.exit(code),
  quit: () => process.exit(0),
};
const BrowserWindow = electron.BrowserWindow;
const ipcMain = electron.ipcMain || { handle() {} };
const { spawn, execSync } = require('child_process');
const fs = require('fs');
const { getTopMovers, searchCards, getCardDetail, getScraperHealth, getRecentSignals,
        getFodderSummary, getFodderSnapshot, getFodderByRating, getFodderHistory,
        getLLMHistory,
        getRecommendations, dismissRecommendation, getRecommendationStats,
        getRecommendationBudgetStatus } = require('./db-queries.cjs');

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

// Writable DB — used only for LLM call logging (llm_calls table writes)
let _writeDb = null;
function openWriteDb() {
  if (_writeDb) return _writeDb;
  const Database = require('better-sqlite3');
  _writeDb = new Database(DB_PATH);
  _writeDb.pragma('journal_mode = WAL');
  return _writeDb;
}

app.on('will-quit', () => {
  if (_db) { try { _db.close(); } catch {} _db = null; }
  if (_writeDb) { try { _writeDb.close(); } catch {} _writeDb = null; }
});

// ---------------------------------------------------------------------------
// LLM helpers
// ---------------------------------------------------------------------------

const NVIDIA_BASE_URL = 'https://integrate.api.nvidia.com/v1';

const NVIDIA_MODELS = {
  'deepseek-v4-pro': { modelId: 'deepseek-ai/deepseek-v4-pro',       displayName: 'DeepSeek V4 Pro' },
  'kimi-k2-6':       { modelId: 'moonshotai/kimi-k2.6',              displayName: 'Kimi K2.6' },
  'qwen3-80b':       { modelId: 'qwen/qwen3-next-80b-a3b-instruct',  displayName: 'Qwen3 80B' },
  'mistral-small':   { modelId: 'mistralai/mistral-small-4-119b-2603', displayName: 'Mistral Small' },
  'gpt-oss-120b':    { modelId: 'openai/gpt-oss-120b',               displayName: 'GPT OSS 120B' },
  'mistral-vision':  { modelId: 'mistralai/mistral-small-4-119b-2603', displayName: 'Mistral Vision' },
};

function getAnthropicKey() {
  const key = process.env.ANTHROPIC_API_KEY;
  if (key) return key.trim();
  try {
    const content = fs.readFileSync(path.join(PROJECT_ROOT, '.env'), 'utf8');
    for (const line of content.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (trimmed.startsWith('ANTHROPIC_API_KEY')) {
        const eqIdx = trimmed.indexOf('=');
        if (eqIdx >= 0) return trimmed.slice(eqIdx + 1).trim().replace(/^["']|["']$/g, '');
      }
    }
  } catch {}
  return null;
}

function readLLMConfig() {
  try {
    const content = fs.readFileSync(path.join(PROJECT_ROOT, 'config', 'llm_config.yaml'), 'utf8');
    const capMatch = content.match(/daily_cap_usd\s*:\s*([\d.]+)/);
    return { daily_cap_usd: capMatch ? parseFloat(capMatch[1]) : 0.50 };
  } catch { return { daily_cap_usd: 0.50 }; }
}

function checkDailyCap(db, capUsd) {
  const today = new Date().toISOString().slice(0, 10);
  try {
    const row = db.prepare(
      `SELECT COALESCE(SUM(cost_usd),0) AS total, COUNT(*) AS cnt
       FROM llm_calls WHERE ts_utc >= ?`
    ).get(today + 'T00:00:00Z');
    if (row.total >= capUsd) {
      throw new Error(`Daily AI budget reached ($${capUsd.toFixed(2)}). Resets at midnight UTC.`);
    }
    return { total: row.total, count: row.cnt };
  } catch (e) {
    if (e.message.includes('Daily AI budget')) throw e;
    return { total: 0, count: 0 };
  }
}

function buildAskContext(db, text, platform) {
  // Match cards by name substring
  const allCards = db.prepare('SELECT id, card_key, player_name, version_name FROM cards').all();
  const textLower = text.toLowerCase();
  const mentionedCards = [];
  for (const card of allCards) {
    const name = card.player_name.toLowerCase();
    if (name.length >= 4 && textLower.includes(name)) {
      const price = db.prepare(
        `SELECT bin_price FROM price_snapshots
         WHERE card_id=? AND platform=? AND bin_price IS NOT NULL
         ORDER BY ts_utc DESC LIMIT 1`
      ).get(card.id, platform);
      const price24h = db.prepare(
        `SELECT bin_price FROM price_snapshots
         WHERE card_id=? AND platform=? AND bin_price IS NOT NULL
           AND ts_utc <= datetime('now','-24 hours')
         ORDER BY ts_utc DESC LIMIT 1`
      ).get(card.id, platform);
      mentionedCards.push({
        ...card,
        current_price: price?.bin_price ?? null,
        price_24h_ago: price24h?.bin_price ?? null,
      });
    }
  }

  // Fodder context for mentioned ratings
  const ratingMatches = text.match(/\b(8[2-9]|9[01])\b/g) || [];
  const ratings = [...new Set(ratingMatches.map(r => parseInt(r)))];
  const fodderContext = [];
  for (const rating of ratings) {
    try {
      const snap = db.prepare(
        `SELECT cheapest_bin, median_bin, ts_utc FROM fodder_snapshots
         WHERE rating=? AND platform=? ORDER BY ts_utc DESC LIMIT 1`
      ).get(rating, platform);
      if (snap) fodderContext.push({ rating, ...snap });
    } catch {}
  }

  // Recent signals
  const recentSignals = db.prepare(
    `SELECT raw_text, source, ts_utc, COALESCE(signal_context, 'fut_market') AS signal_context FROM signals
     WHERE raw_text IS NOT NULL ORDER BY ts_utc DESC LIMIT 10`
  ).all();

  return { mentionedCards, fodderContext, recentSignals, platform };
}

function formatUserMessage(context, tradeCallText) {
  const lines = [`Trade call: ${tradeCallText}`, '', `Platform: ${context.platform.toUpperCase()}`, ''];

  if (context.mentionedCards.length > 0) {
    lines.push('Mentioned cards:');
    for (const c of context.mentionedCards) {
      const price = c.current_price ? c.current_price.toLocaleString() : 'N/A';
      const change = c.current_price && c.price_24h_ago && c.price_24h_ago > 0
        ? ` | 24h: ${(((c.current_price - c.price_24h_ago) / c.price_24h_ago) * 100).toFixed(1)}%`
        : '';
      lines.push(`  - ${c.player_name} (${c.version_name}): ${price} coins${change}`);
    }
    lines.push('');
  }

  if (context.fodderContext.length > 0) {
    lines.push('Fodder context:');
    for (const f of context.fodderContext) {
      lines.push(`  - Rating ${f.rating}: cheapest ${f.cheapest_bin || 'N/A'}, median ${f.median_bin || 'N/A'}`);
    }
    lines.push('');
  }

  if (context.recentSignals.length > 0) {
    lines.push('Recent market signals:');
    for (const s of context.recentSignals.slice(0, 5)) {
      lines.push(`  [${s.source} | ${s.signal_context || 'fut_market'}] ${(s.raw_text || '').slice(0, 120)}`);
    }
  }

  return lines.join('\n');
}

const LLM_SYSTEM_PROMPT = `You are FCPriceMaster, an EA FC Ultimate Team market analyst. You have deep knowledge of how the FUT transfer market works, including:

Promo cycles (TOTW, TOTY, FUT Birthday, TOTS, Winter Wildcards, etc.)
How promo releases cause price spikes followed by corrections
How SBCs drive demand for fodder cards at specific ratings
How TOTW makes a player's gold card go OOP (out of packs), often increasing its price if used in SBCs
How market hype causes temporary over-pricing that typically corrects within 24-48h
The difference between PC and Console markets

When evaluating a trade call, always consider:

Current price vs historical baseline (is this already hyped/overpriced?)
Upcoming calendar events that could affect demand
Whether the reasoning in the call is sound given current market data
Hold time vs risk

Signals tagged irl_transfer or irl_result are real-world football news, not FUT market data. A real-world transfer fee (e.g. €150M to Real Madrid) does NOT indicate a FUT card price. A high-profile IRL move may create mild in-game demand — treat as weak positive sentiment only, never as price evidence. Signals tagged promo_leak are high priority.

Always respond in this exact JSON format:
{
  "verdict": "buy" | "hold" | "avoid",
  "confidence": 0-100,
  "reasoning": "2-3 sentence explanation",
  "price_context": "what current prices tell us",
  "risk": "low" | "medium" | "high",
  "suggested_buy_price": null or number,
  "suggested_sell_price": null or number,
  "horizon": "short (hours)" | "medium (days)" | "long (weeks)"
}
Respond ONLY with the JSON object. No preamble, no markdown fences.`;

function getNvidiaKey() {
  const key = process.env.NVIDIA_API_KEY;
  if (key) return key.trim();
  try {
    const content = fs.readFileSync(path.join(PROJECT_ROOT, '.env'), 'utf8');
    for (const line of content.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (trimmed.startsWith('NVIDIA_API_KEY')) {
        const eqIdx = trimmed.indexOf('=');
        if (eqIdx >= 0) return trimmed.slice(eqIdx + 1).trim().replace(/^["']|["']$/g, '');
      }
    }
  } catch {}
  return null;
}

function parseVerdictText(raw, providerId, providerName) {
  let text = raw.trim();
  if (text.startsWith('```')) {
    const lines = text.split('\n');
    const start = lines[0].startsWith('```') ? 1 : 0;
    const end = lines[lines.length - 1].trim() === '```' ? lines.length - 1 : lines.length;
    text = lines.slice(start, end).join('\n').trim();
  }
  const v = JSON.parse(text);
  return {
    provider_id: providerId,
    provider_name: providerName,
    action: v.verdict || 'hold',
    confidence: v.confidence ?? 50,
    reasoning: v.reasoning || '',
    price_context: v.price_context || '',
    risk: v.risk || 'medium',
    horizon: v.horizon || 'medium (days)',
    suggested_buy_price: v.suggested_buy_price ?? null,
    suggested_sell_price: v.suggested_sell_price ?? null,
    cost_usd: 0,
  };
}

async function callNvidiaModel(apiKey, modelId, userMessage, imageB64 = null) {
  const messages = imageB64
    ? [{
        role: 'user',
        content: [
          { type: 'image_url', image_url: { url: `data:image/jpeg;base64,${imageB64}` } },
          { type: 'text', text: `${LLM_SYSTEM_PROMPT}\n\n${userMessage}` },
        ],
      }]
    : [
        { role: 'system', content: LLM_SYSTEM_PROMPT },
        { role: 'user', content: userMessage },
      ];
  const body = JSON.stringify({
    model: modelId,
    messages,
    max_tokens: 500,
    temperature: 0,
  });
  const response = await fetch(`${NVIDIA_BASE_URL}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
      'Accept': 'application/json',
    },
    body,
  });
  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`NVIDIA API ${response.status}: ${errText.slice(0, 200)}`);
  }
  return response.json();
}

async function callAnthropic(apiKey, userMessage) {
  const body = JSON.stringify({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 1000,
    temperature: 0,
    system: LLM_SYSTEM_PROMPT,
    messages: [{ role: 'user', content: userMessage }],
  });

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body,
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Anthropic API ${response.status}: ${errText.slice(0, 200)}`);
  }
  return response.json();
}

function logLLMCall(db, { model, inputTokens, outputTokens, inputText, outputJson, feature = 'ask' }) {
  const INPUT_COST = 0.00000025;
  const OUTPUT_COST = 0.00000125;
  const cost = inputTokens * INPUT_COST + outputTokens * OUTPUT_COST;
  try {
    db.prepare(
      `INSERT INTO llm_calls (model, input_tokens, output_tokens, cost_usd, feature, input_text, output_json)
       VALUES (?,?,?,?,?,?,?)`
    ).run(model, inputTokens, outputTokens, cost, feature, inputText, outputJson);
  } catch (e) {
    console.error('[askLLM] Failed to log call:', e.message);
  }
}

ipcMain.handle('db:getProviderAvailability', () => ({
  haiku: !!getAnthropicKey(),
  nvidia: !!(getNvidiaKey()?.startsWith('nvapi-')),
}));

// Build context + user message without calling any LLM (used by Ask for incremental per-model calls)
ipcMain.handle('db:buildAskContext', (_e, { trade_call, platform }) => {
  const db = openDb();
  const ctx = buildAskContext(db, trade_call || 'Image analysis', platform || 'pc');
  return {
    userMessage: formatUserMessage(ctx, trade_call || 'Image analysis'),
    context_info: {
      cards: ctx.mentionedCards.map(c => c.player_name),
      signals_count: ctx.recentSignals.length,
    },
  };
});

// Call a single LLM provider and return its verdict + timing
ipcMain.handle('db:callSingleProvider', async (_e, { provider_id, user_message, image_b64 = null, input_text = '' }) => {
  const start = Date.now();

  if (provider_id === 'haiku') {
    const apiKey = getAnthropicKey();
    if (!apiKey) return { provider_id: 'haiku', provider_name: 'Claude Haiku', error: 'ANTHROPIC_API_KEY not set', cost_usd: 0, elapsed_ms: 0 };
    try {
      const cfg = readLLMConfig();
      const wDb = openWriteDb();
      checkDailyCap(wDb, cfg.daily_cap_usd);
      const apiResponse = await callAnthropic(apiKey, user_message);
      const raw = apiResponse.content[0].text;
      const result = parseVerdictText(raw, 'haiku', 'Claude Haiku');
      const inputTokens = apiResponse.usage?.input_tokens || 0;
      const outputTokens = apiResponse.usage?.output_tokens || 0;
      result.cost_usd = inputTokens * 0.00000025 + outputTokens * 0.00000125;
      result.elapsed_ms = Date.now() - start;
      // Log for budget tracking (feature='ask')
      logLLMCall(wDb, {
        model: apiResponse.model || 'claude-haiku-4-5-20251001',
        inputTokens, outputTokens,
        inputText: input_text,
        outputJson: JSON.stringify({ verdict: result.action, confidence: result.confidence, reasoning: result.reasoning }),
        feature: 'ask',
      });
      return result;
    } catch (e) {
      return { provider_id: 'haiku', provider_name: 'Claude Haiku', error: e.message, cost_usd: 0, elapsed_ms: Date.now() - start };
    }
  }

  const modelInfo = NVIDIA_MODELS[provider_id];
  if (!modelInfo) return { provider_id, provider_name: provider_id, error: `Unknown provider: ${provider_id}`, cost_usd: 0, elapsed_ms: 0 };
  const nvidiaKey = getNvidiaKey();
  if (!nvidiaKey) return { provider_id, provider_name: modelInfo.displayName, error: 'NVIDIA_API_KEY not set', cost_usd: 0, elapsed_ms: 0 };
  try {
    const apiResponse = await callNvidiaModel(
      nvidiaKey,
      modelInfo.modelId,
      user_message,
      provider_id === 'mistral-vision' ? image_b64 : null,
    );
    const raw = apiResponse.choices?.[0]?.message?.content || '';
    const result = parseVerdictText(raw, provider_id, modelInfo.displayName);
    result.elapsed_ms = Date.now() - start;
    return result;
  } catch (e) {
    return { provider_id, provider_name: modelInfo.displayName, error: e.message, cost_usd: 0, elapsed_ms: Date.now() - start };
  }
});

// Log aggregate multi-model ask session for history (cost_usd=0 since Haiku already logged its cost)
ipcMain.handle('db:logAskMulti', (_e, { input_text, verdicts }) => {
  try {
    logLLMCall(openWriteDb(), {
      model: 'multi',
      inputTokens: 0,
      outputTokens: 0,
      inputText: input_text,
      outputJson: JSON.stringify({ verdicts }),
      feature: 'ask_multi',
    });
  } catch (e) {
    console.error('[logAskMulti] failed:', e.message);
  }
  return { ok: true };
});

ipcMain.handle('db:askMultiModel', async (_e, { trade_call, provider_ids, platform = 'pc', image_b64 = null }) => {
  if (!provider_ids || provider_ids.length === 0) {
    return { error: 'Select at least one model' };
  }
  const context = buildAskContext(openDb(), trade_call, platform);
  const userMessage = formatUserMessage(context, trade_call);

  const tasks = (provider_ids || []).map(async (providerId) => {
    if (providerId === 'haiku') {
      const apiKey = getAnthropicKey();
      if (!apiKey) return { provider_id: 'haiku', provider_name: 'Claude Haiku', error: 'ANTHROPIC_API_KEY not set', cost_usd: 0 };
      try {
        const cfg = readLLMConfig();
        const wDb = openWriteDb();
        checkDailyCap(wDb, cfg.daily_cap_usd);
        const apiResponse = await callAnthropic(apiKey, userMessage);
        const raw = apiResponse.content[0].text;
        const result = parseVerdictText(raw, 'haiku', 'Claude Haiku');
        const inputTokens = apiResponse.usage?.input_tokens || 0;
        const outputTokens = apiResponse.usage?.output_tokens || 0;
        result.cost_usd = inputTokens * 0.00000025 + outputTokens * 0.00000125;
        logLLMCall(wDb, {
          model: apiResponse.model || 'claude-haiku-4-5-20251001',
          inputTokens, outputTokens,
          inputText: trade_call,
          outputJson: JSON.stringify({ verdict: result.action, confidence: result.confidence, reasoning: result.reasoning }),
          feature: 'ask',
        });
        return result;
      } catch (e) {
        return { provider_id: 'haiku', provider_name: 'Claude Haiku', error: e.message, cost_usd: 0 };
      }
    }

    const modelInfo = NVIDIA_MODELS[providerId];
    if (!modelInfo) return { provider_id: providerId, provider_name: providerId, error: `Unknown provider: ${providerId}`, cost_usd: 0 };

    const nvidiaKey = getNvidiaKey();
    if (!nvidiaKey) return { provider_id: providerId, provider_name: modelInfo.displayName, error: 'NVIDIA_API_KEY not set', cost_usd: 0 };

    try {
      const apiResponse = await callNvidiaModel(
        nvidiaKey,
        modelInfo.modelId,
        userMessage,
        providerId === 'mistral-vision' ? image_b64 : null,
      );
      const raw = apiResponse.choices?.[0]?.message?.content || '';
      return parseVerdictText(raw, providerId, modelInfo.displayName);
    } catch (e) {
      return { provider_id: providerId, provider_name: modelInfo.displayName, error: e.message, cost_usd: 0 };
    }
  });

  const verdicts = await Promise.all(tasks);
  return {
    verdicts,
    context_used: {
      cards: context.mentionedCards.map(c => c.player_name),
      signals_count: context.recentSignals.length,
    },
  };
});

// ---------------------------------------------------------------------------
// Backend process management
// ---------------------------------------------------------------------------
let backendProc = null;
let discordProc = null;
let twitterProc = null;

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

function startTwitterIngest() {
  const settings = readSettings();
  const enabled = settings.enableTwitterIngest !== false;  // default true
  if (!enabled) {
    console.log('[main] enableTwitterIngest=false — skipping twitter ingest spawn');
    return;
  }
  const cookiePath = path.join(PROJECT_ROOT, 'data', '.cookies', 'x_cookies.txt');
  if (!fs.existsSync(cookiePath)) {
    console.log('[twitter] Cookie file not found at', cookiePath, '— skipping Twitter worker');
    return;
  }
  const uvExe = resolveUv();
  const backendDir = path.join(PROJECT_ROOT, 'backend');
  console.log('[twitter] Starting Twitter ingest worker with:', uvExe);
  twitterProc = spawn(uvExe, ['run', 'python', '-m', 'src.workers.twitter_ingest'], {
    cwd: backendDir,
    stdio: 'pipe',
    windowsHide: true,
    shell: false,
  });
  twitterProc.stdout.on('data', d => process.stdout.write('[twitter] ' + d));
  twitterProc.stderr.on('data', d => process.stderr.write('[twitter] ' + d));
  twitterProc.on('exit', code => console.log('[twitter] exited', code));
}

function stopTwitterIngest() {
  if (!twitterProc) return;
  try {
    if (process.platform === 'win32') {
      execSync(`taskkill /F /T /PID ${twitterProc.pid}`, { stdio: 'ignore' });
    } else {
      twitterProc.kill('SIGTERM');
    }
  } catch {}
  twitterProc = null;
}

// ---------------------------------------------------------------------------
// IPC handlers — DB queries (main process owns DB, preload just relays)
// ---------------------------------------------------------------------------
ipcMain.handle('db:getTopMovers', (_e, opts) => getTopMovers(openDb(), opts));
ipcMain.handle('db:searchCards',  (_e, opts) => searchCards(openDb(), opts));
ipcMain.handle('db:getCardDetail', (_e, opts) => getCardDetail(openDb(), opts));
ipcMain.handle('db:getScraperHealth', (_e, opts) => getScraperHealth(openDb(), opts));
ipcMain.handle('db:getRecentSignals', (_e, opts) => getRecentSignals(openDb(), opts));
ipcMain.handle('db:getFodderSummary',   (_e, opts) => getFodderSummary(openDb(), opts));
ipcMain.handle('db:getFodderSnapshot',  (_e, opts) => getFodderSnapshot(openDb(), opts));
ipcMain.handle('db:getFodderByRating',  (_e, opts) => getFodderByRating(openDb(), opts));
ipcMain.handle('db:getFodderHistory',   (_e, opts) => getFodderHistory(openDb(), opts));
ipcMain.handle('db:getLLMHistory', (_e, opts) => getLLMHistory(openWriteDb(), opts));
ipcMain.handle('db:getRecommendations',           (_e, opts) => getRecommendations(openDb(), opts));
ipcMain.handle('db:dismissRecommendation',        (_e, opts) => dismissRecommendation(openWriteDb(), opts));
ipcMain.handle('db:getRecommendationStats',       (_e, opts) => getRecommendationStats(openDb(), opts));
ipcMain.handle('db:getRecommendationBudgetStatus', ()        => getRecommendationBudgetStatus(openDb()));

ipcMain.handle('db:triggerRecommendations', async (_e, { platform, provider_id } = {}) => {
  try {
    const body = JSON.stringify({ platform: platform || 'pc', provider_id: provider_id || 'haiku' });
    const res = await fetch('http://127.0.0.1:8765/run-recommendations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    if (!res.ok) return { error: `HTTP ${res.status}` };
    return await res.json();
  } catch (e) {
    return { error: e.message };
  }
});

ipcMain.handle('db:askLLM', async (_e, { text, platform }) => {
  const apiKey = getAnthropicKey();
  if (!apiKey) {
    return { error: 'ANTHROPIC_API_KEY not found in .env or environment.' };
  }
  const cfg = readLLMConfig();
  try {
    const wDb = openWriteDb();
    checkDailyCap(wDb, cfg.daily_cap_usd);
    const context = buildAskContext(openDb(), text, platform);
    const userMessage = formatUserMessage(context, text);
    const apiResponse = await callAnthropic(apiKey, userMessage);
    let rawText = apiResponse.content[0].text.trim();
    // Strip markdown code fences if model adds them despite prompt instructions
    if (rawText.startsWith('```')) {
      const lines = rawText.split('\n');
      const start = lines[0].startsWith('```') ? 1 : 0;
      const end = lines[lines.length - 1].trim() === '```' ? lines.length - 1 : lines.length;
      rawText = lines.slice(start, end).join('\n').trim();
    }
    let verdict;
    try {
      verdict = JSON.parse(rawText);
    } catch {
      return { error: `LLM returned non-JSON: ${rawText.slice(0, 200)}` };
    }
    const model = apiResponse.model || 'claude-haiku-4-5-20251001';
    const inputTokens = apiResponse.usage?.input_tokens || 0;
    const outputTokens = apiResponse.usage?.output_tokens || 0;
    logLLMCall(wDb, {
      model, inputTokens, outputTokens,
      inputText: text,
      outputJson: JSON.stringify(verdict),
    });
    return {
      verdict,
      context_used: {
        cards: context.mentionedCards.map(c => c.player_name),
        fodder_ratings: context.fodderContext.map(f => f.rating),
        signals_count: context.recentSignals.length,
      },
      usage: { model, input_tokens: inputTokens, output_tokens: outputTokens },
    };
  } catch (e) {
    return { error: e.message };
  }
});

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
if (isSelfTest) {
  app.whenReady().then(() => {
    try {
      const db = openDb();

      const topMovers    = getTopMovers(db,     { platform: 'pc', hoursBack: 24, limit: 5 });
      const cards        = searchCards(db,      { query: 'Mbappe' });
      const cardDetail   = getCardDetail(db,    { cardKey: 'mbappe-toty-fc26', platform: 'pc' });
      const health       = getScraperHealth(db, {});
      const signals      = getRecentSignals(db, { hoursBack: 168, limit: 10 });
      const fodderSum    = getFodderSummary(db, { platform: 'pc' });
      const fodderSnap   = getFodderSnapshot(db, { rating: 85, platform: 'pc', hoursBack: 168 });
      const fodderCards  = getFodderByRating(db, { rating: 85, platform: 'pc', limit: 10 });
      const fodderHist   = getFodderHistory(db,  { rating: 85, platform: 'pc', hoursBack: 168 });
      const llmHistory   = getLLMHistory(openWriteDb(), { limit: 5 });
      const recsList     = getRecommendations(openDb(), { platform: 'pc', limit: 10, activeOnly: true });
      const recsStats    = getRecommendationStats(openDb(), { days: 7 });
      const recsBudget   = getRecommendationBudgetStatus(openDb());
      const providerAvail = { haiku: !!getAnthropicKey(), nvidia: !!(getNvidiaKey()?.startsWith('nvapi-')) };
      const ipcHandlers = [
        'db:getTopMovers',
        'db:searchCards',
        'db:getCardDetail',
        'db:getScraperHealth',
        'db:getRecentSignals',
        'db:getFodderSummary',
        'db:getFodderSnapshot',
        'db:getFodderByRating',
        'db:getFodderHistory',
        'db:getLLMHistory',
        'db:getRecommendations',
        'db:dismissRecommendation',
        'db:getRecommendationStats',
        'db:getRecommendationBudgetStatus',
        'db:triggerRecommendations',
        'db:askLLM',
        'db:askMultiModel',
        'db:getProviderAvailability',
        'db:buildAskContext',
        'db:callSingleProvider',
        'db:logAskMulti',
      ];

      const result = {
        selftest: true,
        db_path: DB_PATH,
        ipc_handlers_registered: ipcHandlers,
        handlers: {
          getTopMovers:    { platform: 'pc', count: topMovers.length,  rows: topMovers },
          searchCards:     { query: 'Mbappe', count: cards.length,     rows: cards },
          getCardDetail:   { card_key: 'mbappe-toty-fc26', snapshots: cardDetail?.snapshots?.length ?? 0, attrs: cardDetail?.attrs?.length ?? 0 },
          getScraperHealth:{ count: health.length, rows: health },
          getRecentSignals:{ count: signals.length, rows: signals },
          getFodderSummary: { platform: 'pc', count: fodderSum.length },
          getFodderSnapshot:{ rating: 85, count: fodderSnap.length },
          getFodderByRating:{ rating: 85, count: fodderCards.length },
          getFodderHistory: { rating: 85, count: fodderHist.length },
          getLLMHistory:          { count: llmHistory.length },
          getRecommendations:           { count: recsList.length, rows: recsList },
          getRecommendationStats:       recsStats,
          getRecommendationBudgetStatus: recsBudget,
          getProviderAvailability:       providerAvail,
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
  startTwitterIngest();
  createWindow();
});

app.on('window-all-closed', () => {
  stopBackend();
  stopDiscordIngest();
  stopTwitterIngest();
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
