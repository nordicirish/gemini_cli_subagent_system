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
        local_storage_state: { mutable_state: { portfolio_snapshot: [{ ticker: 'NVDA', shares: 10, wac: 100 }], watched_tickers: ['AMD', 'PLTR', 'TSLA'] } },
        tickers: [
            {
                ticker: 'NVDA', price: 120.5, session_change_pct: 1.2, gap_percent: 0.1, atr_percent: 2.5, rsi: 55, vwap: 119,
                volatility_regime: 'ELEVATED', vix_price: 24.5,
                fib_forecast: {
                    mode: 'EXTENSION', pA: 118.0, pB: 122.0, pC: 119.5, impulse: 4.0,
                    t1_100: 123.5, t2_1618: 125.97, t3_2618: 129.97,
                    next_resistance: 122.0, next_resistance_label: 'Session HOD', distance_to_next_pct: 1.24,
                    daily_peak_target: 122.0, daily_peak_atr: 123.0, daily_peak_status: 'EXPANDING', atr_confluence: true,
                },
            },
            {
                ticker: 'SPY', price: 718.68, session_change_pct: -0.5, gap_percent: -0.2, atr_percent: 1.1, rsi: 48, vwap: 719.0, net_gex_total: -0.013,
            },
            // Warm-up row: no numbers yet. Used to throw in renderRow and blank the whole table.
            { ticker: 'AMD', price: null, session_change_pct: null, gap_percent: null, atr_percent: null, rsi: null },
            {
                ticker: 'PLTR', price: 140.0, session_change_pct: -2.1, gap_percent: -0.5, atr_percent: 3.1, rsi: 41, vwap: 141.5, volume: 2500000, rvol: 0.62,
                fib_forecast: {
                    mode: 'RETRACEMENT', pA: 138.0, pB: 144.0, pC: 138.0, impulse: 6.0,
                    t1_100: 144.0, t2_1618: 147.71, t3_2618: 153.71,
                    next_resistance: 141.0, next_resistance_label: '0.500 Retracement', distance_to_next_pct: 0.71,
                    daily_peak_target: 144.0, daily_peak_atr: 145.0, daily_peak_status: 'PULLBACK', atr_confluence: false,
                },
            },
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
    let scoutCategories = [];
    window.fetch = async (url, opts = {}) => {
        const pathOnly = String(url).split('?')[0];
        const method = (opts.method || 'GET').toUpperCase();
        calls.push(`${method} ${pathOnly}`);
        if (pathOnly === '/api/scout_categories') {
            if (method === 'POST') {
                scoutCategories = JSON.parse(opts.body || '[]');
                return { ok: true, status: 200, json: async () => ({ status: 'success', categories: [...scoutCategories] }) };
            }
            return { ok: true, status: 200, json: async () => [...scoutCategories] };
        }
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
        assert.deepStrictEqual(symbols, ['NVDA', 'AMD', 'PLTR', 'TSLA']);
        const amd = rows.find((r) => r.cells[0].textContent.trim() === 'AMD');
        assert.strictEqual(amd.cells[1].textContent.trim(), '—', 'null price should render as —');
        assert(rows.find((r) => r.cells[0].textContent.trim() === 'TSLA').textContent.includes('Pending scan'));
    });
    check('Fib Target UI separates Extension vs Retracement without dual Peak ambiguity', () => {
        const rows = [...window.document.querySelectorAll('#data-table tbody tr')];
        const nvda = rows.find((r) => r.cells[0].textContent.trim() === 'NVDA');
        const pltr = rows.find((r) => r.cells[0].textContent.trim() === 'PLTR');
        assert(nvda && pltr, 'NVDA and PLTR rows missing');
        const nvdaFib = nvda.cells[11];
        assert(nvdaFib.textContent.includes('HOD $122.00'), `expected HOD tag, got: ${nvdaFib.textContent}`);
        assert(nvdaFib.textContent.includes('Target Cap: $122.00'), `expected Target Cap subtitle, got: ${nvdaFib.textContent}`);
        assert(!nvdaFib.textContent.includes('Daily Peak:'), 'should not render contradictory Daily Peak label');
        const pltrFib = pltr.cells[11];
        assert(pltrFib.querySelector('.fib-target-tag.retracement'), 'expected retracement tag class');
        assert(pltrFib.textContent.includes('0.50 $141.00'), `expected retracement prefix, got: ${pltrFib.textContent}`);
        assert(pltrFib.textContent.includes('HOD Cap: $144.00'), `expected HOD Cap subtitle, got: ${pltrFib.textContent}`);
        assert(pltrFib.textContent.includes('Pullback'), 'expected Pullback status pill');
    });
    check('chat boots and market table polls', () => {
        assert(calls.includes('POST /api/chat'), 'boot chat turn not sent');
        assert(calls.includes('GET /api/data'), 'market data not polled');
    });
    check('Package A: regime pill, ticker-first macro HUD, and collapsible tools sidebar', () => {
        const pill = window.document.getElementById('volatility-regime-pill');
        assert(pill, 'volatility-regime-pill missing');
        assert.strictEqual(pill.textContent.trim(), 'ELEVATED');
        assert(pill.classList.contains('regime-elevated'), 'expected regime-elevated class');

        const macroTicker = window.document.querySelector('#macro-card-SPY .macro-ticker');
        const macroSubtitle = window.document.querySelector('#macro-card-SPY .macro-subtitle');
        assert(macroTicker && macroTicker.textContent.trim() === 'SPY', 'macro card ticker header missing');
        assert(macroSubtitle && macroSubtitle.textContent.trim() === 'S&P 500', 'macro card subtitle missing');

        const main = window.document.getElementById('main-content');
        const btn = window.document.getElementById('toggle-sidebar-btn');
        assert(main && main.classList.contains('sidebar-collapsed'), 'sidebar should default to collapsed');
        assert(btn && btn.getAttribute('aria-expanded') === 'false', 'aria-expanded should start false');
        btn.click();
        assert(!main.classList.contains('sidebar-collapsed'), 'clicking TOOLS should expand sidebar');
        assert.strictEqual(btn.getAttribute('aria-expanded'), 'true');
        btn.click();
        assert(main.classList.contains('sidebar-collapsed'), 'clicking TOOLS again should collapse sidebar');
    });
    check('Chat Structure: hoists verdict, Alerts & Recommendations above prose and collapses Source Index', () => {
        const sampleReply = [
            '### 🏁 Final Council Decision',
            'HOLD',
            '',
            '### 💡 AI Overview',
            'Broad indices remain choppy under negative dealer gamma.',
            '',
            '### 🚨 Alerts & Risk Warnings',
            '- [CRITICAL] VIX elevated above 25; tighten trailing VWAP stops.',
            '',
            '### 🎯 Actionable Recommendations',
            '- **[TRIM] NVDA** — Trim 25% into resistance near $125.00.',
            '- **[HOLD] AMD** — Wait for rVol > 1.5x confirmation.',
            '',
            '### 📚 Source Index',
            '- [SEC EDGAR](https://www.sec.gov/)',
        ].join('\n');
        window.chatUI.appendMessage('ai', sampleReply, false, 0, null, 'gemini-4-hydrogen');
        const aiMsgs = [...window.document.querySelectorAll('.modern-message.ai')];
        const msg = aiMsgs[aiMsgs.length - 1];
        assert(msg.firstElementChild && msg.firstElementChild.classList.contains('chat-top-brief'), '.chat-top-brief should be prepended first');
        assert.strictEqual(msg.querySelector('.verdict-pill').textContent.trim(), 'VERDICT: HOLD');
        assert(msg.querySelector('.chat-callout-alert').textContent.includes('VIX elevated'));
        const pills = [...msg.querySelectorAll('.chat-callout-reco .action-pill')].map((p) => p.textContent.trim());
        assert.deepStrictEqual(pills, ['TRIM', 'HOLD']);
        const srcDetails = msg.querySelector('details.chat-sources-details');
        assert(srcDetails && srcDetails.open === false, 'Source Index should auto-collapse');
    });

    const techBtnBefore = window.document.querySelector('#scout-categories-container button[data-sector="Technology"]');
    const initialPressed = techBtnBefore ? techBtnBefore.getAttribute('aria-pressed') : null;
    if (techBtnBefore) techBtnBefore.click();
    await new Promise((r) => setTimeout(r, 60));
    const techBtnAfterOn = window.document.querySelector('#scout-categories-container button[data-sector="Technology"]');
    const pressedAfterOn = techBtnAfterOn ? techBtnAfterOn.getAttribute('aria-pressed') : null;
    const activeClassAfterOn = techBtnAfterOn ? techBtnAfterOn.classList.contains('active') : false;
    if (techBtnAfterOn) techBtnAfterOn.click();
    await new Promise((r) => setTimeout(r, 60));
    const techBtnAfterOff = window.document.querySelector('#scout-categories-container button[data-sector="Technology"]');
    const pressedAfterOff = techBtnAfterOff ? techBtnAfterOff.getAttribute('aria-pressed') : null;
    const activeClassAfterOff = techBtnAfterOff ? techBtnAfterOff.classList.contains('active') : true;
    const postScoutCalls = calls.filter((c) => c === 'POST /api/scout_categories').length;
    check('Sector Discovery: clicking a sector button toggles it on and off', () => {
        assert(techBtnBefore, 'Technology sector button missing');
        assert.strictEqual(initialPressed, 'false');
        assert.strictEqual(pressedAfterOn, 'true', 'clicking sector button should toggle it active');
        assert.strictEqual(activeClassAfterOn, true);
        assert.strictEqual(pressedAfterOff, 'false', 'clicking active sector button should toggle it off');
        assert.strictEqual(activeClassAfterOff, false);
        assert.strictEqual(postScoutCalls, 2, `expected 2 POST /api/scout_categories calls, got ${postScoutCalls}`);
    });

    window.close();
    console.log(results.join('\n'));
    const failed = results.filter((r) => r.startsWith('FAIL')).length;
    console.log(failed ? `${failed} FAILED` : 'ALL PASSED');
    if (errors.length) console.log('Captured errors:\n' + errors.join('\n'));
    process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
