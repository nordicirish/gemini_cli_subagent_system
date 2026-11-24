// Dashboard load test: runs static/index.html with portfolio.js, app.js and modern_ui.js as real
// classic scripts (shared global scope, page order) in jsdom against a mocked API. Catches
// cross-file problems a per-file syntax check cannot: duplicate top-level declarations, load-order
// bugs, and one file silently overriding another's functions.
// Run: npm install && node tests/js/dashboard_load.test.js
const path = require('path');
const fs = require('fs');
const assert = require('assert');
const { JSDOM, VirtualConsole } = require('jsdom');

const staticDir = process.argv[2] || path.join(__dirname, '..', '..', 'static');
const read = (rel) => fs.readFileSync(rel, 'utf8');
const nodeModules = path.join(__dirname, '..', '..', 'node_modules');

const API = {
    '/api/tickers': { tickers: ['NVDA'], macro: ['SPY'], macro_labels: { SPY: 'S&P 500' } },
    '/api/basket': {
        portfolio: [{ ticker: 'NVDA', shares: 10, wac: 100.0 }],
        unallocated_cash_eur: 1000.0, unallocated_cash_usd: 1097.7,
        eurusd_rate: 1.0977, eurusd_rate_as_of: '2026-11-24T16:00:00Z',
    },
    '/api/watchlist': ['AMD'],
    '/api/scout_categories': ['AI_INFRA'],
    '/api/scout_sectors': [],
    '/api/scout_config': { limit: 5, max_rsi: 70 },
    '/api/data': {
        status: 'OPEN',
        timestamp: '2026-11-24T16:00:00Z',
        // Watchlist comes from the SSoT (the only place /api/data publishes it)
        local_storage_state: { mutable_state: { portfolio_snapshot: [{ ticker: 'NVDA', shares: 10, wac: 100 }], watched_tickers: ['AMD', 'TSLA'] } },
        tickers: [
            { ticker: 'NVDA', price: 120.5, session_change_pct: 1.2, gap_percent: 0.1, atr_percent: 2.5, rsi: 55, vwap: 119 },
            // Warm-up row: no numbers yet. Used to throw in renderRow and blank the whole table.
            { ticker: 'AMD', price: null, session_change_pct: null, gap_percent: null, atr_percent: null, rsi: null },
            // TSLA is on the watchlist but not scanned yet: must still get a row.
        ],
    },
    '/api/proposals': { status: 'success', proposals: [] },
    '/api/list_models': { status: 'success', models: [], current_model: 'test-model' },
    '/api/chat': { status: 'success', response: 'Boot complete.', proposals: [], turn_id: 't1' },
};

async function main() {
    const errors = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError', (e) => errors.push(`jsdomError: ${e.message}`));
    virtualConsole.on('error', (...args) => errors.push(`console.error: ${args.map(String).join(' ')}`));

    // Page markup without its <script> tags; scripts are re-added below in the same order.
    const html = read(path.join(staticDir, 'index.html')).replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
    const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole, url: 'http://127.0.0.1:8000/' });
    const { window } = dom;
    window.addEventListener('error', (e) => errors.push(`uncaught: ${e.message}`));
    window.addEventListener('unhandledrejection', (e) => errors.push(`unhandled rejection: ${e.reason}`));

    const calls = [];
    window.fetch = async (url, opts = {}) => {
        const pathOnly = String(url).split('?')[0];
        calls.push(`${(opts.method || 'GET').toUpperCase()} ${pathOnly}`);
        const prompt = pathOnly.startsWith('/api/prompts/') ? { prompt: '' } : undefined;
        const body = API[pathOnly] ?? prompt ?? {};
        return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(body)) };
    };
    window.HTMLCanvasElement.prototype.getContext = () => null;
    window.LightweightCharts = { createChart: () => ({}) };  // chart library stub (not exercised)
    window.EventSource = class { constructor() { this.onmessage = null; } close() {} };  // jsdom has no SSE

    const addScript = (code) => {
        const s = window.document.createElement('script');
        s.textContent = code;
        window.document.body.appendChild(s);
    };
    addScript(read(path.join(nodeModules, 'marked', 'marked.min.js')));
    addScript(read(path.join(nodeModules, 'dompurify', 'dist', 'purify.min.js')));
    for (const file of ['portfolio.js', 'app.js', 'modern_ui.js']) addScript(read(path.join(staticDir, file)));
    window.dispatchEvent(new window.Event('load'));
    await new Promise((r) => setTimeout(r, 500));

    const results = [];
    const check = (name, fn) => {
        try { fn(); results.push(`PASS ${name}`); } catch (e) { results.push(`FAIL ${name} - ${e.message}`); }
    };

    check('all three scripts load without errors', () => assert.deepStrictEqual(errors, []));
    check('one portfolio implementation: window.fetchPortfolio comes from portfolio.js', () => {
        assert(window.PortfolioPanel, 'PortfolioPanel missing');
        assert(String(window.fetchPortfolio).includes('PortfolioPanel.fetchPortfolio'));
        assert(String(window.renderPortfolio).includes('PortfolioPanel.renderPortfolio'));
    });
    check('chat controller holds no portfolio code', () => {
        const chat = window.chatUI;
        assert(chat, 'chatUI missing');
        for (const key of ['fetchPortfolio', 'renderPortfolio', 'markPortfolioDegraded', 'clearPortfolioDegraded', 'lastValidPortfolioBasket']) {
            assert(!(key in chat), `chatUI still has ${key}`);
        }
    });
    check('portfolio panel renders the basket with live-FX cash', () => {
        const body = window.document.getElementById('portfolio-manager-body');
        assert(body && body.innerHTML.includes('NVDA'), 'NVDA row missing');
        assert.strictEqual(window.document.getElementById('cash-val-usd').textContent.trim(), '$1097.70');
        assert.strictEqual(window.PortfolioPanel.portfolioDegraded, false);
    });
    // app.js's polling must reach the single portfolio.js implementation (no shadow copy)
    let panelFetches = 0;
    let pollError = null;
    const basketBefore = calls.filter((c) => c === 'GET /api/basket').length;
    try {
        const realFetch = window.PortfolioPanel.fetchPortfolio;
        window.PortfolioPanel.fetchPortfolio = function (...args) { panelFetches += 1; return realFetch.apply(this, args); };
        await window.eval('pollData()');
        await new Promise((r) => setTimeout(r, 100));
    } catch (e) {
        pollError = e;
    }
    const basketAfter = calls.filter((c) => c === 'GET /api/basket').length;
    check('app.js polling goes through PortfolioPanel', () => {
        assert.strictEqual(pollError, null, `polling failed: ${pollError}`);
        assert.strictEqual(panelFetches, 1, `PortfolioPanel.fetchPortfolio called ${panelFetches}x`);
        assert.strictEqual(basketAfter - basketBefore, 1, 'expected exactly one basket request per poll');
    });
    check('market table shows portfolio and watchlist sections, tolerating missing numbers', () => {
        const rows = [...window.document.querySelectorAll('#data-table tbody tr')];
        const sections = rows.filter((r) => r.querySelector('td[colspan="14"]')).map((r) => r.textContent.trim());
        assert.deepStrictEqual(sections, ['Your Portfolio', 'Strategic Watchlist']);
        const symbols = rows.filter((r) => !r.querySelector('td[colspan="14"]')).map((r) => r.cells[0].textContent.trim());
        assert.deepStrictEqual(symbols, ['NVDA', 'AMD', 'TSLA']);
        const amd = rows.find((r) => r.cells[0].textContent.trim() === 'AMD');
        assert.strictEqual(amd.cells[1].textContent.trim(), '—', 'null price should render as —');
        assert(rows.find((r) => r.cells[0].textContent.trim() === 'TSLA').textContent.includes('Pending scan'));
    });
    check('chat boots and market table polls', () => {
        assert(calls.includes('POST /api/chat'), 'boot chat turn not sent');
        assert(calls.includes('GET /api/data'), 'market data not polled');
    });

    window.close();
    console.log(results.join('\n'));
    const failed = results.filter((r) => r.startsWith('FAIL')).length;
    console.log(failed ? `${failed} FAILED` : 'ALL PASSED');
    if (errors.length) console.log('Captured errors:\n' + errors.join('\n'));
    process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
