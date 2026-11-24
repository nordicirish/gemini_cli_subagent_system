#!/usr/bin/env node
/**
 * Zero-Dependency Headless Dashboard UI Verifier — GEM (gemini_cli_subagent_system).
 *
 * Drives locally installed Microsoft Edge or Google Chrome via Chrome DevTools Protocol
 * (Node 24 built-in fetch + WebSocket) against a running GEM server (default http://127.0.0.1:8000)
 * to capture screenshots and verify:
 *   - HTTP document status < 400
 *   - Zero uncaught runtime exceptions or error console logs
 *   - Zero failed same-origin asset / API requests
 *   - Zero broken <img> elements
 *   - Zero unintended horizontal document overflow (scrollWidth <= innerWidth + 1)
 *
 * Usage:
 *   node scripts/verify_dashboard_ui.js
 *   node scripts/verify_dashboard_ui.js --url http://127.0.0.1:8000 --widths 1280,1440
 *   node scripts/verify_dashboard_ui.js --allow-fallback
 */
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');

const DEFAULT_URL = 'http://127.0.0.1:8000';
const DEFAULT_WIDTHS = [1280, 1440];
const DEFAULT_OUT = '.agents/state/screenshots';

const BROWSER_CANDIDATES = {
  win32: {
    msedge: [
      'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
      'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    ],
    chrome: [
      'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
      'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
      path.join(process.env.LOCALAPPDATA || '', 'Google\\Chrome\\Application\\chrome.exe'),
    ],
  },
  darwin: {
    msedge: ['/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'],
    chrome: ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'],
  },
  linux: {
    msedge: ['/usr/bin/microsoft-edge', '/usr/bin/microsoft-edge-stable'],
    chrome: ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'],
  },
};

function findRepoRoot(startDir = __dirname) {
  let current = path.resolve(startDir);
  while (current) {
    if (fs.existsSync(path.join(current, 'package.json')) || fs.existsSync(path.join(current, '.agents', 'AGENTS.md'))) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return path.resolve(__dirname, '..');
}

function parseArgs(argv) {
  const opts = {
    url: DEFAULT_URL,
    widths: [...DEFAULT_WIDTHS],
    out: DEFAULT_OUT,
    browser: null,
    allowFallback: false,
    settleMs: 1200,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      i += 1;
      if (i >= argv.length) throw new Error(`${arg} requires a value`);
      return argv[i];
    };
    if (arg === '--url') {
      opts.url = next();
      const parsed = new URL(opts.url);
      if (!['http:', 'https:'].includes(parsed.protocol)) {
        throw new Error(`Unsupported protocol for --url: ${parsed.protocol}`);
      }
    } else if (arg === '--widths') {
      opts.widths = next().split(',').map((w) => Number(w.trim()));
      if (!opts.widths.length || opts.widths.some((w) => !Number.isInteger(w) || w < 320 || w > 3840)) {
        throw new Error('--widths must be comma-separated integers between 320 and 3840');
      }
    } else if (arg === '--out') {
      opts.out = next();
    } else if (arg === '--browser') {
      const b = next().toLowerCase();
      if (!['msedge', 'chrome'].includes(b)) {
        throw new Error('--browser must be msedge or chrome');
      }
      opts.browser = b;
    } else if (arg === '--settle-ms') {
      opts.settleMs = Number(next());
      if (!Number.isFinite(opts.settleMs) || opts.settleMs < 0 || opts.settleMs > 15000) {
        throw new Error('--settle-ms must be between 0 and 15000');
      }
    } else if (arg === '--allow-fallback') {
      opts.allowFallback = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return opts;
}

function findBrowserBinary(preferred = null, platform = process.platform) {
  const table = BROWSER_CANDIDATES[platform] || BROWSER_CANDIDATES.linux;
  const order = preferred ? [preferred] : ['msedge', 'chrome'];
  for (const kind of order) {
    for (const candidate of table[kind] || []) {
      if (candidate && fs.existsSync(candidate)) {
        return { kind, path: candidate };
      }
    }
  }
  return null;
}

function evaluatePageMetrics(metrics, { maxOverflowPx = 1 } = {}) {
  const issues = [];
  if (typeof metrics.httpStatus === 'number' && metrics.httpStatus >= 400) {
    issues.push(`Main document returned HTTP ${metrics.httpStatus}`);
  }
  if ((metrics.scrollWidth || 0) > (metrics.innerWidth || 0) + maxOverflowPx) {
    issues.push(`Horizontal overflow: scrollWidth=${metrics.scrollWidth} > innerWidth=${metrics.innerWidth}`);
  }
  if ((metrics.brokenImages || 0) > 0) {
    issues.push(`Detected ${metrics.brokenImages} broken <img> element(s)`);
  }
  if ((metrics.consoleErrors || []).length > 0) {
    issues.push(`Console/runtime errors: ${metrics.consoleErrors.slice(0, 3).join(' | ')}`);
  }
  if ((metrics.failedSameOriginRequests || []).length > 0) {
    issues.push(`Failed same-origin requests: ${metrics.failedSameOriginRequests.slice(0, 3).join(' | ')}`);
  }
  return { ok: issues.length === 0, issues };
}

function buildReport({ mode, url, browser = null, results = [], fallback = null }) {
  const allOk = mode === 'screenshot'
    ? results.length > 0 && results.every((r) => r.ok)
    : Boolean(fallback && fallback.ok);
  return {
    schemaVersion: 1,
    timestamp: new Date().toISOString(),
    mode,
    url,
    browser,
    ok: allOk,
    results,
    fallback,
  };
}

class CdpClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    this.openPromise = new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve, { once: true });
      this.ws.addEventListener('error', (err) => reject(err.error || new Error('CDP WebSocket error')), { once: true });
    });
    this.ws.addEventListener('message', (event) => {
      const msg = JSON.parse(String(event.data));
      if (msg.id !== undefined) {
        const entry = this.pending.get(msg.id);
        if (!entry) return;
        this.pending.delete(msg.id);
        if (msg.error) entry.reject(new Error(`${msg.error.message || 'CDP error'} (${msg.error.code})`));
        else entry.resolve(msg.result || {});
      } else if (msg.method) {
        const cbs = this.listeners.get(msg.method) || [];
        for (const cb of cbs) cb(msg.params || {});
      }
    });
  }

  async connect() {
    await this.openPromise;
  }

  on(method, cb) {
    const list = this.listeners.get(method) || [];
    list.push(cb);
    this.listeners.set(method, list);
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  waitForEvent(method, timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Timed out waiting for ${method}`)), timeoutMs);
      const handler = (params) => {
        clearTimeout(timer);
        const list = (this.listeners.get(method) || []).filter((fn) => fn !== handler);
        this.listeners.set(method, list);
        resolve(params);
      };
      this.on(method, handler);
    });
  }

  close() {
    for (const { reject } of this.pending.values()) {
      reject(new Error('CDP client closed'));
    }
    this.pending.clear();
    try {
      this.ws.close();
    } catch (_) {}
  }
}

async function launchBrowserAndGetPort(binPath, userDataDir) {
  const proc = spawn(
    binPath,
    [
      '--headless=new',
      '--disable-gpu',
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-background-networking',
      '--remote-debugging-port=0',
      `--user-data-dir=${userDataDir}`,
      'about:blank',
    ],
    { stdio: ['ignore', 'ignore', 'pipe'] }
  );

  const port = await new Promise((resolve, reject) => {
    let stderrBuf = '';
    const timer = setTimeout(() => reject(new Error(`Browser failed to expose DevTools port: ${stderrBuf}`)), 15000);
    proc.stderr.on('data', (chunk) => {
      stderrBuf += chunk.toString('utf8');
      const m = stderrBuf.match(/DevTools listening on ws:\/\/127\.0\.0\.1:(\d+)/);
      if (m) {
        clearTimeout(timer);
        resolve(Number(m[1]));
      }
    });
    proc.once('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`Browser exited prematurely with code ${code}: ${stderrBuf}`));
    });
  });

  return { proc, port };
}

async function runLiveScreenshotVerification(opts, repoRoot) {
  const browser = findBrowserBinary(opts.browser);
  if (!browser) {
    throw new Error('No local Microsoft Edge or Google Chrome binary found for Screenshot Mode.');
  }

  // Confirm target server is reachable before launching browser
  const ping = await fetch(opts.url, { signal: AbortSignal.timeout(4000) });
  if (!ping) throw new Error(`Unable to reach ${opts.url}`);

  const outDir = path.isAbsolute(opts.out) ? opts.out : path.join(repoRoot, opts.out);
  fs.mkdirSync(outDir, { recursive: true });
  const tmpProfile = fs.mkdtempSync(path.join(os.tmpdir(), 'gem-cdp-'));
  let proc = null;
  let client = null;

  try {
    const launched = await launchBrowserAndGetPort(browser.path, tmpProfile);
    proc = launched.proc;
    const targetsResp = await fetch(`http://127.0.0.1:${launched.port}/json/list`, { signal: AbortSignal.timeout(5000) });
    const targets = await targetsResp.json();
    const pageTarget = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
    if (!pageTarget) throw new Error('No DevTools page target available.');

    client = new CdpClient(pageTarget.webSocketDebuggerUrl);
    await client.connect();
    await client.send('Page.enable');
    await client.send('Runtime.enable');
    await client.send('Network.enable');

    const targetOrigin = new URL(opts.url).origin;
    const results = [];

    for (const width of opts.widths) {
      const consoleErrors = [];
      const failedSameOriginRequests = [];
      const requestOrigins = new Map();
      let mainHttpStatus = 200;

      const onException = (p) => {
        const desc = p.exceptionDetails?.exception?.description || p.exceptionDetails?.text || 'Uncaught exception';
        consoleErrors.push(String(desc).slice(0, 200));
      };
      const onConsole = (p) => {
        if (p.type === 'error') {
          const text = (p.args || []).map((a) => a.value ?? a.description ?? '').join(' ').trim() || 'console.error';
          consoleErrors.push(text.slice(0, 200));
        }
      };
      const onReq = (p) => {
        requestOrigins.set(p.requestId, p.request?.url || '');
      };
      const onResp = (p) => {
        const reqUrl = p.response?.url || requestOrigins.get(p.requestId) || '';
        if (p.type === 'Document' && reqUrl.startsWith(targetOrigin)) {
          mainHttpStatus = p.response?.status || 200;
        }
        if (reqUrl.startsWith(targetOrigin) && (p.response?.status || 0) >= 400) {
          failedSameOriginRequests.push(`${p.response.status} ${reqUrl}`);
        }
      };
      const onFailed = (p) => {
        const reqUrl = requestOrigins.get(p.requestId) || '';
        if (reqUrl.startsWith(targetOrigin) && !p.canceled) {
          failedSameOriginRequests.push(`${p.errorText || 'FAILED'} ${reqUrl}`);
        }
      };

      // Reset listeners per viewport width
      client.listeners.clear();
      client.on('Runtime.exceptionThrown', onException);
      client.on('Runtime.consoleAPICalled', onConsole);
      client.on('Network.requestWillBeSent', onReq);
      client.on('Network.responseReceived', onResp);
      client.on('Network.loadingFailed', onFailed);

      await client.send('Emulation.setDeviceMetricsOverride', {
        width,
        height: 900,
        deviceScaleFactor: 1,
        mobile: false,
      });

      const loaded = client.waitForEvent('Page.loadEventFired', 15000);
      await client.send('Page.navigate', { url: opts.url });
      await loaded;
      await new Promise((r) => setTimeout(r, opts.settleMs));

      const evalRes = await client.send('Runtime.evaluate', {
        returnByValue: true,
        expression: `(() => ({
          innerWidth: window.innerWidth,
          scrollWidth: document.documentElement.scrollWidth,
          brokenImages: Array.from(document.images).filter((i) => !i.complete || i.naturalWidth === 0).length,
          tableHeaders: document.querySelectorAll('#data-table thead th').length,
          tableRows: document.querySelectorAll('#table-body tr').length
        }))()`,
      });

      const shot = await client.send('Page.captureScreenshot', { format: 'png' });
      const shotRel = path.posix.join(opts.out.replace(/\\/g, '/'), `dashboard-${width}.png`);
      const shotAbs = path.join(outDir, `dashboard-${width}.png`);
      fs.writeFileSync(shotAbs, Buffer.from(shot.data, 'base64'));

      const domMetrics = evalRes.result?.value || {};
      const metrics = {
        width,
        httpStatus: mainHttpStatus,
        ...domMetrics,
        consoleErrors,
        failedSameOriginRequests,
      };
      const verdict = evaluatePageMetrics(metrics);
      results.push({
        width,
        screenshot: shotRel,
        ...metrics,
        ok: verdict.ok,
        issues: verdict.issues,
      });
    }

    const report = buildReport({
      mode: 'screenshot',
      url: opts.url,
      browser: browser.kind,
      results,
    });
    fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
    return report;
  } finally {
    if (client) client.close();
    if (proc && !proc.killed) {
      try {
        proc.kill('SIGKILL');
      } catch (_) {}
    }
    try {
      fs.rmSync(tmpProfile, { recursive: true, force: true });
    } catch (_) {}
  }
}

function runStaticFallback(repoRoot, url, outDirRel, reason) {
  const outDir = path.isAbsolute(outDirRel) ? outDirRel : path.join(repoRoot, outDirRel);
  fs.mkdirSync(outDir, { recursive: true });
  const dashTest = path.join(repoRoot, 'tests', 'js', 'dashboard_load.test.js');
  let ok = false;
  let details = reason;
  if (fs.existsSync(path.join(repoRoot, 'node_modules', 'jsdom')) && fs.existsSync(dashTest)) {
    try {
      execFileSync(process.execPath, [dashTest], { cwd: repoRoot, encoding: 'utf8', stdio: 'pipe' });
      ok = true;
      details = `${reason}; Static Fallback Mode ran dashboard_load.test.js (PASS). Live rendering, console errors & overflow remain UNVERIFIED.`;
    } catch (err) {
      ok = false;
      details = `${reason}; Static Fallback Mode failed: ${err.message}`;
    }
  }
  const report = buildReport({
    mode: 'static_fallback',
    url,
    fallback: { ok, details },
  });
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
  return report;
}

async function main(argv = process.argv.slice(2)) {
  const opts = parseArgs(argv);
  const repoRoot = findRepoRoot();
  try {
    const report = await runLiveScreenshotVerification(opts, repoRoot);
    console.log(`Dashboard UI Verification (Screenshot Mode via ${report.browser}): ${report.ok ? 'PASS' : 'FAIL'}`);
    for (const r of report.results) {
      console.log(`  - ${r.width}px: ${r.ok ? 'PASS' : 'FAIL'} (headers=${r.tableHeaders}, rows=${r.tableRows}, scrollWidth=${r.scrollWidth}/${r.innerWidth}, shot=${r.screenshot})`);
      for (const issue of r.issues) console.error(`      * ${issue}`);
    }
    return report.ok ? 0 : 1;
  } catch (err) {
    if (!opts.allowFallback) {
      console.error(`Screenshot Mode failed: ${err.message}`);
      return 1;
    }
    const report = runStaticFallback(repoRoot, opts.url, opts.out, err.message);
    console.log(`Dashboard UI Verification (Static Fallback Mode): ${report.ok ? 'PASS (UNVERIFIED LIVE)' : 'FAIL'}`);
    console.log(`  ${report.fallback.details}`);
    return report.ok ? 0 : 1;
  }
}

if (require.main === module) {
  main().then(
    (code) => process.exit(code),
    (err) => {
      console.error(err.message);
      process.exit(1);
    }
  );
}

module.exports = {
  parseArgs,
  findBrowserBinary,
  evaluatePageMetrics,
  buildReport,
  runStaticFallback,
};
