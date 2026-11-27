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
            backtest_evidence: { loaded: true, freshness: { fresh: true, stale: false } },
        },
        pipeline: {
            state: 'setup_required',
            trigger: 'startup',
            percent: 0,
            step_label: 'Install uv',
            jobs: {},
        },
    },
    '/api/colab/artifacts/backtest_evidence': {
        artifact_type: 'backtest_evidence',
        loaded: true,
        freshness: { fresh: true, stale: false, age_hours: 1.0, ttl_hours: 168.0 },
        data: {
            schema_version: '2.0',
            generated_at_utc: '2026-11-26T20:00:00Z',
            protocol_06_read_only: true,
            folds: 6,
            universe_count: 15,
            friction_hurdle_pct: 0.85,
            stability_score: 0.83,
            baseline_metrics: { sharpe_ratio: 1.18, sortino_ratio: 1.5, max_drawdown_pct: -14.0, hit_rate_pct: 53.0, profit_factor: 1.4 },
            candidate_metrics: { sharpe_ratio: 1.44, sortino_ratio: 1.9, max_drawdown_pct: -10.2, hit_rate_pct: 58.0, profit_factor: 1.75 },
            proposed_rule_deltas: [
                {
                    rule_id: 'ENH_104',
                    parameter: 'trailing_stop_vwap_ext_pct',
                    current_value: 2.0,
                    proposed_value: 2.5,
                    rationale: 'OOS improvement <img src=x onerror=alert(1)>',
                },
            ],
        },
    },
    '/api/colab/artifacts/quant_scout': {
        artifact_type: 'quant_scout',
        loaded: true,
        freshness: { fresh: true, stale: false, age_hours: 1.0, ttl_hours: 48.0 },
        data: {
            schema_version: '2.0',
            universe_size: 520,
            notes: 'Screened 520 universe ticker(s); ADV >= $20M.',
            candidates: [
                {
                    ticker: 'SMCI',
                    sector: 'AI & Data',
                    composite_score: 77.5,
                    rs_1m_pct: 12.1,
                    rs_3m_pct: 29.4,
                    rs_6m_pct: 48.0,
                    rsi_14: 61.5,
                    corr_to_portfolio: 0.18,
                    pead_score: 14.2,
                    adv_usd_m: 920.0,
                    vol_regime: 'NORMAL',
                    cluster_label: 'LOW_CORR<img src=x onerror=alert(1)>',
                    rationale: 'Orthogonal alpha <script>evil()</script>',
                },
                {
                    ticker: 'REGN',
                    sector: 'Biotech',
                    composite_score: 64.0,
                    rs_1m_pct: 8.2,
                    rs_3m_pct: 19.5,
                    rs_6m_pct: 22.0,
                    rsi_14: 58.0,
                    corr_to_portfolio: 0.05,
                    pead_score: 0.0,
                    adv_usd_m: 410.0,
                    vol_regime: 'NORMAL',
                    cluster_label: 'LOW_CORR_ALPHA',
                    rationale: '[Biotech] 3m RS +19.5%, RSI 58, corr +0.05',
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
    let mockColabPipeline = null;
    let scoutCategories = [];
    window.fetch = async (url, opts = {}) => {
        const pathOnly = String(url).split('?')[0];
        const method = (opts.method || 'GET').toUpperCase();
        calls.push(`${method} ${pathOnly}`);
        if (pathOnly === '/api/colab/jobs/run_all' && method === 'POST') {
            const body = JSON.parse(opts.body || '{}');
            colabJobPosts.push({ path: pathOnly, body });
            mockColabPipeline = {
                state: 'running',
                trigger: 'manual',
                percent: 50,
                step_label: 'Step 2/4: Running SEC Filing Risk Scan...',
                jobs: {
                    vol_surface: { status: 'succeeded', label: 'Options Flow & Vol Surface', progress_pct: 100 },
                    sec_nlp: { status: 'running', label: 'SEC Filing Risk Scan', progress_pct: 55 },
                    quant_scout: { status: 'pending', label: 'Stock Finder (500+)', progress_pct: 0 },
                    walk_forward: { status: 'pending', label: 'Strategy Check (Backtest)', progress_pct: 0 },
                },
            };
            return {
                ok: true,
                status: 202,
                json: async () => mockColabPipeline,
            };
        }
        if (pathOnly === '/api/colab/status' && mockColabPipeline) {
            const base = JSON.parse(JSON.stringify(API['/api/colab/status']));
            base.pipeline = mockColabPipeline;
            return { ok: true, status: 200, json: async () => base };
        }
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
        const sections = rows.filter((r) => r.matches('tr[data-section-row]')).map((r) => r.textContent.trim());
        assert.deepStrictEqual(sections, ['Your Portfolio', 'Strategic Watchlist']);
        const symOf = (r) => (r.querySelector('.ticker-symbol') || r.cells[0]).textContent.trim();
        const symbols = rows.filter((r) => !r.matches('tr[data-section-row]')).map(symOf);
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
        assert.strictEqual(pill.textContent.trim(), 'Elevated');
        assert.strictEqual(pill.getAttribute('data-regime'), 'ELEVATED');
        assert(pill.classList.contains('regime-elevated'), 'expected regime-elevated class');

        const macroTicker = window.document.querySelector('#macro-card-SPY .macro-ticker');
        const macroSubtitle = window.document.querySelector('#macro-card-SPY .macro-subtitle');
        assert(macroTicker && macroTicker.textContent.trim() === 'SPY', 'macro card ticker header missing');
        assert(macroSubtitle && macroSubtitle.textContent.trim() === 'S&P 500', 'macro card subtitle missing');

        // UX Stage 3: top-level workspace views replace the hidden TOOLS drawer.
        const doc = window.document;
        assert(!doc.getElementById('dashboard-sidebar') && !doc.getElementById('toggle-sidebar-btn'), 'TOOLS drawer should be gone');
        assert(!doc.getElementById('view-today').hidden, 'Today should be the default view');
        ['portfolio', 'opportunities', 'alerts', 'research'].forEach((v) => {
            assert(doc.getElementById(`view-${v}`).hidden, `${v} view should start hidden`);
        });
        assert.strictEqual(doc.getElementById('nav-today').getAttribute('aria-selected'), 'true');
        doc.getElementById('nav-portfolio').click();
        assert(!doc.getElementById('view-portfolio').hidden && doc.getElementById('view-today').hidden, 'Portfolio tab should switch views');
        assert.strictEqual(doc.getElementById('nav-portfolio').getAttribute('aria-selected'), 'true');
        assert.strictEqual(window.location.hash, '#portfolio', 'view should be deep-linkable');
        doc.getElementById('nav-today').click();
        assert(!doc.getElementById('view-today').hidden, 'Today tab should switch back');
        assert.strictEqual(doc.querySelectorAll('.desktop-only').length, 0, 'no panels may be hidden on mobile');
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
        assert.strictEqual(msg.querySelector('.verdict-pill').textContent.trim(), 'AI Advisory: Hold');
        assert.strictEqual(msg.querySelector('.verdict-pill').getAttribute('data-verdict'), 'HOLD');
        assert(msg.querySelector('.verdict-advisory-note'), 'verdict must carry an advisory-only note');
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
        // Bar shows the 2 most severe alerts; the full set (incl. this warning) lives in the Alerts view.
        const alertBanner = window.document.getElementById('alerts-content');
        const alertsList = window.document.getElementById('alerts-view-list');
        assert(alertsList && alertsList.textContent.includes('NVDA: HOD Break'), 'triggered alert missing from Alerts view');
        assert(alertBanner && alertBanner.querySelectorAll('.alert-item.critical').length === 2, 'bar should lead with the critical alerts');
        assert(alertBanner.querySelector('.alert-more-link'), 'bar should link to the remaining alerts');
        assert(alertBanner.textContent.includes('33.42 (+26.75%)'), `expected live VIX price & session % in alert bar, got: ${alertBanner.textContent}`);
        assert(!alertBanner.textContent.includes('+6.83%'), 'VIX alert should show live session_change_pct, not static opening gap_percent');
        // De-duplication is checked on the full set (Alerts view), not just the 2-slot bar.
        const nvdaItems = alertsList.querySelectorAll('.alert-item[data-ticker="NVDA"]');
        const pltrItems = alertsList.querySelectorAll('.alert-item[data-ticker="PLTR"]');
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
        assert(quantPanel.textContent.includes('Elevated volatility') && !quantPanel.textContent.includes('ELEVATED_VOLATILITY'), 'plain-English regime missing from quant panel');
        assert(quantPanel.textContent.includes('VIX >= 20 new-risk long veto') && !quantPanel.textContent.includes('ENH_72'), 'global veto should show its reason without the rule ID');
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
        assert.strictEqual(pill.textContent.trim(), 'Bearish · Volatile');
        assert.strictEqual(pill.getAttribute('data-regime'), 'BEAR_VOLATILE');
        assert(pill.classList.contains('regime-crisis'), 'BEAR_VOLATILE should map to regime-crisis');
        assert(window.document.getElementById('chart-exec-plan-btn'), 'chart-exec-plan-btn missing from chart modal');
    });

    // Top alert bar (UX Stage 3): stationary, highest severity first, "+N more" → Alerts view
    {
        const content = window.document.getElementById('alerts-content');
        const bar = window.document.getElementById('top-alert-bar');
        const listEl = window.document.getElementById('alerts-view-list');
        const many = '<div class="alert-item info" data-event-id="e0"><span>INFO first</span></div>'
            + '<div class="alert-item critical" data-ticker="^VIX">⚠️ FEAR ALERT: (<span class="vix-spike-pct">+6.83%</span>)</div>'
            + '<div class="alert-item critical" data-event-id="e1"><span>A</span><button type="button" class="alert-dismiss-btn">&times;</button></div>'
            + '<div class="alert-item warning">B</div>';
        window.renderAlertTicker(content, many, 'vix|e1', { '.vix-spike-pct': '+6.83%' });
        const firstStrip = content.querySelector('.alert-strip');
        window.renderAlertTicker(content, many, 'vix|e1', { '.vix-spike-pct': '+7.12%' }); // live VIX % jitter
        const sameStrip = content.querySelector('.alert-strip') === firstStrip;
        const barItems = [...content.querySelectorAll('.alert-item')];
        const barSeverities = barItems.map((el) => (el.classList.contains('critical') ? 'critical' : el.classList.contains('warning') ? 'warning' : 'info'));
        const moreLink = content.querySelector('.alert-more-link');
        const listCount = listEl ? listEl.querySelectorAll('.alert-item').length : 0;
        const vixPcts = [...window.document.querySelectorAll('.vix-spike-pct')].map((el) => el.textContent);
        const navBadge = window.document.getElementById('nav-badge-alerts');
        const badgeTxt = navBadge && !navBadge.hidden ? navBadge.textContent : '';
        const barShown = bar.classList.contains('has-alerts');
        const noMotion = !content.querySelector('.ticker-track') && !content.classList.contains('is-scrolling');
        if (moreLink) moreLink.click();
        const wentToAlerts = !window.document.getElementById('view-alerts').hidden;
        window.setWorkspaceView('today');
        window.renderAlertTicker(content, '');
        const emptyHidesBar = !bar.classList.contains('has-alerts') && navBadge.hidden
            && listEl.textContent.includes('No active alerts');
        const cssText = read(path.join(staticDir, 'styles.css'));
        check('Alert bar: stationary, top-2 by severity, "+N more" opens Alerts view, live % updates in place', () => {
            assert(noMotion, 'alert bar must not scroll (no marquee track / is-scrolling)');
            assert.deepStrictEqual(barSeverities, ['critical', 'critical'], `bar should show the 2 most severe, got ${barSeverities}`);
            assert(moreLink && moreLink.textContent.includes('+2 more'), `missing +N more link: ${moreLink && moreLink.textContent}`);
            assert.strictEqual(listCount, 4, 'Alerts view must list every active alert');
            assert(sameStrip, 'live numeric update must not rebuild the strip');
            assert(vixPcts.length >= 1 && vixPcts.every((t) => t === '+7.12%'), `VIX % should update in place, got ${JSON.stringify(vixPcts)}`);
            assert.strictEqual(badgeTxt, '4', `Alerts nav badge should show 4, got "${badgeTxt}"`);
            assert(barShown, 'bar should be visible while alerts are active');
            assert(wentToAlerts, '"+N more" should switch to the Alerts view');
            assert(emptyHidesBar, 'no alerts: bar hidden, badge hidden, Alerts view shows empty state');
            assert(cssText.includes('.top-alert-bar:not(.has-alerts)') && !cssText.includes('@keyframes marquee'), 'marquee CSS should be retired');
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
        assert(!impTitleBefore.includes('ENH_1') && impTitleBefore.includes('not applied'), `pending title was: ${impTitleBefore}`);
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
        .filter((r) => !r.matches('tr[data-section-row]'))
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
    chatInputEl.dispatchEvent(new window.KeyboardEvent('keydown', { key: '2', bubbles: true }));
    const viewKeyIgnoredInsideInput = !window.document.getElementById('view-today').hidden;
    chatInputEl.blur();
    window.document.body.focus();
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: '2', bubbles: true }));
    const viewKeySwitched = !window.document.getElementById('view-portfolio').hidden;
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: '1', bubbles: true }));
    const viewKeyBackToToday = !window.document.getElementById('view-today').hidden;
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'c', bubbles: true }));
    const chatOpenedViaShortcut = window.document.getElementById('chat-window').classList.contains('active');
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    const chatClosedViaEsc = !window.document.getElementById('chat-window').classList.contains('active');
    check('Stage C: keyboard shortcuts (/, 1–5 views, c, Esc) work and ignore editable inputs', () => {
        assert.strictEqual(slashFocusedFilter, true);
        assert.strictEqual(viewKeyIgnoredInsideInput, true, 'view keys must be ignored while typing');
        assert.strictEqual(viewKeySwitched, true, '"2" should open the Portfolio view');
        assert.strictEqual(viewKeyBackToToday, true, '"1" should return to Today');
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
        assert(pill && pill.textContent.includes('Cloud: Setup needed'), `Unexpected pill: ${pill && pill.textContent}`);
        assert.strictEqual(pill.getAttribute('data-transport-mode'), 'stdio');
        assert((pill.getAttribute('title') || '').includes('Connection mode: stdio'), 'transport mode should move into the tooltip');

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
        assert(nlpBadge && nlpBadge.textContent === 'Filing risk 72', `Unexpected SEC NLP badge: ${nlpBadge && nlpBadge.textContent}`);
        assert(nlpBadge.getAttribute('title').includes('424B5 shelf'), 'Expected escaped snippet in SEC NLP tooltip');
        const secPost = colabJobPosts.find((p) => p.path === '/api/colab/jobs/sec_nlp');
        assert(secPost && secPost.body && secPost.body.options && secPost.body.options.use_llm === true, 'Expected use_llm:true option on SEC NLP POST');
    });
    check('Stage M4: Read-only Protocol 06 Evidence Pack renders metrics, escapes deltas & exposes Copy Proposal button', () => {
        const panel = window.document.getElementById('colab-evidence-panel');
        const sum = window.document.getElementById('colab-evidence-summary');
        const copyBtn = window.document.getElementById('colab-copy-p06-btn');
        assert(panel && sum && copyBtn, 'Missing Stage M4 Evidence Pack elements');
        assert(panel.textContent.includes('Read-Only'), 'Missing Protocol 06 Read-Only notice');
        assert(sum.textContent.includes('1.18 -> 1.44'), `Expected Sharpe comparison, got: ${sum.textContent}`);
        assert(sum.textContent.includes('Trailing stop vwap ext pct') && !sum.textContent.includes('ENH_104'), 'Expected delta row by parameter, without rule ID');
        assert.strictEqual(sum.querySelectorAll('img, script').length, 0, 'XSS elements must never render in Evidence Pack');
    });

    // UI Streamlining: Watchlist Rescan, unified Discovery tabs, unified Quant Lab tabs, slim Admin
    const wlContent = window.document.getElementById('watchlist-content');
    const scoutContent = window.document.getElementById('scout-content');
    const quantContent = window.document.getElementById('analytics-quant-content');
    const adminContent = window.document.getElementById('admin-content');
    const scoutQuantTab = scoutContent.querySelector('.sidebar-tab-btn[data-panel-tab="scout-quant"]');
    const scoutSectorTab = scoutContent.querySelector('.sidebar-tab-btn[data-panel-tab="scout-sector"]');
    const scoutQuantPane = scoutContent.querySelector('.sidebar-tab-pane[data-tab-pane="scout-quant"]');
    const scoutSectorPane = scoutContent.querySelector('.sidebar-tab-pane[data-tab-pane="scout-sector"]');
    const researchContent = window.document.getElementById('research-content');
    const viewOf = (id) => {
        const el = window.document.getElementById(id);
        const v = el && el.closest('.workspace-view');
        return v ? v.getAttribute('data-view') : null;
    };

    const discoveryDefaultOk = scoutQuantTab.classList.contains('active')
        && scoutQuantTab.getAttribute('aria-selected') === 'true'
        && !scoutQuantPane.classList.contains('hidden')
        && scoutSectorPane.classList.contains('hidden');
    scoutSectorTab.click();
    const discoverySwitchedOk = !scoutQuantTab.classList.contains('active')
        && scoutSectorTab.classList.contains('active')
        && scoutSectorTab.getAttribute('aria-selected') === 'true'
        && scoutQuantPane.classList.contains('hidden')
        && !scoutSectorPane.classList.contains('hidden');
    scoutQuantTab.click();
    const discoveryRestoredOk = scoutQuantTab.classList.contains('active')
        && !scoutQuantPane.classList.contains('hidden')
        && scoutSectorPane.classList.contains('hidden');

    check('UI Streamlining: Watchlist Rescan, Discovery tabs, workspace homes for health/research, Admin in Settings', () => {
        assert(wlContent.contains(window.document.getElementById('scan-watchlist-btn')), '#scan-watchlist-btn should live inside #watchlist-content');
        assert(!scoutContent.contains(window.document.getElementById('scan-watchlist-btn')), '#scan-watchlist-btn should not live inside #scout-content');
        assert(scoutContent.contains(window.document.getElementById('colab-run-quant-scout-btn')), '#colab-run-quant-scout-btn should live inside #scout-content');
        assert(scoutContent.contains(window.document.getElementById('colab-quant-scout-panel')), '#colab-quant-scout-panel should live inside #scout-content');
        assert(scoutContent.contains(window.document.getElementById('run-ai-scout-btn')), '#run-ai-scout-btn should live inside #scout-content');
        assert.strictEqual(discoveryDefaultOk, true, 'Discovery default tab should be scout-quant');
        assert.strictEqual(discoverySwitchedOk, true, 'Discovery tab switch to scout-sector failed');
        assert.strictEqual(discoveryRestoredOk, true, 'Discovery tab switch back to scout-quant failed');

        assert(quantContent.contains(window.document.getElementById('analytics-quant-panel')), '#analytics-quant-panel should live inside #analytics-quant-content');
        assert(researchContent.contains(window.document.getElementById('colab-evidence-panel')), '#colab-evidence-panel should live inside #research-content');
        assert(researchContent.contains(window.document.getElementById('colab-export-btn')), '#colab-export-btn should live inside #research-content');
        assert.strictEqual(viewOf('analytics-quant-panel'), 'portfolio', 'portfolio health belongs in the Portfolio view');
        assert.strictEqual(viewOf('portfolio-manager-body'), 'portfolio');
        assert.strictEqual(viewOf('watchlist-content'), 'portfolio');
        assert.strictEqual(viewOf('scout-content'), 'opportunities');
        assert.strictEqual(viewOf('alerts-rules-container'), 'alerts');
        assert.strictEqual(viewOf('colab-run-all-btn'), 'research');
        assert.strictEqual(viewOf('data-table'), 'today');

        assert(window.document.getElementById('app-settings-popover').contains(adminContent), '#admin-content should live in the Settings popover');
        assert(window.document.getElementById('app-settings-popover').contains(window.document.getElementById('open-indices-modal-btn')), 'Manage indices should live in Settings');
        assert(adminContent.contains(window.document.getElementById('clear-log-btn')), '#clear-log-btn should remain inside #admin-content');
        assert(!adminContent.contains(window.document.getElementById('colab-quant-scout-panel')), '#colab-quant-scout-panel should be removed from #admin-content');
        assert(!adminContent.contains(window.document.getElementById('colab-evidence-panel')), '#colab-evidence-panel should be removed from #admin-content');
        assert(!adminContent.contains(window.document.getElementById('colab-export-btn')), '#colab-export-btn should be removed from #admin-content');
    });

    const runAllBtn = window.document.getElementById('colab-run-all-btn');
    if (runAllBtn) {
        runAllBtn.click();
        await new Promise((r) => setTimeout(r, 80));
    }
    check('Package 1: Colab job feedback becomes active, Setup Guidance shows when not ready, and Run All updates progress banner', () => {
        const jobStatus = window.document.getElementById('colab-mcp-job-status');
        assert(jobStatus && jobStatus.classList.contains('active'), '#colab-mcp-job-status must include .active so feedback is visible');
        const setupBox = window.document.getElementById('colab-setup-guidance');
        assert(setupBox && setupBox.style.display === 'flex', '#colab-setup-guidance should be visible when MCP transport is not ready');
        assert(setupBox.textContent.includes('Install uv'), '#colab-setup-guidance should display transport setup_hint');
        const runAllPost = colabJobPosts.find((p) => p.path === '/api/colab/jobs/run_all');
        assert(runAllPost, 'Expected POST /api/colab/jobs/run_all on #colab-run-all-btn click');
        const banner = window.document.getElementById('colab-progress-banner');
        const fill = window.document.getElementById('colab-pipeline-bar-fill');
        const chips = window.document.getElementById('colab-pipeline-chips');
        assert(banner && !banner.classList.contains('hidden'), '#colab-progress-banner should show while pipeline is running');
        assert(fill && fill.style.width === '50%', `Expected 50% progress width, got ${fill && fill.style.width}`);
        assert(chips && chips.textContent.includes('Options Flow') && chips.textContent.includes('Filing Risk'), 'Pipeline chips missing expected job labels');
    });

    check('Package 2: Today\'s Brief & Suggested Next Steps synthesizes plain-English actionable items safely', () => {
        const briefCard = window.document.getElementById('action-brief-card');
        const briefList = window.document.getElementById('action-brief-list');
        const moodPill = window.document.getElementById('brief-mood-summary');
        assert(briefCard && briefList && moodPill, 'Action brief elements missing');
        assert(!moodPill.textContent.includes('Analyzing market state'), `Mood summary did not update: ${moodPill.textContent}`);
        assert(briefList.textContent.includes('SMCI'), `Expected Stock Finder top candidate SMCI in action brief, got: ${briefList.textContent}`);
        assert(briefList.querySelectorAll('.brief-action-btn').length > 0, 'Expected actionable buttons in Today\'s Brief');
        assert.strictEqual(briefList.querySelectorAll('img, script').length, 0, 'XSS elements must never render in Today\'s Brief');
    });

    const simpleBtn = window.document.getElementById('table-view-simple-btn');
    const proBtn = window.document.getElementById('table-view-pro-btn');
    const defaultSimple = tableContainer.classList.contains('view-simple') && simpleBtn.getAttribute('aria-pressed') === 'true';
    proBtn.click();
    const switchedPro = !tableContainer.classList.contains('view-simple') && proBtn.getAttribute('aria-pressed') === 'true';
    simpleBtn.click();
    const restoredSimple = tableContainer.classList.contains('view-simple') && simpleBtn.getAttribute('aria-pressed') === 'true';
    check('Package 3 & 4: Simple/Pro table toggle, plain-English signal pills, and version v19.0 render cleanly', () => {
        assert.strictEqual(defaultSimple, true, 'Table should default to Simple view (.view-simple)');
        assert.strictEqual(switchedPro, true, 'Clicking Pro (14) should remove .view-simple');
        assert.strictEqual(restoredSimple, true, 'Clicking Simple should restore .view-simple');
        const plainPill = window.document.querySelector('#data-table tbody .signal-plain-pill');
        assert(plainPill && plainPill.textContent.trim().length > 0, 'Plain-English signal pill missing in table row');
        const verEl = window.document.querySelector('header .version');
        assert(verEl && verEl.textContent.trim() === 'v19.0', `Expected version v19.0, got ${verEl && verEl.textContent}`);
    });

    mockColabPipeline = {
        state: 'failed',
        trigger: 'startup',
        started_at_utc: '2026-11-26T20:00:00Z',
        percent: 100,
        step_label: 'Cloud research finished with 4 failure(s)',
        error_summary: 'Google Colab browser session did not connect within 60s.',
        jobs: {
            vol_surface: { status: 'failed', label: 'Options Flow & Vol Surface', progress_pct: 100, error: 'Google Colab browser session did not connect within 60s.' },
        },
    };
    await window.fetchColabMcpStatus();
    const bannerEl = window.document.getElementById('colab-progress-banner');
    const errDetailEl = window.document.getElementById('colab-pipeline-error-detail');
    const retryBannerBtn = window.document.getElementById('colab-banner-retry-btn');
    const dismissBannerBtn = window.document.getElementById('colab-banner-dismiss-btn');
    const errBannerRendered = bannerEl.classList.contains('is-failed')
        && !bannerEl.classList.contains('hidden')
        && retryBannerBtn.style.display === 'inline-block'
        && errDetailEl.style.display === 'block'
        && errDetailEl.textContent.includes('Google Colab browser session did not connect');
    dismissBannerBtn.click();
    const dismissedBannerOk = bannerEl.classList.contains('hidden');
    check('Colab Failure Banner: surfaces root-cause error, Retry button, and dismiss button', () => {
        assert.strictEqual(errBannerRendered, true, 'Expected failed state banner, retry button, and error detail');
        assert.strictEqual(dismissedBannerOk, true, 'Dismiss button should hide #colab-progress-banner');
    });

    const connectUrl = 'https://colab.research.google.com/notebooks/empty.ipynb#mcpProxyToken=abc&mcpProxyPort=51277';
    mockColabPipeline = {
        state: 'running',
        trigger: 'manual',
        started_at_utc: '2026-11-26T20:10:00Z',
        percent: 25,
        step_label: "Click 'Open Google Colab to Connect' in the banner",
        connect_url: connectUrl,
        jobs: {
            vol_surface: { status: 'running', label: 'Options Flow & Vol Surface', progress_pct: 25 },
        },
    };
    await window.fetchColabMcpStatus();
    const connectLink = window.document.getElementById('colab-connect-link');
    const safeLinkShown = connectLink && connectLink.style.display === 'inline-block' && connectLink.getAttribute('href') === connectUrl;

    mockColabPipeline = {
        ...mockColabPipeline,
        started_at_utc: '2026-11-26T20:11:00Z',
        connect_url: 'javascript:alert(1)',
    };
    await window.fetchColabMcpStatus();
    const badLinkBlocked = connectLink && connectLink.style.display === 'none' && connectLink.getAttribute('href') === '#';
    mockColabPipeline = { state: 'idle', percent: 0, step_label: 'Idle', jobs: {} };
    await window.fetchColabMcpStatus();
    check('Colab Connect Link: surfaces safe colab.research.google.com URL and blocks unsafe URLs', () => {
        assert.strictEqual(safeLinkShown, true, 'Expected #colab-connect-link visible with safe Colab URL');
        assert.strictEqual(badLinkBlocked, true, 'Unsafe javascript: URL must remain hidden and reset to #');
    });

    mockColabPipeline = {
        state: 'partial',
        trigger: 'manual',
        started_at_utc: '2026-11-26T20:15:00Z',
        percent: 100,
        step_label: 'Completed with 1 issue(s) across 4 steps',
        error_summary: 'Simulated step failure',
        jobs: {
            vol_surface: { status: 'succeeded', label: 'Options Flow & Vol Surface', progress_pct: 100 },
            quant_scout: { status: 'failed', label: 'Stock Finder (500+)', progress_pct: 100, error: 'Simulated step failure' },
        },
    };
    await window.fetchColabMcpStatus();
    const partialRetryLabelOk = retryBannerBtn && retryBannerBtn.textContent === '↻ Retry Failed';
    colabJobPosts.length = 0;
    retryBannerBtn.click();
    await new Promise((r) => setTimeout(r, 40));
    const retrySentFailedOnly = colabJobPosts.some((p) => p.path === '/api/colab/jobs/run_all' && p.body && p.body.retry_failed_only === true);
    check('Colab Partial Retry: Retry button relabels and sends retry_failed_only=true', () => {
        assert.strictEqual(partialRetryLabelOk, true, 'Expected Retry Failed label on partial pipeline state');
        assert.strictEqual(retrySentFailedOnly, true, 'Expected Retry button to send retry_failed_only=true');
    });

    const openScoutModalBtn = window.document.getElementById('colab-open-scout-modal-btn');
    const scoutModalOverlay = window.document.getElementById('scout-rankings-modal-overlay');
    const scoutModalTbody = window.document.getElementById('scout-rankings-tbody');
    const scoutModalSectorSel = window.document.getElementById('scout-modal-sector-filter');
    const scoutModalLimitSel = window.document.getElementById('scout-modal-limit-select');
    const scoutModalCloseBtn = window.document.getElementById('close-scout-rankings-modal');

    openScoutModalBtn.click();
    const modalOpenedAll = scoutModalOverlay.classList.contains('active')
        && scoutModalTbody.querySelectorAll('tr[data-scout-rank-row]').length === 2
        && scoutModalTbody.querySelectorAll('.scout-ticker-row .colab-scout-promote-btn').length === 2
        && scoutModalTbody.innerHTML.includes('SMCI')
        && scoutModalTbody.innerHTML.includes('REGN')
        && scoutModalTbody.innerHTML.includes('#1')
        && !scoutModalTbody.innerHTML.includes('<script>evil()</script>');

    const regnWatchBtn = scoutModalTbody.querySelector('.scout-ticker-row .colab-scout-promote-btn[data-scout-ticker="REGN"]');
    if (regnWatchBtn) regnWatchBtn.click();
    await new Promise((r) => setTimeout(r, 40));
    const regnMarkedWatched = Boolean(
        scoutModalTbody.querySelector('.scout-ticker-row .colab-scout-promote-btn.is-watched[data-scout-ticker="REGN"]')
    );

    scoutModalSectorSel.value = 'Biotech';
    scoutModalSectorSel.dispatchEvent(new window.Event('change', { bubbles: true }));
    const filteredToBiotech = scoutModalTbody.querySelectorAll('tr[data-scout-rank-row]').length === 1
        && scoutModalTbody.innerHTML.includes('REGN')
        && !scoutModalTbody.innerHTML.includes('SMCI');

    scoutModalSectorSel.value = 'ALL';
    scoutModalLimitSel.value = '10';
    scoutModalSectorSel.dispatchEvent(new window.Event('change', { bubbles: true }));
    const resetSectorOk = scoutModalTbody.querySelectorAll('tr[data-scout-rank-row]').length === 2;

    scoutModalCloseBtn.click();
    const modalClosedOk = !scoutModalOverlay.classList.contains('active');
    check('Scout Recommendations Modal: ranks candidates with inline Watch toggle, sector filter, limit control, and clean close', () => {
        assert.strictEqual(modalOpenedAll, true, 'Expected Top Recommendations modal to open and render inline Watch buttons');
        assert.strictEqual(regnMarkedWatched, true, 'Clicking + Watch beside REGN should mark it ✓ Watching (.is-watched)');
        assert.strictEqual(filteredToBiotech, true, 'Expected Biotech sector filter to isolate REGN');
        assert.strictEqual(resetSectorOk, true, 'Expected ALL sector filter to restore both candidates');
        assert.strictEqual(modalClosedOk, true, 'Close button should dismiss #scout-rankings-modal-overlay');
    });

    const origQuantFetch = window.fetch;
    let analyticsPhase = 0;
    window.fetch = async (url, opts = {}) => {
        const pathOnly = String(url).split('?')[0];
        if (pathOnly === '/api/analytics') {
            analyticsPhase += 1;
            const body = analyticsPhase === 1
                ? {
                    regime: { volatility_state: 'BEAR_VOLATILE', strategy_permissibility: {} },
                    global_vetoes: [],
                    positions: [{ ticker: 'UMAC', price: null, tranches: {}, triggered_mandates: [] }],
                    watchlist_signals: [],
                }
                : {
                    regime: { volatility_state: 'BEAR_VOLATILE', strategy_permissibility: {} },
                    global_vetoes: [],
                    positions: [{ ticker: 'UMAC', price: 9.52, sweep_limit_usd: 9.47, tranches: { '25pct': { sell_shares: 465 }, '50pct': { sell_shares: 930 } }, triggered_mandates: [] }],
                    watchlist_signals: [],
                };
            return { ok: true, status: 200, json: async () => body };
        }
        if (pathOnly === '/api/data') {
            const baseData = JSON.parse(JSON.stringify(API['/api/data']));
            baseData.timestamp = '2026-11-27T10:35:00-05:00';
            baseData.tickers = [
                { ticker: 'UMAC', price: 9.52, price_validity: 'VALID', freshness: 'CURRENT', regular_close: 9.07, fib_forecast: {} },
                ...(baseData.tickers || []),
            ];
            return { ok: true, status: 200, json: async () => baseData };
        }
        return origQuantFetch(url, opts);
    };
    await window.eval('fetchAnalyticsAndBacktest()');
    const quantPanelEl = window.document.getElementById('analytics-quant-panel');
    const showedAwaitingInitially = Boolean(quantPanelEl && quantPanelEl.textContent.includes('awaiting live price'));
    await window.eval('pollData()');
    await new Promise((r) => setTimeout(r, 80));
    const clearedAwaitingAfterPoll = Boolean(
        quantPanelEl
        && !quantPanelEl.textContent.includes('awaiting live price')
        && quantPanelEl.textContent.includes('465sh')
        && quantPanelEl.textContent.includes('$9.47')
    );
    window.fetch = origQuantFetch;
    check('Quant Analytics auto-resync: pollData updates UMAC awaiting live price once /api/data receives VALID quote', () => {
        assert.strictEqual(showedAwaitingInitially, true, 'Expected initial unpriced /api/analytics to show awaiting live price');
        assert.strictEqual(clearedAwaitingAfterPoll, true, 'Expected pollData() with live UMAC quote to re-fetch /api/analytics and render 465sh / $9.47');
    });

    const sentProvSel = window.document.getElementById('sentinel-provider-select');
    const hasDeterministicOpt = Boolean(sentProvSel && [...sentProvSel.options].some((o) => o.value === 'deterministic'));
    window.renderSentinelStatus({
        enabled: true,
        provider: 'deterministic',
        interval_sec: 60,
        peak_predictor_enabled: true,
        api_key_configured: false,
        last_run: { status: 'OK', provider_used: 'deterministic', model_used: 'local-rules-v1', armed_count: 5 },
    });
    const sentMetaTxt = (window.document.getElementById('sentinel-status-text') || {}).textContent || '';
    check('Sentinel Deterministic mode: exposes deterministic option and renders 0-LLM status without key warning', () => {
        assert.strictEqual(hasDeterministicOpt, true, 'Expected #sentinel-provider-select to include deterministic option');
        assert.strictEqual(sentMetaTxt.includes('Local Deterministic') && sentMetaTxt.includes('5 armed'), true, `Unexpected status text: ${sentMetaTxt}`);
        assert.strictEqual(sentMetaTxt.includes('GEMINI_API_KEY'), false, 'Deterministic mode must never show missing API key error');
    });

    // ── UX Stage 1: data honesty, colspan, chat persistence, Esc on Execution Ticket ──
    const s1State = JSON.parse(JSON.stringify(API['/api/data']));
    // Backend emits net_gex_total: 0.0 when it has no GEX data for a ticker.
    s1State.tickers.find((t) => t.ticker === 'PLTR').net_gex_total = 0.0;
    s1State.local_storage_state.mutable_state.portfolio_snapshot = [
        { ticker: 'NVDA', shares: 10, wac: 100 },
        { ticker: 'ZZZ', shares: 5, wac: 42 }, // held but never scanned, no quote
    ];
    window.renderTable(s1State.tickers, s1State);
    const s1Rows = [...window.document.querySelectorAll('#data-table tbody tr')];
    const s1SymOf = (r) => (r.querySelector('.ticker-symbol') || r.cells[0]).textContent.trim();
    const zzz = s1Rows.find((r) => !r.matches('tr[data-section-row]') && s1SymOf(r) === 'ZZZ');
    const amdRow = s1Rows.find((r) => !r.matches('tr[data-section-row]') && s1SymOf(r) === 'AMD');
    check('Stage 1: unscanned held row shows — instead of fabricated 0 / 50.0 / EUR WAC as USD', () => {
        assert(zzz, 'held-but-unscanned ZZZ row missing');
        const cellTxt = (i) => zzz.cells[i].textContent.trim();
        assert.strictEqual(cellTxt(1), '—', `Price should be —, got ${cellTxt(1)}`);
        assert.strictEqual(cellTxt(2), '—', `Day % should be —, got ${cellTxt(2)}`);
        assert.strictEqual(cellTxt(4), '—', `Volume should be —, got ${cellTxt(4)}`);
        assert.strictEqual(cellTxt(7), '—', `RSI should be —, got ${cellTxt(7)}`);
        assert.strictEqual(cellTxt(8), '—', `VWAP should be —, got ${cellTxt(8)}`);
        assert.strictEqual(cellTxt(12), '—', `Dealer GEX should be —, got ${cellTxt(12)}`);
        assert(!zzz.textContent.includes('42.00'), 'EUR WAC leaked into a USD column');
        assert(!zzz.textContent.includes('50.0'), 'fabricated neutral RSI 50.0 rendered');
        assert(zzz.textContent.includes('Pending live quote'), 'missing pending-quote note');
        assert.strictEqual(zzz.querySelector('.score-badge').textContent.trim(), '—', 'score should be — when unscored');
        assert(amdRow && amdRow.cells[12].textContent.trim() === '—', 'scanned row without GEX must not show 0.000');
        const pltrRow = s1Rows.find((r) => !r.matches('tr[data-section-row]') && s1SymOf(r) === 'PLTR');
        assert(pltrRow && pltrRow.cells[12].textContent.trim() === '—', `backend 0.0 GEX placeholder must render —, got ${pltrRow && pltrRow.cells[12].textContent.trim()}`);
    });

    const s1SimpleBtn = window.document.getElementById('table-view-simple-btn');
    const s1ProBtn = window.document.getElementById('table-view-pro-btn');
    const spanOf = () => [...window.document.querySelectorAll('#data-table tbody tr[data-section-row] td')].map((td) => td.getAttribute('colspan'));
    if (s1SimpleBtn) s1SimpleBtn.click();
    const simpleSpans = spanOf();
    if (s1ProBtn) s1ProBtn.click();
    const proSpans = spanOf();
    if (s1SimpleBtn) s1SimpleBtn.click();
    check('Stage 1: section-row colspan matches visible columns (6 Simple / 14 Pro)', () => {
        assert(simpleSpans.length > 0, 'no section rows rendered');
        assert(simpleSpans.every((s) => s === '6'), `Simple spans: ${simpleSpans}`);
        assert(proSpans.every((s) => s === '14'), `Pro spans: ${proSpans}`);
    });

    window.renderPortfolio({
        portfolio: [{ ticker: 'NVDA', shares: 10, wac: 100.0 }, { ticker: 'ZZZ', shares: 5, wac: 42.0 }],
        unallocated_cash_eur: 1000.0, eurusd_rate: 1.0977,
    });
    const s1TotalUsd = window.document.getElementById('total-val-usd');
    check('Stage 1: portfolio USD total excludes unpriced holdings instead of adding EUR WAC', () => {
        // 1000 EUR * 1.0977 + 10 * 120.50 = 2302.70 -> $2,303 (adding 5 * 42 EUR WAC would give $2,513)
        assert(s1TotalUsd && s1TotalUsd.textContent.includes('$2,303'), `unexpected total: ${s1TotalUsd && s1TotalUsd.textContent}`);
        assert(s1TotalUsd.textContent.includes('*'), 'unpriced marker missing');
        assert((s1TotalUsd.getAttribute('title') || '').includes('Excludes 1 holding'), 'unpriced tooltip missing');
    });
    window.renderPortfolio(API['/api/basket']);

    const s1CashInput = window.document.getElementById('cash-input-eur');
    const s1CashUsd = window.document.getElementById('cash-val-usd');
    const savedCash = s1CashInput ? s1CashInput.value : '';
    const savedCashUsd = s1CashUsd ? s1CashUsd.textContent : '';
    if (s1CashInput) s1CashInput.value = '';
    if (s1CashUsd) s1CashUsd.textContent = '—';
    window.renderPortfolioCommandStrip({ positions: [], global_vetoes: [] });
    const s1CmdCash = (window.document.getElementById('cmd-cash-val') || {}).textContent || '';
    if (s1CashInput) s1CashInput.value = savedCash;
    if (s1CashUsd) s1CashUsd.textContent = savedCashUsd;
    check('Stage 1: Command Strip cash shows — (not €0.00) before cash hydrates', () => {
        assert.strictEqual(s1CmdCash.trim(), '—', `got ${s1CmdCash}`);
    });

    const chat = window.chatUI;
    const ls = window.localStorage;
    const ss = window.sessionStorage;
    const convo = [{ role: 'user', text: 'Review NVDA' }, { role: 'ai', text: 'NVDA briefing body text' }];
    ls.setItem('gemini_chat_sessions', '[]');
    ls.setItem('gem_chat_history', JSON.stringify(convo));
    ss.removeItem('gem_chat_tab_active');
    chat.loadHistory(); // new browser session
    const archivedAfterNew = JSON.parse(ls.getItem('gemini_chat_sessions') || '[]');
    const domAfterNew = chat.messages.textContent;
    ls.setItem('gem_chat_history', JSON.stringify(convo));
    chat.loadHistory(); // same-tab reload (flag now set)
    const domAfterReload = chat.messages.textContent;
    const archivedAfterReload = JSON.parse(ls.getItem('gemini_chat_sessions') || '[]');
    ss.removeItem('gem_chat_tab_active');
    ls.setItem('gem_chat_history', JSON.stringify([{ role: 'ai', text: 'Council standing by.' }]));
    chat.loadHistory(); // new session with only the standby greeting
    const archivedAfterStandby = JSON.parse(ls.getItem('gemini_chat_sessions') || '[]');
    check('Stage 1: chat restores on same-tab reload, archives + starts clean on a new session', () => {
        assert.strictEqual(archivedAfterNew.length, 1, 'new session should archive the previous conversation');
        assert.strictEqual(archivedAfterNew[0].preview, 'Review NVDA');
        assert(!domAfterNew.includes('NVDA briefing body text'), 'new session should start clean');
        assert(domAfterReload.includes('NVDA briefing body text'), 'same-tab reload should restore the conversation');
        assert.strictEqual(archivedAfterReload.length, 1, 'reload must not archive a duplicate');
        assert.strictEqual(archivedAfterStandby.length, 1, 'standby-only transcript must not be archived');
    });

    const s1Exec = window.document.getElementById('execution-plan-modal-overlay');
    const s1Chart = window.document.getElementById('chart-modal-overlay');
    s1Chart.classList.add('active');
    s1Exec.classList.add('active');
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    const execClosed = !s1Exec.classList.contains('active');
    const chartStillOpen = s1Chart.classList.contains('active');
    s1Chart.classList.remove('active');
    check('Stage 1: Escape closes the Execution Ticket only, leaving the chart it was opened from', () => {
        assert.strictEqual(s1Exec.getAttribute('role'), 'dialog');
        assert.strictEqual(execClosed, true, 'Esc should close the Execution Ticket');
        assert.strictEqual(chartStillOpen, true, 'Esc on the ticket must not also close the chart');
    });

    // ── UX Stage 2: table comprehension & plain-English labels ──
    const s2State = JSON.parse(JSON.stringify(API['/api/data']));
    window.renderTable(s2State.tickers, s2State);
    const s2Ths = [...window.document.querySelectorAll('#data-table thead th')];
    const s2RowOf = (sym) => [...window.document.querySelectorAll('#data-table tbody tr')]
        .find((r) => !r.matches('tr[data-section-row]') && (r.querySelector('.ticker-symbol') || r.cells[0]).textContent.trim() === sym);
    const s2Nvda = s2RowOf('NVDA');
    check('Stage 2: 14 columns preserved, Dealer (GEX) label, $ prices and ▲/▼ direction cues', () => {
        assert.strictEqual(s2Ths.length, 14, 'must keep all 14 columns');
        assert.strictEqual(window.document.querySelectorAll('#data-table thead th.num-col').length, 8);
        assert(s2Ths[12].textContent.includes('Dealer (GEX)'), `col 13 header: ${s2Ths[12].textContent}`);
        ['macd_hist', 'target_distance', 'dealer_gex'].forEach((k) => {
            assert(window.document.querySelector(`#data-table thead th[data-sort-key="${k}"]`), `${k} should be sortable`);
        });
        assert(s2Nvda, 'NVDA row missing');
        assert.strictEqual(s2Nvda.cells[1].textContent.trim(), '$120.50');
        assert(s2Nvda.cells[2].textContent.includes('▲') && s2Nvda.cells[2].textContent.includes('+1.20%'), `Day %: ${s2Nvda.cells[2].textContent}`);
        assert(s2Nvda.cells[8].textContent.includes('▲') && s2Nvda.cells[8].textContent.includes('$119.00'), `VWAP: ${s2Nvda.cells[8].textContent}`);
        assert(!s2Nvda.cells[11].textContent.includes('🎯'), 'Target cell should drop decorative emoji');
    });

    check('Stage 2: derived sort keys match what the cells display', () => {
        const sv = window._sortValueFor;
        assert.strictEqual(sv({ fib_forecast: { next_resistance: 10, distance_to_next_pct: 1.5 } }, 'target_distance'), 1.5);
        assert.strictEqual(sv({ fib_forecast: { next_resistance: 0, distance_to_next_pct: 1.5 } }, 'target_distance'), null);
        assert.strictEqual(sv({ ticker: 'X1', net_gex_total: 0.32 }, 'dealer_gex'), 0.32);
        assert.strictEqual(sv({ ticker: 'X1', net_gex_total: 0.0 }, 'dealer_gex'), null);
        assert.strictEqual(sv({ volume: 0, rvol: 1.0 }, 'rvol'), null);
        assert.strictEqual(sv({ macd_hist: -0.4 }, 'macd_hist'), -0.4);
    });

    const s2WatchPill = window.document.querySelector('.table-filter-pill[data-table-filter="watchlist"]');
    if (s2WatchPill) s2WatchPill.click();
    const s2WatchSections = [...window.document.querySelectorAll('#data-table tbody tr[data-section-row]')].map((r) => r.textContent.trim());
    const s2WatchHasHeld = Boolean(s2RowOf('NVDA'));
    const s2AllPill = window.document.querySelector('.table-filter-pill[data-table-filter="all"]');
    if (s2AllPill) s2AllPill.click();
    check('Stage 2: Watchlist filter pill shows only watchlist tickers', () => {
        assert(s2WatchPill, 'Watchlist pill missing');
        assert.deepStrictEqual(s2WatchSections, ['Strategic Watchlist']);
        assert.strictEqual(s2WatchHasHeld, false, 'held NVDA must be hidden in Watchlist filter');
    });

    const s2GuideBtn = window.document.getElementById('metric-guide-btn');
    const s2GuidePop = window.document.getElementById('metric-guide-popover');
    s2GuideBtn.click();
    const s2GuideOpened = !s2GuidePop.hidden && s2GuideBtn.getAttribute('aria-expanded') === 'true';
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    check('Stage 2: Metric guide explains GEX / rVol / VWAP / ATR % / RSI and closes on Esc', () => {
        assert.strictEqual(s2GuideOpened, true, 'guide should open on click');
        assert.strictEqual(s2GuidePop.hidden, true, 'guide should close on Esc');
        const txt = s2GuidePop.textContent;
        ['Dealer (GEX)', 'rVol', 'VWAP', 'ATR %', 'RSI', 'Signal'].forEach((k) => assert(txt.includes(k), `guide missing ${k}`));
    });

    const s2ExecBody = window.document.getElementById('exec-modal-body');
    const s2SentinelSel = window.document.getElementById('sentinel-provider-select');
    check('Stage 2: plain-English Execution Ticket, advisory note, and tucked-away engine settings', () => {
        const t = s2ExecBody.textContent;
        assert(t.includes('Protective stop at'), 'plain-English summary missing');
        assert(t.includes('Advisory plan only'), 'advisory-only note missing');
        assert(t.includes('Trailing stop protection') && t.includes('Cash balance check'), 'plain-English headings missing');
        assert(!/\b(?:ENH|MANDATE)_\d+/.test(t), 'rule IDs must not appear in the Execution Ticket');
        assert(!t.includes('Trailing Stop Audit'), 'old compliance-style heading still present');
        assert(s2SentinelSel && s2SentinelSel.closest('details.tech-settings'), 'Sentinel provider should sit inside Engine settings');
    });

    // Rule IDs are internal (LLM / rule engine). Nothing outside the AI chat transcript
    // (which is model prose) may show them — neither visible text nor tooltips.
    const ruleIdRe = /\b(?:ENH|MANDATE)_\d+/;
    const leakedText = [];
    const walker = window.document.createTreeWalker(window.document.body, 4 /* SHOW_TEXT */);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const el = n.parentElement;
        if (!el || el.closest('#chat-messages, script, style')) continue;
        if (ruleIdRe.test(n.nodeValue)) leakedText.push(`${el.tagName}#${el.id || ''}.${el.className || ''}: ${n.nodeValue.trim().slice(0, 80)}`);
    }
    const leakedTitles = [...window.document.querySelectorAll('[title]')]
        .filter((el) => !el.closest('#chat-messages') && ruleIdRe.test(el.getAttribute('title')))
        .map((el) => `${el.tagName}#${el.id || ''}: ${el.getAttribute('title').slice(0, 80)}`);
    check('Stage 2: no internal rule IDs (ENH_* / MANDATE_*) anywhere in the dashboard UI', () => {
        assert.deepStrictEqual(leakedText, [], `rule IDs in text: ${leakedText.join(' || ')}`);
        assert.deepStrictEqual(leakedTitles, [], `rule IDs in tooltips: ${leakedTitles.join(' || ')}`);
    });

    // ── UX Stage 3: workspace views, positions table, settings popover ──
    window.renderPositionsTable([
        { ticker: 'NVDA', shares: 10, wac: 100.0, price: 120.5, market_value_usd: 1205.0, unrealized_pnl_usd: 107.2, unrealized_pnl_pct: 9.76 },
        { ticker: 'ZZZ', shares: 5, wac: 42.0, price: null, market_value_usd: null, unrealized_pnl_usd: null, unrealized_pnl_pct: null },
    ]);
    const s3Rows = [...window.document.querySelectorAll('#portfolio-positions-body tr')];
    const s3Cells = (i) => [...s3Rows[i].cells].map((c) => c.textContent.trim());
    const s3PortfolioBadge = window.document.getElementById('nav-badge-portfolio');
    check('Stage 3: Positions table shows shares, € cost, $ price/value, P&L and weight; unpriced rows show —', () => {
        assert.strictEqual(s3Rows.length, 2);
        const nvda = s3Cells(0);
        assert.deepStrictEqual(nvda.slice(0, 5), ['NVDA', '10', '€100.00', '$120.50', '$1,205.00']);
        assert(nvda[5].includes('▲ +$107.20') && nvda[5].includes('(+9.8%)'), `P&L cell: ${nvda[5]}`);
        assert.strictEqual(nvda[6], '100.0%', 'weight uses priced holdings only');
        const zzz = s3Cells(1);
        assert.deepStrictEqual(zzz.slice(3, 7), ['—', '—', '—', '—'], `unpriced row: ${zzz}`);
        assert.strictEqual(zzz[2], '€42.00', 'EUR cost stays labelled as EUR');
        assert(s3PortfolioBadge && !s3PortfolioBadge.hidden && s3PortfolioBadge.textContent === '2', 'Portfolio nav badge should count holdings');
    });

    const s3SettingsBtn = window.document.getElementById('app-settings-btn');
    const s3SettingsPop = window.document.getElementById('app-settings-popover');
    s3SettingsBtn.click();
    const s3SettingsOpened = !s3SettingsPop.hidden && s3SettingsBtn.getAttribute('aria-expanded') === 'true';
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    const s3SettingsClosed = s3SettingsPop.hidden;
    window._briefActionClick('view', 'portfolio');
    const s3BriefToPortfolio = !window.document.getElementById('view-portfolio').hidden;
    window._briefActionClick('colab', '');
    const s3BriefToResearch = !window.document.getElementById('view-research').hidden;
    window.setWorkspaceView('today');
    check('Stage 3: Settings popover opens/closes (Esc) and Today\'s Brief actions jump to the right view', () => {
        assert.strictEqual(s3SettingsOpened, true, 'settings should open on click');
        assert.strictEqual(s3SettingsClosed, true, 'settings should close on Esc');
        assert.strictEqual(s3BriefToPortfolio, true, '"Review risk" should open the Portfolio view');
        assert.strictEqual(s3BriefToResearch, true, 'cloud-research actions should open the Research view');
    });

    // ── UX Stage 4: type scale, motion, palette, chat co-pilot, accessible dialogs ──
    const s4Css = read(path.join(staticDir, 'styles.css')) + '\n' + read(path.join(staticDir, 'modern_ui.css'));
    const s4Markup = ['index.html', 'app.js', 'modern_ui.js', 'portfolio.js'].map((f) => read(path.join(staticDir, f))).join('\n');
    const tinyRem = [...(s4Css + s4Markup).matchAll(/font-size:\s*(0?\.\d+)rem/g)].map((m) => m[0]).filter((d) => parseFloat(d.split(':')[1]) < 0.75);
    const tinyPx = [...(s4Css + s4Markup).matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)].map((m) => m[0]).filter((d) => parseFloat(d.split(':')[1]) < 12);
    check('Stage 4: 12px type floor, no "transition: all", global reduced-motion, single palette', () => {
        assert.deepStrictEqual(tinyRem, [], `sub-12px rem font sizes remain: ${tinyRem.slice(0, 8).join(', ')}`);
        assert.deepStrictEqual(tinyPx, [], `sub-12px px font sizes remain: ${tinyPx.slice(0, 8).join(', ')}`);
        assert(/--text-xs:\s*12px/.test(s4Css) && /--text-sm:\s*13px/.test(s4Css) && /--text-body:\s*15px/.test(s4Css), 'type-scale tokens missing');
        assert(!/transition:\s*all\b/.test(s4Css), 'transition: all must be replaced with explicit properties');
        assert(/@media \(prefers-reduced-motion: reduce\)\s*\{\s*\*, \*::before, \*::after/.test(s4Css), 'global reduced-motion rule missing');
        ['#131722', '#089981', '#f23645', '#bc85ff', '#8b5cf6', '#6e7681'].forEach((c) => {
            assert(!(s4Css + s4Markup).includes(c), `stray off-palette colour ${c}`);
        });
    });
    check('Stage 4: no native alert()/confirm() popups in dashboard scripts', () => {
        ['app.js', 'modern_ui.js', 'portfolio.js'].forEach((f) => {
            const src = read(path.join(staticDir, f));
            assert(!/(^|[^.\w])(alert|confirm)\(/m.test(src.replace(/window\.gem(Confirm|Notice)/g, '')), `${f} still calls alert()/confirm()`);
        });
    });

    const s4Chat = window.document.getElementById('chat-window');
    const s4Expand = window.document.getElementById('chat-expand-btn');
    window.chatUI.setExpanded(false);
    window.chatUI.show();
    const s4DockedOk = s4Chat.classList.contains('active') && !s4Chat.classList.contains('is-expanded')
        && s4Chat.getAttribute('aria-modal') === 'false';
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: '2', bubbles: true }));
    const s4ViewSwitchWhileDocked = !window.document.getElementById('view-portfolio').hidden;
    window.setWorkspaceView('today');
    s4Expand.click();
    const s4ExpandedOk = s4Chat.classList.contains('is-expanded') && s4Chat.getAttribute('aria-modal') === 'true'
        && s4Expand.getAttribute('aria-pressed') === 'true';
    s4Expand.click();
    const s4ReDocked = !s4Chat.classList.contains('is-expanded');
    window.chatUI.hide();
    const s4PaidSwitch = window.document.getElementById('settings-paid-toggle');
    check('Stage 4: AI Council docks beside the dashboard (non-modal) and expands on demand', () => {
        assert.strictEqual(s4DockedOk, true, 'chat should open docked and non-modal');
        assert.strictEqual(s4ViewSwitchWhileDocked, true, 'dashboard stays usable while the chat is docked');
        assert.strictEqual(s4ExpandedOk, true, 'Expand should switch to the modal full-screen view');
        assert.strictEqual(s4ReDocked, true, 'Dock should return to the side panel');
        assert(!window.document.getElementById('settings-caching-toggle'), 'retired Context Caching switch should be removed');
        assert(s4PaidSwitch && s4PaidSwitch.getAttribute('role') === 'switch' && s4PaidSwitch.tabIndex === 0, 'settings toggles must be keyboard switches');
        assert(['true', 'false'].includes(s4PaidSwitch.getAttribute('aria-checked')), 'switch must expose aria-checked');
    });

    const s4ConfirmCancel = window.gemConfirm('Delete it?', { confirmLabel: 'Delete', danger: true });
    const s4DialogOpen = window.document.querySelector('.gem-dialog-overlay[role="alertdialog"]');
    const s4FocusInDialog = Boolean(s4DialogOpen && s4DialogOpen.contains(window.document.activeElement));
    window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    const s4CancelResult = await s4ConfirmCancel;
    const s4ConfirmOk = window.gemConfirm('Proceed?');
    window.document.querySelector('.gem-dialog-confirm').click();
    const s4OkResult = await s4ConfirmOk;
    const s4Modals = [...window.document.querySelectorAll('.modal-overlay')];
    const s4Unlabelled = s4Modals.filter((m) => !['dialog', 'alertdialog'].includes(m.getAttribute('role'))).map((m) => m.id);
    const s4SortTh = window.document.querySelector('#data-table thead th[data-sort-key="price"]');
    s4SortTh.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    const s4SortedByKey = s4SortTh.getAttribute('aria-sort') === 'descending';
    s4SortTh.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    s4SortTh.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    check('Stage 4: accessible confirm dialog, dialog roles on every modal, keyboard-sortable headers', () => {
        assert(s4DialogOpen, 'gemConfirm should render an alertdialog');
        assert.strictEqual(s4FocusInDialog, true, 'focus should move into the dialog');
        assert.strictEqual(s4CancelResult, false, 'Esc should cancel');
        assert.strictEqual(s4OkResult, true, 'Confirm should resolve true');
        assert(!window.document.querySelector('.gem-dialog-overlay'), 'dialog should be removed after closing');
        assert.deepStrictEqual(s4Unlabelled, [], `modals without role=dialog: ${s4Unlabelled}`);
        assert.strictEqual(s4SortTh.tabIndex, 0, 'sortable headers must be focusable');
        assert.strictEqual(s4SortedByKey, true, 'Enter on a sortable header should sort');
    });

    window.close();
    console.log(results.join('\n'));
    const failed = results.filter((r) => r.startsWith('FAIL')).length;
    console.log(failed ? `${failed} FAILED` : 'ALL PASSED');
    if (errors.length) console.log('Captured errors:\n' + errors.join('\n'));
    process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
