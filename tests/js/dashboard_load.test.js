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
    '/api/tickers': { tickers: ['NVDA'], macro: ['SPY', '^VIX'], macro_labels: { SPY: 'S&P 500', '^VIX': 'CBOE VIX' } },
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
            {
                ticker: '^VIX', price: 33.42, session_change_pct: 26.75, gap_percent: 6.83, atr_percent: 15.0, rsi: 64,
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
        alerts: {
            rules: [
                { rule_id: 'trg-1', ticker: 'NVDA', metric: 'price', op: 'crosses_above', target: 122.0, label: 'HOD Break', source: 'sentinel', enabled: true, trigger_count: 1 },
            ],
            unacknowledged_events: [
                { event_id: 'evt-1', rule_id: 'trg-1', ticker: 'NVDA', severity: 'warning', label: 'HOD Break', message: 'NVDA: HOD Break (price=122.10 crosses_above 122.00)' },
                { event_id: 'evt-2', rule_id: 'trg-2', ticker: 'NVDA', severity: 'info', label: 'Stale Support', message: 'NVDA: Stale Support Trigger' },
                { event_id: 'evt-3', rule_id: 'trg-3', ticker: 'PLTR', severity: 'info', label: 'Minor Bounce', message: 'PLTR: Minor Bounce' },
                { event_id: 'evt-4', rule_id: 'trg-4', ticker: 'PLTR', severity: 'critical', label: 'Stop Breach', message: 'PLTR: Stop Breach' },
            ],
            active_rules_count: 1,
            unacknowledged_count: 2,
        },
    },
    '/api/alerts': {
        rules: [
            { rule_id: 'trg-1', ticker: 'NVDA', metric: 'price', op: 'crosses_above', target: 122.0, label: 'HOD Break', source: 'sentinel', enabled: true, trigger_count: 1 },
        ],
        unacknowledged_events: [
            { event_id: 'evt-1', rule_id: 'trg-1', ticker: 'NVDA', severity: 'warning', label: 'HOD Break', message: 'NVDA: HOD Break (price=122.10 crosses_above 122.00)' },
        ],
        sentinel: { enabled: false, provider: 'gemini_free', interval_sec: 600, model_chain: ['gemini-2.5-flash-lite'], api_key_configured: true, last_run: { model_used: 'gemini-2.0-flash-lite', fallback_count: 1 } },
    },
    '/api/alert_sentinel': { enabled: false, provider: 'gemini_free', interval_sec: 600, model_chain: ['gemini-2.5-flash-lite'], api_key_configured: true, last_run: { model_used: 'gemini-2.0-flash-lite', fallback_count: 1 } },
    '/api/proposals': { status: 'success', proposals: [] },
    '/api/analytics': {
        regime: {
            volatility_state: 'ELEVATED_VOLATILITY',
            strategy_permissibility: {
                Momentum_Breakouts: false,
                Episodic_Pivots: false,
                Pullback_Continuations: false,
                Mean_Reversion: true,
            },
        },
        macro_calendar: {
            shield_active: true,
            stop_tighten_factor: 0.5,
            sizing_dampener: 0.5,
            next_event: { name: 'FOMC Rate Decision', date: '2026-12-09', time_et: '14:00', tier: 'TIER_1', hours_away: 18.5 },
            upcoming_events: [{ name: 'FOMC Rate Decision', date: '2026-12-09', time_et: '14:00', tier: 'TIER_1', hours_away: 18.5 }],
        },
        global_vetoes: [{ rule_id: 'ENH_72', reason: 'VIX >= 20 new-risk long veto' }],
        positions: [
            {
                ticker: 'NVDA',
                sweep_limit_usd: 119.90,
                tranches: { '25pct': { sell_shares: 2 }, '50pct': { sell_shares: 5 } },
                triggered_mandates: [],
            },
        ],
        watchlist_signals: [
            { ticker: 'PLTR', setup: 'PULLBACK_CONTINUATION', strategy_permissible: false, rvol: 0.62 },
        ],
        colab_vol_surface: {
            loaded: true,
            fresh: true,
            stale: false,
            generated_at_utc: '2026-11-26T20:00:00Z',
            tickers: {
                NVDA: {
                    atm_iv_30d: 44.5,
                    svi_skew_25d: -4.1,
                    gamma_flip_multi_expiry: 118.5,
                    net_gex_multi_expiry: 320.0,
                    vanna_exposure: 18.5,
                    charm_decay_bias: -3.2,
                    dealer_flow_regime: 'VANNA_TAILWIND',
                    expiries_analyzed: 5,
                    stale: false,
                },
            },
        },
        colab_sec_nlp: {
            loaded: true,
            fresh: true,
            stale: false,
            generated_at_utc: '2026-11-26T20:00:00Z',
            model_used: 'ProsusAI/finbert',
            tickers: {
                PLTR: {
                    dilution_risk_score: 72.0,
                    catalyst_sentiment: -0.25,
                    offering_overhang_flag: true,
                    going_concern_flag: false,
                    earnings_tone_delta: -0.1,
                    filings_analyzed: 3,
                    evidence_snippets: ['424B5 shelf <script>alert(1)</script>'],
                    stale: false,
                    model_used: 'ProsusAI/finbert',
                },
            },
        },
    },
    '/api/analytics/execution': {
        source: 'python_deterministic',
        macro_calendar: { shield_active: true, stop_tighten_factor: 0.5 },
        cash_reconciliation: { proof: 'Proof: Internally verified 1000.00 EUR Cash Active / 0.00 EUR Allocated this cycle = 1000.00 EUR Unallocated' },
        positions: [
            {
                ticker: 'NVDA',
                shares: 10,
                price: 120.5,
                sweep_limit_usd: 119.90,
                tranches: { '25pct': { sell_shares: 2 }, '50pct': { sell_shares: 5 }, '100pct': { sell_shares: 10 } },
                trailing_stop: {
                    anchor_price: 119.0,
                    pct_distance_from_anchor: 1.26,
                    effective_stop_pct: 1.0,
                    effective_stop_price: 117.81,
                },
                fib_limit_ladder: [
                    { tier: 'T1', label: '1.000 Measured Move', tranche_pct: 25, sell_shares: 2, limit_usd: 123.19, est_proceeds_usd: 246.38, est_proceeds_eur: 224.45, order_text: 'SELL 2 NVDA LMT $123.19' },
                    { tier: 'T2', label: '1.618 Golden Ratio', tranche_pct: 50, sell_shares: 5, limit_usd: 125.65, est_proceeds_usd: 628.25, est_proceeds_eur: 572.33, order_text: 'SELL 5 NVDA LMT $125.65' },
                ],
                order_directives: [],
            },
        ],
        order_directives: [],
    },
    '/api/analytics/backtest': {
        total_logged: 8,
        evaluated_count: 5,
        hit_rate_pct: 60.0,
        avg_ret_5d_pct: 2.45,
    },
    '/api/calibration/status': {
        loaded: true,
        schema_version: '1.0',
        tickers_calibrated: ['NVDA'],
        imported_at_utc: '2026-11-26T17:00:00Z',
    },
    '/api/colab/status': {
        transport: {
            mode: 'stdio',
            ready: false,
            mcp_sdk_available: true,
            uvx_available: false,
            setup_hint: 'Install uv',
        },
        artifacts: {
            vol_surface: { loaded: true, freshness: { fresh: true, stale: false } },
            quant_scout: { loaded: true, freshness: { fresh: true, stale: false } },
            sec_nlp: { loaded: false },
            backtest_evidence: { loaded: false },
        },
    },
    '/api/colab/artifacts/quant_scout': {
        artifact_type: 'quant_scout',
        loaded: true,
        freshness: { fresh: true, stale: false, age_hours: 1.0, ttl_hours: 48.0 },
        data: {
            schema_version: '2.0',
            universe_size: 520,
            candidates: [
                {
                    ticker: 'SMCI',
                    composite_score: 77.5,
                    rs_3m_pct: 29.4,
                    corr_to_portfolio: 0.18,
                    vol_regime: 'NORMAL',
                    cluster_label: 'LOW_CORR<img src=x onerror=alert(1)>',
                    rationale: 'Orthogonal alpha <script>evil()</script>',
                },
            ],
        },
    },
    '/api/analytics/sec/NVDA': {
        status: 'ok',
        ticker: 'NVDA',
        dilution_warning_72h: true,
        latest_dilution_form: '424B3',
        latest_dilution_date: '2026-11-23',
        filings: [
            {
                form: '424B3',
                filing_date: '2026-11-23',
                is_dilution_form: true,
                url: 'https://www.sec.gov/Archives/edgar/data/1045810/test_424b3.htm',
            },
        ],
    },
    '/api/analytics/peak/NVDA': {
        status: 'ok',
        ticker: 'NVDA',
        p50_peak_usd: 123.50,
        p75_peak_usd: 126.00,
        predicted_peak_low_usd: 123.50,
        predicted_peak_high_usd: 125.50,
        front_run_sell_limit_usd: 123.19,
        exhaustion_score: 76,
        exhaustion_state: 'SELL_ZONE',
        signals: ['TIME_WINDOW (10:05 ET inside peak corridor)', 'VWAP_EXTENDED (+3.20%)'],
        profile: {
            status: 'ok',
            sessions_analyzed: 60,
            median_peak_time_et: '10:05',
            peak_window_start_et: '09:45',
            peak_window_end_et: '10:45',
            pct_peaking_before_1030: 68.3,
            avg_fade_from_peak_pct: -2.4,
        },
    },
    '/api/list_models': { status: 'success', models: [], current_model: 'test-model' },
    '/api/chat': { status: 'success', response: 'Boot complete.', proposals: [], turn_id: 't1' },
    '/api/review': { status: 'success', response: '### 🎯 Actionable Recommendations\n- [LESSON] PORTFOLIO — Log per-ticker decisions.', turn_id: 't-review', improvement_proposals: [{ id: 'imp-1', kind: 'rule', rule_id: 'ENH_1', action: 'ADD', text: 'Halve size before PCE.', justification: 'row 3', source: 'Review Engine', status: 'pending' }] },
    '/api/improvements/imp-1/apply': { status: 'success', proposal: { id: 'imp-1', kind: 'rule', status: 'applied', amendment_id: 'A-1' } },
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
    const watchlistPosts = [];
    const colabJobPosts = [];
    let scoutCategories = [];
    window.fetch = async (url, opts = {}) => {
        const pathOnly = String(url).split('?')[0];
        const method = (opts.method || 'GET').toUpperCase();
        calls.push(`${method} ${pathOnly}`);
        if (pathOnly.startsWith('/api/colab/jobs/') && method === 'POST') {
            colabJobPosts.push({ path: pathOnly, body: JSON.parse(opts.body || '{}') });
            return { ok: true, status: 202, json: async () => ({ job_id: 'colab-sec_nlp-test1', status: 'queued' }) };
        }
        if (pathOnly === '/api/watchlist' && method === 'POST') {
            watchlistPosts.push(JSON.parse(opts.body || '[]'));
            return { ok: true, status: 200, json: async () => ({ status: 'success' }) };
        }
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
        const symOf = (r) => (r.querySelector('.ticker-symbol') || r.cells[0]).textContent.trim();
        const symbols = rows.filter((r) => !r.querySelector('td[colspan="14"]')).map(symOf);
        assert.deepStrictEqual(symbols, ['NVDA', 'AMD', 'PLTR', 'TSLA']);
        const amd = rows.find((r) => symOf(r) === 'AMD');
        assert.strictEqual(amd.cells[1].textContent.trim(), '—', 'null price should render as —');
        assert(rows.find((r) => symOf(r) === 'TSLA').textContent.includes('Pending scan'));
    });
    check('Fib Target UI separates Extension vs Retracement without dual Peak ambiguity', () => {
        const rows = [...window.document.querySelectorAll('#data-table tbody tr')];
        const symOf = (r) => (r.querySelector('.ticker-symbol') || r.cells[0]).textContent.trim();
        const nvda = rows.find((r) => symOf(r) === 'NVDA');
        const pltr = rows.find((r) => symOf(r) === 'PLTR');
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
    check('chat skips auto-boot on load and market table polls', () => {
        assert(!calls.includes('POST /api/chat'), 'chat should not auto-boot on page load');
        assert(calls.includes('GET /api/data'), 'market data not polled');
    });
    const updateAnalysisBtn = window.document.getElementById('update-analysis-btn');
    if (updateAnalysisBtn) updateAnalysisBtn.click();
    await new Promise((r) => setTimeout(r, 80));
    check('Update Analysis button runs Council briefing on demand', () => {
        assert(updateAnalysisBtn, 'update-analysis-btn missing');
        assert(calls.includes('POST /api/chat'), 'Update Analysis click did not trigger POST /api/chat');
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
    check('Key-Level Triggers & Sentinel: renders fired alert banner, armed rule row, and fallback status', () => {
        const alertBanner = window.document.getElementById('alerts-content');
        assert(alertBanner && alertBanner.textContent.includes('NVDA: HOD Break'), 'triggered alert missing from top alert bar');
        assert(alertBanner.textContent.includes('33.42 (+26.75%)'), `expected live VIX price & session % in alert bar, got: ${alertBanner.textContent}`);
        assert(!alertBanner.textContent.includes('+6.83%'), 'VIX alert should show live session_change_pct, not static opening gap_percent');
        const nvdaItems = alertBanner.querySelectorAll('.ticker-group:not([aria-hidden="true"]) .alert-item[data-ticker="NVDA"]');
        const pltrItems = alertBanner.querySelectorAll('.ticker-group:not([aria-hidden="true"]) .alert-item[data-ticker="PLTR"]');
        assert.strictEqual(nvdaItems.length, 1, `expected 1 NVDA alert, got ${nvdaItems.length}`);
        assert(!nvdaItems[0].textContent.includes('Stale Support Trigger'), 'stale lower-severity NVDA alert should be collapsed');
        assert.strictEqual(pltrItems.length, 1, `expected 1 PLTR alert, got ${pltrItems.length}`);
        assert(pltrItems[0].textContent.includes('PLTR: Stop Breach'), `expected critical PLTR alert, got ${pltrItems[0].textContent}`);
        const rulesList = window.document.getElementById('alerts-rules-container');
        assert(rulesList && rulesList.textContent.includes('NVDA') && rulesList.textContent.includes('sentinel'), 'armed trigger missing from sidebar');
        const sentinelStatus = window.document.getElementById('sentinel-status-text');
        assert(sentinelStatus && sentinelStatus.textContent.includes('gemini-2.0-flash-lite'), 'sentinel fallback model status missing');
    });
    check('Phase 3 UI: Quant & Backtest panel and SEC 72h dilution badge render cleanly', () => {
        const quantPanel = window.document.getElementById('analytics-quant-panel');
        assert(quantPanel, 'analytics-quant-panel missing');
        assert(quantPanel.textContent.includes('ELEVATED_VOLATILITY'), 'regime missing from quant panel');
        assert(quantPanel.textContent.includes('ENH_72'), 'global veto missing from quant panel');
        assert(quantPanel.textContent.includes('NVDA') && quantPanel.textContent.includes('2sh'), 'NVDA trim sizing missing from quant panel');
        assert(quantPanel.textContent.includes('60.0%'), 'backtest hit rate missing from quant panel');

        const nvdaBadge = window.document.querySelector('td.ticker-cell[data-sec-ticker="NVDA"] .sec-dilution-badge');
        assert(nvdaBadge, 'SEC 72h dilution badge missing on NVDA row');
        assert(nvdaBadge.textContent.includes('SEC 424B3'), `unexpected badge text: ${nvdaBadge.textContent}`);
        assert.strictEqual(nvdaBadge.getAttribute('href'), 'https://www.sec.gov/Archives/edgar/data/1045810/test_424b3.htm');
    });

    const planBtn = window.document.querySelector('.quant-plan-btn');
    if (planBtn) planBtn.click();
    await new Promise((r) => setTimeout(r, 80));
    const armStopBtn = window.document.querySelector('#exec-modal-body .exec-mini-btn.arm');
    if (armStopBtn) armStopBtn.click();
    await new Promise((r) => setTimeout(r, 80));
    check('Phase 4 UI: Macro Shield pill, Watchlist Setup tags, and Execution Ticket modal with 1-click alert arming', () => {
        const macroPill = window.document.getElementById('macro-calendar-pill');
        assert(macroPill && macroPill.classList.contains('macro-cal-shield'), 'expected active macro-cal-shield class');
        assert(macroPill.textContent.includes('FOMC'), `expected FOMC in macro pill, got ${macroPill.textContent}`);

        const quantPanel = window.document.getElementById('analytics-quant-panel');
        assert(quantPanel.textContent.includes('PLTR') && quantPanel.textContent.includes('PULLBACK'), 'watchlist setup tag missing');
        assert(planBtn, 'Execution Plan button missing in Quant panel');

        const execOverlay = window.document.getElementById('execution-plan-modal-overlay');
        const execBody = window.document.getElementById('exec-modal-body');
        assert(execOverlay && execOverlay.classList.contains('active'), 'Execution Ticket modal did not open');
        assert(execBody && execBody.textContent.includes('$117.81') && execBody.textContent.includes('$123.19'), 'trailing stop or T1 fib limit missing');
        assert(execBody.textContent.includes('1000.00 EUR'), 'cash reconciliation proof missing');
        assert(calls.includes('POST /api/alerts'), '1-click Arm Stop did not POST /api/alerts');
    });
    check('Design Packages 1-3: Command Strip, numeric alignment, held-row Plan button, Regime fallback & Chart-to-Ticket link', () => {
        const eqEl = window.document.getElementById('cmd-equity-val');
        const vetoesEl = window.document.getElementById('cmd-vetoes-val');
        const cmdPlanBtn = window.document.querySelector('#cmd-strip-actions .cmd-plan-btn');
        assert(eqEl && eqEl.textContent.trim().length > 0, 'cmd-equity-val missing');
        assert(vetoesEl && vetoesEl.textContent.trim() === '1', `expected 1 global veto, got ${vetoesEl && vetoesEl.textContent}`);
        assert(cmdPlanBtn && cmdPlanBtn.textContent.includes('NVDA Plan'), 'NVDA Plan button missing from command strip');

        assert.strictEqual(window.document.querySelectorAll('#data-table thead th').length, 14);
        assert.strictEqual(window.document.querySelectorAll('#data-table thead th.num-col').length, 8);
        const nvdaRowPlanBtn = window.document.querySelector('td.ticker-cell[data-sec-ticker="NVDA"] .row-plan-btn');
        assert(nvdaRowPlanBtn, 'inline Execution Ticket button missing on held NVDA row');

        window.updateVolatilityRegimePill({
            volatility_regime: 'UNAVAILABLE',
            precomputed_analytics: { regime: { volatility_state: 'BEAR_VOLATILE' } },
        });
        const pill = window.document.getElementById('volatility-regime-pill');
        assert.strictEqual(pill.textContent.trim(), 'BEAR_VOLATILE');
        assert(pill.classList.contains('regime-crisis'), 'BEAR_VOLATILE should map to regime-crisis');
        assert(window.document.getElementById('chart-exec-plan-btn'), 'chart-exec-plan-btn missing from chart modal');
    });

    // Top alert bar marquee: scrolls only when alerts overflow, loops via an aria-hidden clone
    {
        const content = window.document.getElementById('alerts-content');
        const proto = window.HTMLElement.prototype;
        const origSW = Object.getOwnPropertyDescriptor(proto, 'scrollWidth');
        const origCW = Object.getOwnPropertyDescriptor(proto, 'clientWidth');
        let groupWidth = 2000;
        Object.defineProperty(proto, 'scrollWidth', { configurable: true, get() { return this.classList && this.classList.contains('ticker-group') ? groupWidth : 0; } });
        Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return this.id === 'alerts-content' ? 800 : 0; } });
        const many = '<div class="alert-item critical" data-ticker="^VIX">⚠️ FEAR ALERT: (<span class="vix-spike-pct">+6.83%</span>)</div>'
            + '<div class="alert-item critical" data-event-id="e1"><span>A</span><button type="button" class="alert-dismiss-btn">&times;</button></div>'
            + '<div class="alert-item warning">B</div>';
        window.renderAlertTicker(content, many, 'vix|e1', { '.vix-spike-pct': '+6.83%' });
        const firstTrack = content.querySelector('.ticker-track');
        window.renderAlertTicker(content, many, 'vix|e1', { '.vix-spike-pct': '+7.12%' }); // live VIX % jitter
        const scrolling = content.classList.contains('is-scrolling');
        const clone = content.querySelector('.ticker-group[aria-hidden="true"]');
        const sameTrack = content.querySelector('.ticker-track') === firstTrack;
        const vixPcts = [...content.querySelectorAll('.vix-spike-pct')].map((el) => el.textContent);
        const duration = content.style.getPropertyValue('--marquee-duration');
        groupWidth = 300; // fits: single-pass scroll without cloning
        window.renderAlertTicker(content, '<div class="alert-item info">C</div>');
        const fitsSinglePass = content.classList.contains('is-scrolling')
            && content.classList.contains('is-single-pass')
            && !content.querySelector('.ticker-group[aria-hidden="true"]');
        window.renderAlertTicker(content, '');
        const emptyCleared = !content.classList.contains('is-scrolling')
            && !content.classList.contains('is-single-pass')
            && content.textContent.includes('NO ACTIVE ALERTS');
        if (origSW) Object.defineProperty(proto, 'scrollWidth', origSW); else delete proto.scrollWidth;
        if (origCW) Object.defineProperty(proto, 'clientWidth', origCW); else delete proto.clientWidth;
        const cssText = read(path.join(staticDir, 'styles.css'));
        check('Alert bar marquee: clones on overflow, single-pass scrolls when fitting, survives VIX % jitter', () => {
            assert(scrolling, 'overflowing alerts should scroll');
            assert(clone, 'aria-hidden clone missing for overflow seamless loop');
            assert.strictEqual(clone.querySelector('button').tabIndex, -1, 'clone buttons must not take focus');
            assert(sameTrack, 'live numeric update must not re-render .ticker-track (would restart animation)');
            assert.deepStrictEqual(vixPcts, ['+7.12%', '+7.12%'], `VIX % should update in-place on primary and clone, got ${JSON.stringify(vixPcts)}`);
            assert(/^\d+(\.\d)?s$/.test(duration) && parseFloat(duration) >= 12, `bad duration ${duration}`);
            assert(fitsSinglePass, 'fitting alerts should scroll via single-pass mode without creating a duplicate clone');
            assert(emptyCleared, 'empty state should clear marquee classes and render placeholder');
            assert(
                cssText.includes('.top-alert-bar:hover .ticker-content.is-scrolling.is-single-pass .ticker-track')
                && cssText.includes('animation-play-state: paused'),
                'hover pause selector must match/exceed .is-scrolling.is-single-pass specificity',
            );
        });
    }

    // Replies saved before payload stripping (e.g. archived sessions) must not show the SSoT JSON either
    window.chatUI.appendMessage('ai', [
        'Routine wrap.',
        '',
        '<details class="execution-payload-details" data-payload-state="no_changes">',
        '<summary>⚖️ SSoT Execution Payload (Hidden)</summary>',
        '<div class="payload-body">',
        '',
        '```json',
        '{"EXECUTION_PAYLOAD": {"portfolio_snapshot": []}}',
        '```',
        '',
        '</div>',
        '</details>',
    ].join('\n'), false);
    const legacyMsg = [...window.document.querySelectorAll('.modern-message.ai')].slice(-1)[0];
    check('Chat replies never display SSoT EXECUTION_PAYLOAD JSON (legacy blocks removed)', () => {
        assert.strictEqual(legacyMsg.querySelector('.execution-payload-details'), null, 'payload details block still rendered');
        assert(!legacyMsg.textContent.includes('EXECUTION_PAYLOAD'), 'payload JSON text still visible');
        assert(legacyMsg.textContent.includes('Routine wrap.'), 'prose should remain');
    });

    // Review Log chip goes to the dedicated read-only review endpoint, not the orchestrator chat
    const chatCallsBefore = calls.filter((c) => c === 'POST /api/chat').length;
    const saveLogBefore = calls.filter((c) => c === 'POST /api/save_decision_log').length;
    window.document.getElementById('qp-review-log').click();
    await new Promise((r) => setTimeout(r, 120));
    check('Review Log chip: POSTs /api/review (not /api/chat) and does not auto-save the review as a decision', () => {
        assert(calls.includes('POST /api/review'), 'Review Log click did not POST /api/review');
        assert.strictEqual(calls.filter((c) => c === 'POST /api/chat').length, chatCallsBefore, 'Review Log must not go through /api/chat');
        assert.strictEqual(calls.filter((c) => c === 'POST /api/save_decision_log').length, saveLogBefore, 'review output must not be auto-saved to decision log');
        const lastAi = [...window.document.querySelectorAll('.modern-message.ai')].slice(-1)[0];
        assert(lastAi && lastAi.querySelector('.reco-action-btn.save-lesson'), 'review [LESSON] line should render a Save Lesson button');
    });

    const impCard = window.document.querySelector('.improvement-card[data-improvement-id="imp-1"]');
    const impTitleBefore = impCard ? impCard.querySelector('.proposal-title').textContent : '';
    let applyBody = null;
    const origFetch = window.fetch;
    window.fetch = async (url, opts = {}) => {
        if (String(url).includes('/api/improvements/imp-1/apply')) applyBody = opts.body;
        return origFetch(url, opts);
    };
    if (impCard) {
        impCard.querySelector('.improvement-text').value = 'Halve size before PCE (edited).';
        impCard.querySelector('.proposal-apply').click();
    }
    await new Promise((r) => setTimeout(r, 80));
    window.fetch = origFetch;
    check('Improvement proposals: review rule proposal renders an approval card and Apply sends the edited text', () => {
        assert(impCard, 'improvement card for imp-1 not rendered');
        assert(impTitleBefore.includes('ENH_1') && impTitleBefore.includes('not applied'), `pending title was: ${impTitleBefore}`);
        assert(applyBody && JSON.parse(applyBody).text === 'Halve size before PCE (edited).', `apply body was ${applyBody}`);
        assert.strictEqual(impCard.dataset.state, 'applied');
        assert(impCard.textContent.includes('A-1'), 'applied card should show the amendment id');
    });

    // Review Log / Chat actionable recommendation buttons & cost estimate removal check
    window.chatUI.appendMessage('ai', [
        '### 🏁 Final Council Decision',
        'HOLD',
        '### 🎯 Actionable Recommendations',
        '- **[LESSON] UMAC** — Tighten pre-PCE stop by 50%.',
        '- **[TRIGGER] NVDA** — Arm defensive support stop at $118.50.',
    ].join('\n'), false, 0.05, { prompt_tokens: 100, candidates_tokens: 50 }, 'gemini-3.8-flash-high');
    const latestChatMsg = [...window.document.querySelectorAll('.modern-message.ai')].slice(-1)[0];
    const saveLessonBtn = latestChatMsg.querySelector('.reco-action-btn.save-lesson');
    const armRecoBtn = latestChatMsg.querySelector('.reco-action-btn.arm-trigger');
    if (saveLessonBtn) saveLessonBtn.click();
    if (armRecoBtn) armRecoBtn.click();
    await new Promise((r) => setTimeout(r, 80));
    check('Chat Actions & Cost Removal: omits cost estimate UI and wires 1-click Save Lesson & Arm Trigger buttons', () => {
        assert.strictEqual(window.document.getElementById('chat-session-cost'), null, '#chat-session-cost should be removed');
        assert.strictEqual(latestChatMsg.querySelector('.cost-estimator'), null, '.cost-estimator badge should not render');
        const pillLi = latestChatMsg.querySelector('.chat-callout-reco li');
        assert(pillLi && pillLi.textContent.includes('LESSON UMAC'), `expected space after pill, got: ${pillLi && pillLi.textContent}`);
        assert(saveLessonBtn && calls.includes('POST /api/trade_lessons/add'), '1-click Save Lesson did not POST /api/trade_lessons/add');
        assert(armRecoBtn && armRecoBtn.textContent.includes('Armed') || armRecoBtn.textContent.includes('$118.50'), '1-click Arm Trigger missing');
    });
    check('Sentinel 1m Cadence & Daily Peak Predictor UI: exposes 1m option, peak checkbox & Execution modal peak window', () => {
        const ivSelect = window.document.getElementById('sentinel-interval-select');
        const opt1m = ivSelect && ivSelect.querySelector('option[value="60"]');
        assert(opt1m && opt1m.textContent.trim() === '1m', '1m (60s) Sentinel interval option missing');
        assert(window.document.getElementById('sentinel-peak-toggle'), '#sentinel-peak-toggle missing');
        const execBody = window.document.getElementById('exec-modal-body');
        assert(execBody && execBody.textContent.includes('10:05 ET'), 'Median peak time missing from Execution Ticket modal');
        assert(execBody.textContent.includes('SELL_ZONE') && execBody.textContent.includes('Arm Peak Sell ($123.19)'), 'Peak exhaustion / Arm Peak Sell button missing');
    });

    // Stage A UI regressions: watchlist save without ReferenceError, Command Strip EUR cash, FX-missing draft guard
    await window.saveWatchlist(['AMD', 'MSFT']);
    check('Stage A: saveWatchlist succeeds without dDataStatus ReferenceError and updates #watchlist-status', () => {
        const st = window.document.getElementById('watchlist-status');
        assert(st && st.textContent.includes('Watchlist updated'), `unexpected watchlist status: ${st && st.textContent}`);
    });
    check('Stage A: Command Strip cash reads #cash-input-eur instead of missing #cash-val-eur', () => {
        const cmdCash = window.document.getElementById('cmd-cash-val');
        assert(cmdCash && cmdCash.textContent.includes('€1,000.00'), `unexpected #cmd-cash-val: ${cmdCash && cmdCash.textContent}`);
    });
    const origRate = window.eval('currentEurUsdRate');
    window.eval('currentEurUsdRate = null;');
    const addInput = window.document.getElementById('add-portfolio-ticker');
    addInput.value = 'PLTR';
    await window.addToPortfolio();
    const draftPltr = window.document.querySelector('tr.portfolio-item-row[data-draft="true"]');
    const pfHint = window.document.getElementById('portfolio-feedback');
    check('Stage A: addToPortfolio opens draft row when live FX rate is unavailable', () => {
        assert(draftPltr && draftPltr.textContent.includes('PLTR'), 'PLTR should open as draft row without FX rate');
        assert(pfHint && pfHint.textContent.includes('FX unavailable'), `expected FX warning, got: ${pfHint && pfHint.textContent}`);
    });
    window.eval(`currentEurUsdRate = ${JSON.stringify(origRate)};`);

    // Stage C: CSS/HTML unification & interactive trader UX checks
    const rawHtmlText = read(path.join(staticDir, 'index.html'));
    const modernCssText = read(path.join(staticDir, 'modern_ui.css'));
    check('Stage C: CSS/HTML unification removes duplicate dashboard rules and inline style/script blocks', () => {
        assert(!modernCssText.includes('--panel-bg:'), 'modern_ui.css should not define --panel-bg');
        assert(!modernCssText.includes('--text-secondary:'), 'modern_ui.css should not define --text-secondary');
        assert(!modernCssText.includes('.dashboard-grid'), 'modern_ui.css still contains dead .dashboard-grid');
        assert(!modernCssText.includes('.modern-table'), 'modern_ui.css still contains dead .modern-table');
        assert(!/<style\b/i.test(rawHtmlText), 'index.html should contain no inline <style> blocks');
        assert(!/<script(?![^>]*\bsrc=)[^>]*>[^<\s]/i.test(rawHtmlText), 'index.html should contain no inline <script> body');
    });

    const filterInput = window.document.getElementById('table-filter-input');
    const filterClearBtn = window.document.getElementById('table-filter-clear');
    const getRenderedSymbols = () => [...window.document.querySelectorAll('#data-table tbody tr')]
        .filter((r) => !r.querySelector('td[colspan="14"]'))
        .map((r) => (r.querySelector('.ticker-symbol') || r.cells[0]).textContent.trim());
    filterInput.value = 'PLTR';
    filterInput.dispatchEvent(new window.Event('input', { bubbles: true }));
    const filteredSyms = getRenderedSymbols();
    filterClearBtn.click();
    const restoredSyms = getRenderedSymbols();
    check('Stage C: table search filter narrows rows to matching ticker and clears cleanly', () => {
        assert.deepStrictEqual(filteredSyms, ['PLTR']);
        assert.deepStrictEqual(restoredSyms, ['NVDA', 'AMD', 'PLTR', 'TSLA']);
    });

    const tickerTh = window.document.querySelector('#data-table thead th[data-sort-key="ticker"]');
    tickerTh.click(); // desc within each section
    const sortDescSyms = getRenderedSymbols();
    const sortDescAttr = tickerTh.getAttribute('aria-sort');
    tickerTh.click(); // asc within each section
    const sortAscSyms = getRenderedSymbols();
    const sortAscAttr = tickerTh.getAttribute('aria-sort');
    tickerTh.click(); // clear sort
    const sortResetAttr = tickerTh.getAttribute('aria-sort');
    check('Stage C: sortable column headers sort per-section and reset on third click', () => {
        assert.strictEqual(sortDescAttr, 'descending');
        assert.deepStrictEqual(sortDescSyms, ['NVDA', 'TSLA', 'PLTR', 'AMD']);
        assert.strictEqual(sortAscAttr, 'ascending');
        assert.deepStrictEqual(sortAscSyms, ['NVDA', 'AMD', 'PLTR', 'TSLA']);
        assert.strictEqual(sortResetAttr, null);
    });

    const densityBtn = window.document.getElementById('table-density-btn');
    const tableContainer = window.document.getElementById('main-data-table-container');
    densityBtn.click();
    const compactOn = tableContainer.classList.contains('density-compact') && densityBtn.getAttribute('aria-pressed') === 'true';
    densityBtn.click();
    const compactOff = !tableContainer.classList.contains('density-compact') && densityBtn.getAttribute('aria-pressed') === 'false';
    check('Stage C: table density button toggles compact mode cleanly', () => {
        assert.strictEqual(compactOn, true);
        assert.strictEqual(compactOff, true);
    });

    window.adjustChatSize(1);
    const dynamicStyle = window.document.getElementById('dynamic-chat-style');
    const intradayCtxBtn = window.document.getElementById('toggle-intraday-ctx-btn');
    intradayCtxBtn.click();
    const ctxOn = window.chatUI.includeChartsNext === true && intradayCtxBtn.getAttribute('aria-pressed') === 'true';
    intradayCtxBtn.click();
    const ctxOff = window.chatUI.includeChartsNext === false && intradayCtxBtn.getAttribute('aria-pressed') === 'false';
    check('Stage C: adjustChatSize and 1m intraday context toggle work cleanly', () => {
        assert(dynamicStyle && dynamicStyle.textContent.includes('1.05rem'), `unexpected dynamic-chat-style: ${dynamicStyle && dynamicStyle.textContent}`);
        assert.strictEqual(ctxOn, true);
        assert.strictEqual(ctxOff, true);
    });

    const closeExecBtn = window.document.getElementById('close-exec-modal');
    if (closeExecBtn) closeExecBtn.click();
    window.document.body.focus();
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: '/', bubbles: true }));
    const slashFocusedFilter = window.document.activeElement === filterInput;
    const chatInputEl = window.document.getElementById('chat-input');
    chatInputEl.focus();
    chatInputEl.dispatchEvent(new window.KeyboardEvent('keydown', { key: 't', bubbles: true }));
    const sidebarIgnoredInsideInput = window.document.getElementById('main-content').classList.contains('sidebar-collapsed');
    chatInputEl.blur();
    window.document.body.focus();
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 't', bubbles: true }));
    const sidebarToggledOpen = !window.document.getElementById('main-content').classList.contains('sidebar-collapsed');
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 't', bubbles: true }));
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'c', bubbles: true }));
    const chatOpenedViaShortcut = window.document.getElementById('chat-window').classList.contains('active');
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    const chatClosedViaEsc = !window.document.getElementById('chat-window').classList.contains('active');
    check('Stage C: keyboard shortcuts (/, t, c, Esc) work and ignore editable inputs', () => {
        assert.strictEqual(slashFocusedFilter, true);
        assert.strictEqual(sidebarIgnoredInsideInput, true);
        assert.strictEqual(sidebarToggledOpen, true);
        assert.strictEqual(chatOpenedViaShortcut, true);
        assert.strictEqual(chatClosedViaEsc, true);
    });
    check('Colab & Stage D: offline calibration buttons render and status hydrates cleanly', () => {
        const expBtn = window.document.getElementById('colab-export-btn');
        const impBtn = window.document.getElementById('colab-import-btn');
        const calSt = window.document.getElementById('colab-calibration-status');
        assert(expBtn && impBtn, 'Colab calibration export/import buttons missing');
        assert(calSt && calSt.textContent.includes('1 ticker priors'), `unexpected #colab-calibration-status: ${calSt && calSt.textContent}`);
    });
    const promoteBtn = window.document.querySelector('#colab-quant-scout-list .colab-scout-promote-btn[data-scout-ticker="SMCI"]');
    if (promoteBtn) {
        promoteBtn.click();
        await new Promise((r) => setTimeout(r, 80));
    }
    check('Stage M2: Colab MCP panel, job buttons, Vol Surface GEX enrichment & XSS-safe Quant Scout promotion work', () => {
        const pill = window.document.getElementById('colab-mcp-transport-status');
        const btnIds = ['colab-run-vol-surface-btn', 'colab-run-quant-scout-btn', 'colab-run-sec-nlp-btn', 'colab-run-walk-forward-btn'];
        btnIds.forEach((id) => assert(window.document.getElementById(id), `Missing ${id}`));
        assert(pill && pill.textContent.includes('MCP: Setup (stdio)'), `Unexpected pill: ${pill && pill.textContent}`);

        const vsTag = window.document.querySelector('.vol-surface-tag');
        assert(vsTag && vsTag.textContent === 'V+', `Expected V+ vol-surface tag, got ${vsTag && vsTag.textContent}`);
        const badgeWithTip = window.document.querySelector('.dealer-badge[title*="VANNA_TAILWIND"]');
        assert(badgeWithTip, 'Expected GEX badge tooltip to include VANNA_TAILWIND');

        const scoutList = window.document.getElementById('colab-quant-scout-list');
        assert(scoutList && scoutList.textContent.includes('SMCI'), 'Expected SMCI in Quant Scout list');
        assert.strictEqual(scoutList.querySelectorAll('img, script').length, 0, 'XSS elements must never render in Quant Scout list');

        assert(promoteBtn, 'Missing + Watch button for SMCI');
        assert(calls.includes('POST /api/watchlist'), 'Expected POST /api/watchlist on + Watch click');
        const lastWlPost = watchlistPosts[watchlistPosts.length - 1];
        assert.deepStrictEqual(lastWlPost, ['AMD', 'SMCI']);
    });

    const useLlmToggle = window.document.getElementById('colab-sec-use-llm-toggle');
    const secNlpBtn = window.document.getElementById('colab-run-sec-nlp-btn');
    if (useLlmToggle && secNlpBtn) {
        useLlmToggle.checked = true;
        secNlpBtn.click();
        await new Promise((r) => setTimeout(r, 40));
    }
    check('Stage M3: SEC NLP risk badge escapes snippets and L4 7B LLM toggle sends use_llm option', () => {
        const nlpBadge = window.document.querySelector('.sec-nlp-badge.risk-high');
        assert(nlpBadge && nlpBadge.textContent === 'NLP D:72', `Unexpected SEC NLP badge: ${nlpBadge && nlpBadge.textContent}`);
        assert(nlpBadge.getAttribute('title').includes('424B5 shelf'), 'Expected escaped snippet in SEC NLP tooltip');
        const secPost = colabJobPosts.find((p) => p.path === '/api/colab/jobs/sec_nlp');
        assert(secPost && secPost.body && secPost.body.options && secPost.body.options.use_llm === true, 'Expected use_llm:true option on SEC NLP POST');
    });

    window.close();
    console.log(results.join('\n'));
    const failed = results.filter((r) => r.startsWith('FAIL')).length;
    console.log(failed ? `${failed} FAILED` : 'ALL PASSED');
    if (errors.length) console.log('Captured errors:\n' + errors.join('\n'));
    process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
