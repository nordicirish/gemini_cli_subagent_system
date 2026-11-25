const API_BASE = '/api';

// DOM Elements
const dTableBody = document.getElementById('table-body');
const dStatus = document.getElementById('market-status');
const dUpdated = document.getElementById('last-updated');
const dIndicator = document.getElementById('live-indicator');

// Progress Bar DOM Elements
const dProgressContainer = document.getElementById('initial-fetch-progress-container');
const dProgressPhase = document.getElementById('progress-phase-title');
const dProgressStatus = document.getElementById('progress-status-text');
const dProgressBar = document.getElementById('initial-fetch-progress-bar');
const dProgressPercent = document.getElementById('progress-bar-percent');
const dProgressDetails = document.getElementById('progress-ticker-details');
const dTableContainer = document.getElementById('main-data-table-container');


const dIndicesInput = document.getElementById('indices-input');
const dUpdateIndicesBtn = document.getElementById('update-indices-btn');
const dIStatus = document.getElementById('indices-status');
const dDynamicMacroCards = document.getElementById('dynamic-macro-cards');

let currentMacroTickers = [];
let MACRO_LABELS = {
    '^VIX': 'VOLATILITY',
    '^VVIX': '^VVIX',
    'VIXY': 'SHORT-TERM VIX',
    'IEF': 'TREASURY BOND',
    'UUP': 'US DOLLAR',
    'SPY': 'S&P 500',
    'QQQ': 'NASDAQ 100',
    'GDX': 'GOLD MINERS'
};
const MACRO_SUBTITLES = {
    '^VIX': 'Fear Index',
    '^VVIX': 'Vol of Vol',
    'VIXY': 'Short VIX',
    'IEF': '7-10Y Bond',
    'UUP': 'US Dollar',
    'SPY': 'S&P 500',
    'QQQ': 'Nasdaq 100',
    'IWM': 'Russell 2000',
    'DIA': 'Dow 30',
    'GDX': 'Gold Miners'
};
let latestTickersMap = {};

function updateVolatilityRegimePill(state) {
    const pill = document.getElementById('volatility-regime-pill');
    if (!pill) return;
    const tickers = Array.isArray(state && state.tickers) ? state.tickers : [];
    const regimeSample = tickers.find(t => t && t.volatility_regime) || null;
    const analyticsRegime =
        (state && state.precomputed_analytics && state.precomputed_analytics.regime && state.precomputed_analytics.regime.volatility_state) ||
        (_lastQuantAnalytics && _lastQuantAnalytics.regime && _lastQuantAnalytics.regime.volatility_state) ||
        '';
    let rawRegime = String((state && state.volatility_regime) || (regimeSample && regimeSample.volatility_regime) || '').trim();
    if (!rawRegime || /^UNAVAILABLE$|^UNKNOWN$/i.test(rawRegime)) {
        rawRegime = String(analyticsRegime || rawRegime).trim();
    }
    const upper = rawRegime.toUpperCase();

    let cls = 'regime-unknown';
    if (/CRISIS|EXTREME|PANIC|STRESS|HIGH|BEAR_VOLATILE/.test(upper)) {
        cls = 'regime-crisis';
    } else if (/ELEVATED|CAUTION|MODERATE|GUARDED|BEAR_QUIET|BULL_VOLATILE/.test(upper)) {
        cls = 'regime-elevated';
    } else if (/NORMAL|LOW|CALM|STABLE|BULL_QUIET/.test(upper)) {
        cls = 'regime-normal';
    }

    pill.textContent = upper || '—';
    pill.className = `regime-pill ${cls}`;
    const vixVal = regimeSample && Number.isFinite(Number(regimeSample.vix_price))
        ? `VIX: ${Number(regimeSample.vix_price).toFixed(2)}`
        : '';
    const vixyRoc = regimeSample && Number.isFinite(Number(regimeSample.vixy_roc))
        ? `VIXY RoC: ${Number(regimeSample.vixy_roc).toFixed(2)}%`
        : '';
    const tooltip = ['Volatility Regime', rawRegime || 'Pending', vixVal, vixyRoc].filter(Boolean).join(' · ');
    pill.title = tooltip;
}

function setSidebarCollapsed(collapsed) {
    const main = document.getElementById('main-content');
    const btn = document.getElementById('toggle-sidebar-btn');
    if (main) {
        main.classList.toggle('sidebar-collapsed', Boolean(collapsed));
    }
    if (btn) {
        btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        btn.classList.toggle('active', !collapsed);
    }
    try {
        localStorage.setItem('gem_sidebar_open', collapsed ? 'false' : 'true');
    } catch (_) {}
}

function toggleSidebarPanel() {
    const main = document.getElementById('main-content');
    const isCollapsed = main ? main.classList.contains('sidebar-collapsed') : true;
    setSidebarCollapsed(!isCollapsed);
}

function initSidebarToggle() {
    const btn = document.getElementById('toggle-sidebar-btn');
    let savedOpen = false;
    try {
        savedOpen = localStorage.getItem('gem_sidebar_open') === 'true';
    } catch (_) {}
    setSidebarCollapsed(!savedOpen);
    if (btn && !btn._gemSidebarBound) {
        btn.addEventListener('click', toggleSidebarPanel);
        btn._gemSidebarBound = true;

        // Overlay-drawer mode (below the docking breakpoint): Escape or a click outside closes it.
        const isOverlayOpen = () => {
            const main = document.getElementById('main-content');
            const sidebar = document.getElementById('dashboard-sidebar');
            return Boolean(main && sidebar && !main.classList.contains('sidebar-collapsed')
                && getComputedStyle(sidebar).position === 'fixed');
        };
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && isOverlayOpen() && !document.querySelector('.modal-overlay.active, .chart-modal-overlay.active')) {
                setSidebarCollapsed(true);
                btn.focus();
            }
        });
        document.addEventListener('mousedown', (e) => {
            const sidebar = document.getElementById('dashboard-sidebar');
            if (!isOverlayOpen() || !sidebar || sidebar.contains(e.target) || btn.contains(e.target)) return;
            // Clicks inside dialogs opened from the drawer must not close it.
            if (e.target.closest && e.target.closest('[role="dialog"], .modal-overlay, .chart-modal-overlay')) return;
            setSidebarCollapsed(true);
        });
    }
}

const dCopyBtn = document.getElementById('copy-json-btn');
const dCopySessionBtn = document.getElementById('copy-session-btn');
const dNewsScanBtn = document.getElementById('btn-news-scan');
const dPasteBtn = document.getElementById('paste-payload-btn');
const dMobileStatus = document.getElementById('mobile-data-status');

// Manager Elements
const dWatchlistContainer = document.getElementById('watchlist-manager-container');
const dAddWatchlistTicker = document.getElementById('add-watchlist-ticker');
const dAddToWatchlistBtn = document.getElementById('add-to-watchlist-btn');

// Scout Elements
const dScoutContainer = document.getElementById('scout-categories-container');
const dAddScoutCategory = document.getElementById('add-scout-category');
const dAddScoutCategoryBtn = document.getElementById('add-scout-category-btn');
const dSaveScoutCategoriesBtn = document.getElementById('save-scout-categories-btn');
const dRunAiScoutBtn = document.getElementById('run-ai-scout-btn');
const dAiScoutLimitSelect = document.getElementById('scout-limit-select');
const dAiScoutMaxRsiSelect = document.getElementById('scout-max-rsi-select');
const dAiScoutStatus = document.getElementById('ai-scout-status');



// Helper for consistent UI feedback on copy/paste actions
function showFeedback(btn, btnText, statusMsg, isError = false, statusEl = null) {
    if (btn && btn.dataset && btn.dataset.isFeedback === "true") return; // Prevent re-triggering during active feedback
    
    // Use per-button inline feedback if available, otherwise fallback
    const target = statusEl || dDataStatus;
    if (target) {
        target.textContent = statusMsg;
        target.className = `status-message inline-feedback active ${isError ? 'text-red' : 'text-green'}`;
    }

    if (!btn) {
        if (target) {
            setTimeout(() => {
                target.textContent = '';
                target.className = 'status-message inline-feedback';
            }, 2500);
        }
        return;
    }

    btn.dataset.isFeedback = "true";
    const originalHtml = btn.innerHTML;
    btn.innerHTML = btnText;
    btn.classList.add(isError ? 'btn-error' : 'btn-success');
    
    setTimeout(() => {
        btn.innerHTML = originalHtml;
        btn.classList.remove('btn-error', 'btn-success');
        if (target) {
            target.textContent = '';
            target.className = 'status-message inline-feedback';
        }
        btn.dataset.isFeedback = "false";
    }, 2500);
}



const dIndicesModalOverlay = document.getElementById('indices-modal-overlay');
const dOpenIndicesBtn = document.getElementById('open-indices-modal-btn');
const dCloseIndicesBtn = document.getElementById('close-indices-modal');
const dIndicesGrid = document.getElementById('indices-grid');

// Store latest macro data for indices modal
let latestMacroData = {};

// Generic modal helpers
function openModal(overlay) { overlay.classList.add('active'); }
function closeModal(overlay) { overlay.classList.remove('active'); }



// Indices Modal
dOpenIndicesBtn.addEventListener('click', async () => {
    await fetchTickers();
    renderIndicesModal();
    openModal(dIndicesModalOverlay);
});
dCloseIndicesBtn.addEventListener('click', () => closeModal(dIndicesModalOverlay));
dIndicesModalOverlay.addEventListener('click', (e) => {
    if (e.target === dIndicesModalOverlay) closeModal(dIndicesModalOverlay);
});

// Escape to close any open modal
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeModal(dIndicesModalOverlay);
    }
});

// Render Indices Modal content
function renderIndicesModal() {
    if (!currentMacroTickers || currentMacroTickers.length === 0) {
        currentMacroTickers = ['^VIX', '^VVIX', 'VIXY', 'IEF', 'UUP', 'SPY', 'GDX'];
    }
    const html = currentMacroTickers.map(ticker => {
        const d = latestMacroData[ticker] || latestTickersMap[ticker.toUpperCase()] || (latestMacroData && latestMacroData[ticker.replace('^', '')]);
        const label = MACRO_LABELS[ticker] || ticker;
        const title = `${label} (${ticker})`;
        
        if (!d) return `
            <div class="index-card">
                <div class="index-name">${title}</div>
                <div class="index-price text-muted">—</div>
                <div class="index-gap text-muted">Awaiting data</div>
            </div>`;

        const changeVal = d.session_change_pct || d.gap_percent || 0;
        const changeStr = (changeVal > 0 ? '+' : '') + changeVal.toFixed(2) + '%';
        const changeColor = changeVal >= 0 ? 'text-green' : 'text-red';

        let detailsArr = [];
        if (d.rsi !== undefined && d.rsi !== null) detailsArr.push(`RSI ${d.rsi.toFixed(1)}`);
        if (d.atr_percent !== undefined && d.atr_percent !== null && d.atr_percent > 0) detailsArr.push(`ATR ${d.atr_percent.toFixed(2)}%`);
        if (d.volume && d.volume > 0) detailsArr.push(`Vol ${formatVol(d.volume)}`);
        if (d.vwap && d.vwap > 0) detailsArr.push(`VWAP ${d.vwap.toFixed(2)}`);
        if (d.net_gex_total !== undefined && d.net_gex_total !== 0 && !isNaN(d.net_gex_total)) detailsArr.push(`GEX ${d.net_gex_total.toFixed(3)}`);

        let trendStr = '';
        if (d.trend === 'UP') trendStr = ' · ▲ Up';
        else if (d.trend === 'DOWN') trendStr = ' · ▼ Down';
        else trendStr = ' · — Flat';

        return `
            <div class="index-card">
                <div class="index-name">${title}</div>
                <div class="index-price">${(d.price || 0).toFixed(2)}</div>
                <div class="index-gap ${changeColor}">${changeStr}${trendStr}</div>
                <div class="index-details">${detailsArr.join(' · ')}</div>
            </div>`;
    }).join('');

    dIndicesGrid.innerHTML = html;
}

// Cache previous state to flash updates
let prevPrices = {};
let prevGex = {};

// Initialization
async function init() {
    await fetchTickers();
    await fetchPortfolio();
    await fetchWatchlist();
    await fetchScoutCategories();
    await fetchScoutConfig();
    await fetchAlertsAndSentinel();
    await fetchAnalyticsAndBacktest();
    // Google Drive sync disabled in this repo
    pollData();
    setInterval(pollData, 3000); // 3 sec polling
}


// Fetch current ticker list
async function fetchTickers() {
    try {
        const res = await fetch(`${API_BASE}/tickers`);
        const data = await res.json();
        dIndicesInput.value = data.macro.join(', ');
        currentMacroTickers = data.macro;
        if (data.macro_labels) MACRO_LABELS = data.macro_labels;
    } catch (e) {
        console.error("Failed to fetch tickers", e);
    }
}



// Update indices list via POST
dUpdateIndicesBtn.addEventListener('click', async () => {
    const raw = dIndicesInput.value;
    const items = raw.split(/[\s,]+/).map(t => t.trim()).filter(t => t);
    
    dUpdateIndicesBtn.disabled = true;
    dUpdateIndicesBtn.textContent = 'Updating...';
    
    try {
        const res = await fetch(`${API_BASE}/macro`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({macro: items})
        });
        const data = await res.json();
        if(data.status === 'success') {
            currentMacroTickers = data.macro;
            renderIndicesModal(); // update immediately
            pollData(); // Force global refresh
            dIStatus.textContent = 'Tracked indices updated successfully.';
            dIStatus.className = 'status-message text-green';
            setTimeout(() => { dIStatus.textContent = ''; }, 3000);
        }
    } catch (e) {
        console.error(e);
        dIStatus.textContent = 'Failed to update indices.';
        dIStatus.className = 'status-message text-red';
    } finally {
        dUpdateIndicesBtn.disabled = false;
        dUpdateIndicesBtn.textContent = 'Update Indices';
    }
});

// --- Action Handlers ---

async function copyMarketSnapshot(triggerBtn, statusEl) {
    try {
        let snapshotPrompt = "";
        try {
            const promptRes = await fetch(`${API_BASE}/prompts/snapshot`);
            const promptData = await promptRes.json();
            snapshotPrompt = promptData.prompt || "";
        } catch (pe) {
            console.warn("Failed to fetch dynamic snapshot prompt.");
        }
        if (!snapshotPrompt) {
            snapshotPrompt = [
                "SYSTEM DIRECTIVE: ROUTINE TURN EXECUTION",
                "",
                "1. Read the linked `local_ssot_shadow` document via your Gem_Store source to synchronize your local context.",
                "2. Evaluate current 'risk_regime' and 'dealer_posture' shifts.",
                "3. Route the data through the Consensus Pipeline (Data Analyst -> Council Debate -> Synthesis) for any required rebalancing, entries, or defensive trims.",
                "4. Conclude your turn by outputting the final EXECUTION_PAYLOAD."
            ].join("\n");
        }
        
        await navigator.clipboard.writeText(snapshotPrompt);
        showFeedback(triggerBtn, "✅ Copied!", "Market snapshot prompt ready! (Gem_Store holds SSoT context)", false, statusEl);
    } catch (e) {
        console.error(e);
        showFeedback(triggerBtn, "❌ Error", "Failed to copy snapshot.", true, statusEl);
    }
}

async function copySessionBoot(triggerBtn, statusEl) {
    try {
        let bootPrompt = "";
        try {
            const promptRes = await fetch(`${API_BASE}/prompts/boot`);
            const promptData = await promptRes.json();
            bootPrompt = promptData.prompt || "";
        } catch (pe) {
            console.warn("Failed to fetch dynamic boot prompt.");
        }
        if (!bootPrompt) bootPrompt = "SYSTEM BOOT: COUNCIL SESSION INITIALIZATION";
        
        await navigator.clipboard.writeText(bootPrompt);
        showFeedback(triggerBtn, "✅ Copied!", "Session boot prompt ready! (Gem_Store holds SSoT context)", false, statusEl);
    } catch (e) {
        console.error(e);
        showFeedback(triggerBtn, "❌ Error", "Failed to copy prompt", true, statusEl);
    }
}

async function copyNewsScan(triggerBtn, statusEl) {
    try {
        let newsScanPrompt = "";
        try {
            const promptRes = await fetch(`${API_BASE}/prompts/news_scan`);
            const promptData = await promptRes.json();
            newsScanPrompt = promptData.prompt || "";
        } catch (pe) {
            console.warn("Failed to fetch news scan prompt.");
        }
        if (!newsScanPrompt) newsScanPrompt = "SYSTEM DIRECTIVE: MACRO & STOCK NEWS SCAN";
        
        await navigator.clipboard.writeText(newsScanPrompt);
        showFeedback(triggerBtn, "✅ Copied!", "News scan prompt ready!", false, statusEl);
    } catch (e) {
        console.error(e);
        showFeedback(triggerBtn, "❌ Error", "Failed to copy news scan payload.", true, statusEl);
    }
}

async function ingestExecutionPayload(triggerBtn, statusEl) {
    triggerBtn.disabled = true;
    try {
        const text = await navigator.clipboard.readText();
        if (!text) throw new Error("Clipboard empty");
        
        const res = await fetch(`${API_BASE}/paste`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ payload: text })
        });
        
        const data = await res.json();
        if (data.status === 'success') {
            showFeedback(triggerBtn, "✅ Ingested!", "Payload ingested!", false, statusEl);
            pollData();
        } else {
            throw new Error(data.message);
        }
    } catch (e) {
        console.error("Paste Error: ", e);
        showFeedback(triggerBtn, "❌ Error", e.message || "Failed to ingest.", true, statusEl);
    } finally {
        triggerBtn.disabled = false;
    }
}

// --- Listeners ---
if (dCopyBtn) dCopyBtn.addEventListener('click', () => copyMarketSnapshot(dCopyBtn, document.getElementById('outbound-turn-status')));
if (dCopySessionBtn) dCopySessionBtn.addEventListener('click', () => copySessionBoot(dCopySessionBtn, document.getElementById('outbound-session-status')));
if (dNewsScanBtn) dNewsScanBtn.addEventListener('click', () => copyNewsScan(dNewsScanBtn, document.getElementById('outbound-newsscan-status')));
if (dPasteBtn) dPasteBtn.addEventListener('click', () => ingestExecutionPayload(dPasteBtn, document.getElementById('inbound-paste-status')));



// ... (cleaned up)

// Format large numbers (Volume)
function formatVol(vol) {
    if (vol === null || vol === undefined || !Number.isFinite(Number(vol))) return '—';
    if (vol >= 1000000) return (vol / 1000000).toFixed(2) + 'M';
    if (vol >= 1000) return (vol / 1000).toFixed(1) + 'K';
    return vol.toString();
}

// Render the data table with bifurcated sections
function renderTable(tickers, state) {
    if (!tickers || !tickers.length) return;

    // Get held tickers from portfolio snapshot across all SSOT schema keys
    const ssot = state.local_storage_state || {};
    const ms = ssot.mutable_state || {};
    const ep = ssot.EXECUTION_PAYLOAD || {};
    const portfolio = ms.portfolio_snapshot || ssot.portfolio_snapshot || ep.portfolio_snapshot || [];
    const heldTickers = new Set(portfolio.map(p => (p.ticker || '').toUpperCase()).filter(Boolean));

    // Get macro tickers to exclude
    const MACRO_TICKERS = currentMacroTickers && currentMacroTickers.length > 0 
        ? currentMacroTickers.map(t => t.toUpperCase())
        : ['^VIX', 'VIXY', 'UUP', 'IEF', 'SPY', 'QQQ', 'GDX'].map(t => t.toUpperCase());

    // Group tickers
    const groups = {
        held: [],
        watchlist: [],
        scouts: []
    };

    // Watchlist lives in the SSoT (mutable_state.watched_tickers), the same source as GET /api/watchlist.
    // /api/data has never published a top-level `watchlist`, so reading only state.watchlist left this
    // set empty and dropped every watchlist ticker from the table.
    const watchedRaw = [
        ...(Array.isArray(state.watchlist) ? state.watchlist : []),
        ...(Array.isArray(ms.watched_tickers) ? ms.watched_tickers : []),
        ...(Array.isArray(ssot.watched_tickers) ? ssot.watched_tickers : []),
    ];
    const userWatchlist = new Set(
        watchedRaw.filter(s => typeof s === 'string' && s.trim()).map(s => s.trim().toUpperCase())
    );
    const processedSyms = new Set();

    tickers.forEach(t => {
        const sym = t.ticker.toUpperCase();
        if (MACRO_TICKERS.includes(sym) || sym === 'EURUSD=X') return;
        processedSyms.add(sym);

        if (heldTickers.has(sym)) {
            groups.held.push(t);
        } else if (userWatchlist.has(sym)) {
            groups.watchlist.push(t);
        } else if (t._isScout) {
            groups.scouts.push(t);
        }
    });

    // Ensure all held portfolio items display on the dashboard even if initial background scan is still pending
    portfolio.forEach(p => {
        const sym = (p.ticker || '').toUpperCase();
        if (sym && !processedSyms.has(sym) && !MACRO_TICKERS.includes(sym)) {
            const livePrice = (latestTickersMap[sym] && latestTickersMap[sym].price) || p.wac || 0;
            groups.held.push({
                ticker: sym,
                price: livePrice,
                session_change_pct: 0,
                gap_percent: 0,
                volume: 0,
                rvol: 0,
                atr_percent: 0,
                rsi: 50.0,
                macd: 0.0,
                macd_signal: 0.0,
                macd_hist: 0.0,
                macd_slope: 'EXPANDING_POSITIVE',
                macd_status: 'NEUTRAL',
                vwap: p.wac || 0,
                trend: 'FLAT',
                net_gex_total: 0,
                score: 0,
                note: 'Holding'
            });
            processedSyms.add(sym);
        }
    });

    // Likewise show watchlist tickers that the background scan has not reached yet
    userWatchlist.forEach(sym => {
        if (!processedSyms.has(sym) && !heldTickers.has(sym) && !MACRO_TICKERS.includes(sym)) {
            groups.watchlist.push({
                ticker: sym,
                price: (latestTickersMap[sym] && latestTickersMap[sym].price) || null,
                session_change_pct: null,
                gap_percent: null,
                volume: 0,
                rvol: null,
                atr_percent: null,
                rsi: null,
                vwap: 0,
                trend: 'FLAT',
                score: 0,
                note: 'Pending scan'
            });
            processedSyms.add(sym);
        }
    });

    let html = '';

    // Numbers can be null while the background scan warms up (or for pending watchlist rows);
    // show "—" instead of throwing, which used to abort the whole table render.
    const isNum = (v) => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
    const fmt = (v, digits) => (isNum(v) ? Number(v).toFixed(digits) : '—');
    const fmtSigned = (v, digits) => (isNum(v) ? `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(digits)}%` : '—');

    const renderRowUnsafe = (row) => {
        const sym = row.ticker;
        const p = fmt(row.price, 2);

        let pClass = '';
        if (isNum(row.price)) {
            const pNum = Number(row.price);
            if (prevPrices[sym]) {
                if (pNum > prevPrices[sym]) pClass = 'flash-up';
                else if (pNum < prevPrices[sym]) pClass = 'flash-down';
            }
            prevPrices[sym] = pNum;
        }

        let gapColor = row.gap_percent > 0 ? 'text-green' : (row.gap_percent < 0 ? 'text-red' : 'text-white');
        let rsiColor = 'text-white';
        if (row.rsi >= 70) rsiColor = 'text-red';
        else if (row.rsi <= 30) rsiColor = 'text-green';

        let scoreStr = row.score > 0 ? `+${row.score}` : `${row.score}`;
        let scoreBadge = 'neutral';
        if (row.score >= 5) scoreBadge = 'positive';
        else if (row.score <= -5) scoreBadge = 'negative';
        
        const noteHtml = row.note ? `<span class="note-tag">${row.note}</span>` : '';
        
        const isMacroInv = ['^VIX', 'UUP', 'IEF'].includes(sym);
        let trendHtml = '';
        if(row.trend === 'UP') {
            const cls = isMacroInv ? 'up-inv' : 'up';
            trendHtml = `<span class="trend-tag ${cls}">▲ Up</span>`;
        } else if(row.trend === 'DOWN') {
            const cls = isMacroInv ? 'down-inv' : 'down';
            trendHtml = `<span class="trend-tag ${cls}">▼ Down</span>`;
        } else {
            trendHtml = `<span class="trend-tag flat">— Flat</span>`;
        }

        const dayColor = row.session_change_pct > 0 ? 'text-green' : row.session_change_pct < 0 ? 'text-red' : 'text-white';
        const openColor = row.change_from_open_pct > 0 ? 'text-green' : row.change_from_open_pct < 0 ? 'text-red' : 'text-white';
        // Note: gapColor is already declared at line 378

        // MACD Indicator
        let macdHtml = '';
        const macdHist = (row.macd_hist !== undefined && row.macd_hist !== null) ? Number(row.macd_hist) : null;
        const macdVal = (row.macd !== undefined && row.macd !== null) ? Number(row.macd) : null;
        const macdSig = (row.macd_signal !== undefined && row.macd_signal !== null) ? Number(row.macd_signal) : null;
        const macdSlope = row.macd_slope || '';
        const macdStat = (row.macd_status || '').toUpperCase();

        let macdClass = 'neutral';
        let macdLabel = '— Neutral';

        if (macdStat === 'BULLISH' || (!macdStat && macdHist !== null && macdHist > 0.001)) {
            macdClass = 'bullish';
            macdLabel = '▲ Bull';
        } else if (macdStat === 'BEARISH' || (!macdStat && macdHist !== null && macdHist < -0.001)) {
            macdClass = 'bearish';
            macdLabel = '▼ Bear';
        } else {
            macdClass = 'neutral';
            macdLabel = '— Neutral';
        }

        const tooltip = (macdVal !== null && macdSig !== null && macdHist !== null)
            ? `MACD: ${macdVal.toFixed(2)} | Signal: ${macdSig.toFixed(2)} | Hist: ${macdHist > 0 ? '+' : ''}${macdHist.toFixed(2)}${macdSlope ? ' (' + macdSlope.replace(/_/g, ' ') + ')' : ''}`
            : 'MACD: Calculating...';

        macdHtml = `<span class="macd-tag ${macdClass}" title="${tooltip}">${macdLabel}</span>`;

        // Fib Target Indicator
        let fibTargetHtml = '<span class="fib-target-tag none" title="Fib calculation pending">—</span>';
        if (row.fib_forecast && row.fib_forecast.next_resistance > 0) {
            const fib = row.fib_forecast;
            const nextPrice = Number(fib.next_resistance).toFixed(2);
            const dist = Number(fib.distance_to_next_pct || 0);
            const distStr = `${dist >= 0 ? '+' : ''}${dist.toFixed(1)}%`;
            const label = fib.next_resistance_label || '';
            const isRetracement = fib.mode === 'RETRACEMENT'
                || fib.daily_peak_status === 'PULLBACK'
                || fib.daily_peak_status === 'BELOW_VWAP';

            let tagClass = 't1';
            let tagPrefix = '🎯 T1';
            if (isRetracement) {
                tagClass = 'retracement';
                if (label.includes('HOD')) {
                    tagPrefix = '↩ HOD';
                } else if (label.includes('0.786')) {
                    tagPrefix = '↩ 0.786';
                } else if (label.includes('0.618')) {
                    tagPrefix = '↩ 0.618';
                } else if (label.includes('0.500')) {
                    tagPrefix = '↩ 0.50';
                } else if (label.includes('0.382')) {
                    tagPrefix = '↩ 0.382';
                } else if (label.includes('0.236')) {
                    tagPrefix = '↩ 0.236';
                } else {
                    tagPrefix = '↩ Res';
                }
            } else if (label.includes('HOD') || label.includes('Peak')) {
                tagClass = 'peak';
                tagPrefix = '🎯 HOD';
            } else if (label.includes('T3') || label.includes('2.618')) {
                tagClass = 't3';
                tagPrefix = '🎯 T3';
            } else if (label.includes('T2') || label.includes('1.618')) {
                tagClass = 't2';
                tagPrefix = '🎯 T2';
            } else if (label.includes('T1') || label.includes('1.000')) {
                tagClass = 't1';
                tagPrefix = '🎯 T1';
            } else if (label.includes('0.236') || label.includes('Early')) {
                tagClass = 'early';
                tagPrefix = '🎯 0.236';
            } else if (label.includes('0.382') || label.includes('Conservative')) {
                tagClass = 'conservative';
                tagPrefix = '🎯 0.382';
            } else if (label.includes('0.500') || label.includes('Mid')) {
                tagClass = 'mid';
                tagPrefix = '🎯 0.500';
            } else if (label.includes('0.618') || label.includes('Golden')) {
                tagClass = 'golden';
                tagPrefix = '🎯 0.618';
            } else {
                tagClass = 't1';
                tagPrefix = '🎯 Fib';
            }

            const atrCapStr = fib.daily_peak_atr ? `$${Number(fib.daily_peak_atr).toFixed(2)}` : '—';
            const peakCapStr = fib.daily_peak_target ? `$${Number(fib.daily_peak_target).toFixed(2)}` : '—';
            const tooltip = `Mode: ${fib.mode || 'EXTENSION'} | Anchors: A=$${fib.pA || '—'}, B=$${fib.pB || '—'}, C=$${fib.pC || '—'} | Next: ${label} $${nextPrice} (${distStr}) | Cap=${peakCapStr} | ATR Cap=${atrCapStr} | Status=${fib.daily_peak_status || '—'}`;
            if (fib.daily_peak_target && Number(fib.daily_peak_target) > 0) {
                const peakTarget = `$${Number(fib.daily_peak_target).toFixed(2)}`;
                const atrConfBadge = fib.atr_confluence ? `<span class="fib-atr-confluence-badge" title="ATR Confluence detected">⚡ ATR</span>` : '';
                if (isRetracement) {
                    const statusLabel = fib.daily_peak_status === 'BELOW_VWAP'
                        ? 'Below VWAP'
                        : (fib.daily_peak_status === 'PULLBACK' ? 'Pullback' : 'At HOD');
                    fibTargetHtml = `
                        <div class="fib-target-cell-wrapper">
                            <span class="fib-target-tag ${tagClass}" title="${tooltip}">${tagPrefix} $${nextPrice} (${distStr})</span>
                            <div class="fib-peak-indicator" title="Session HOD / ATR Cap: ${peakTarget}">HOD Cap: <strong>${peakTarget}</strong> <span class="fib-status-pill pullback">${statusLabel}</span> ${atrConfBadge}</div>
                        </div>
                    `;
                } else {
                    const exhaustedPill = fib.daily_peak_status === 'PEAK_EXHAUSTED'
                        ? ' <span class="fib-status-pill exhausted">Exhausted</span>'
                        : '';
                    fibTargetHtml = `
                        <div class="fib-target-cell-wrapper">
                            <span class="fib-target-tag ${tagClass}" title="${tooltip}">${tagPrefix} $${nextPrice} (${distStr})</span>
                            <div class="fib-peak-indicator" title="ATR-Capped Intraday Target: ${peakTarget}">Target Cap: <strong>${peakTarget}</strong>${exhaustedPill} ${atrConfBadge}</div>
                        </div>
                    `;
                }
            } else {
                fibTargetHtml = `<span class="fib-target-tag ${tagClass}" title="${tooltip}">${tagPrefix} $${nextPrice} (${distStr})</span>`;
            }
        }

        const scoutIndicator = row._isScout ? `<span class="scout-dot"></span>` : '';
        const secBadgeHtml = _buildSecBadgeHtml(sym);
        const heldPlanHtml = row._isHeld
            ? `<button type="button" class="row-plan-btn" onclick="event.stopPropagation(); openExecutionPlanModal('${_escAlertHtml(sym)}')" title="Open Execution &amp; Risk Ticket for ${_escAlertHtml(sym)}">⚡</button>`
            : '';
        const vwapNum = Number(row.vwap);
        const hasVwap = isNum(row.vwap) && vwapNum > 0;
        let vwapClass = 'num-col text-muted';
        let vwapTitle = '';
        if (hasVwap) {
            if (isNum(row.price)) {
                const px = Number(row.price);
                const vwapDist = ((px - vwapNum) / vwapNum) * 100;
                vwapClass = px >= vwapNum ? 'num-col text-green' : 'num-col text-red';
                vwapTitle = ` title="${vwapDist >= 0 ? '+' : ''}${vwapDist.toFixed(2)}% vs VWAP"`;
            } else {
                vwapClass = 'num-col text-white';
            }
        }
        const rvolNum = Number(row.rvol);
        const hasRvol = Number(row.volume) > 0 && Number.isFinite(rvolNum) && rvolNum > 0;
        let rvolClass = 'text-muted';
        let rvolStyle = '';
        if (hasRvol) {
            if (rvolNum >= 2.0) {
                rvolClass = 'text-green';
                rvolStyle = ' style="font-weight: 600;"';
            } else if (rvolNum >= 1.5) {
                rvolClass = 'text-yellow';
            } else if (rvolNum < 0.5) {
                rvolClass = 'text-muted';
            } else {
                rvolClass = 'text-white';
            }
        }
        const rvolDisplay = hasRvol ? `${rvolNum.toFixed(2)}x` : '—';
        return `
            <tr class="chart-clickable" onclick="openChartModal('${sym}')" title="Click to view 1m TradingView chart">
                <td class="ticker-cell ${row._isScout ? 'is-scout' : ''}" data-sec-ticker="${_escAlertHtml(sym)}">
                    <span class="ticker-symbol ${dayColor}">${sym}</span>
                    ${scoutIndicator}${heldPlanHtml}${secBadgeHtml}
                </td>
                <td class="num-col ${pClass}">${p}</td>
                <td class="num-col ${dayColor}">${fmtSigned(row.session_change_pct, 2)}</td>
                <td class="num-col ${gapColor}">${fmtSigned(row.gap_percent, 2)}</td>
                <td class="num-col">${formatVol(row.volume)}</td>
                <td class="num-col ${rvolClass}"${rvolStyle} title="Relative Volume vs 20d avg (time-paced)">${rvolDisplay}</td>
                <td class="num-col">${isNum(row.atr_percent) ? `${fmt(row.atr_percent, 2)}%` : '—'}</td>
                <td class="num-col ${rsiColor}">${fmt(row.rsi, 1)}</td>
                <td class="${vwapClass}"${vwapTitle}>${hasVwap ? fmt(row.vwap, 2) : '—'}</td>
                <td>${macdHtml}</td>
                <td>${trendHtml}</td>
                <td>${fibTargetHtml}</td>
                <td>${(() => {
                    const gexVal = row.net_gex_total || 0;
                    const diff = row.gex_diff || 0;
                    let chevron = '';
                    if (diff > 0.005) {
                        chevron = `<span class="text-green" style="margin-left: 4px; font-weight: bold;">▲</span>`;
                    } else if (diff < -0.005) {
                        chevron = `<span class="text-red" style="margin-left: 4px; font-weight: bold;">▼</span>`;
                    }
                    
                    let dpClass = 'dealer-neutral';
                    if (gexVal > 0.005) dpClass = 'dealer-long';
                    else if (gexVal < -0.005) dpClass = 'dealer-short';
                    
                    return `<span class="dealer-badge ${dpClass}">${gexVal.toFixed(3)}${chevron}</span>`;
                })()}</td>
                <td class="score-col">
                    <span class="score-badge ${scoreBadge}">${scoreStr}</span>${noteHtml}
                </td>
            </tr>
        `;
    };

    // One malformed row must not blank the whole table: render a placeholder row instead.
    const renderRow = (row) => {
        try {
            return renderRowUnsafe(row);
        } catch (e) {
            console.error(`Row render failed for ${row && row.ticker}`, e);
            const sym = String((row && row.ticker) || '?').replace(/[<>&"']/g, '');
            const TABLE_COLUMNS = 14;  // ticker cell + the rest of the 14-column table
            return `<tr class="row-render-error"><td><span class="ticker-symbol">${sym}</span></td><td colspan="${TABLE_COLUMNS - 1}">— data unavailable —</td></tr>`;
        }
    };

    const renderHeader = (label, cls = '') => `
        <tr class="table-section-header ${cls}">
            <td colspan="14">${label}</td>
        </tr>
    `;

    if (groups.held.length > 0) {
        html += renderHeader('Your Portfolio', 'portfolio-header');
        groups.held.forEach(t => {
            t._isHeld = true;
            html += renderRow(t);
        });
    }

    if (groups.watchlist.length > 0 || groups.scouts.length > 0) {
        html += renderHeader('Strategic Watchlist', 'watchlist-header');
        
        if (groups.watchlist.length > 0) {
            groups.watchlist.forEach(t => html += renderRow(t));
        }

        if (groups.scouts.length > 0) {
            html += `
                <tr class="table-sub-header scout-header">
                    <td colspan="14">Scout Intelligence Suggestions</td>
                </tr>
            `;
            groups.scouts.forEach(t => {
                // Add scout indicator to the row object for renderRow to pick up
                t._isScout = true; 
                html += renderRow(t);
            });
        }
    }

    dTableBody.innerHTML = html;
    prefetchSecDilutionBadges([...groups.held, ...groups.watchlist, ...groups.scouts].map(r => r && r.ticker));
}

async function pollData() {
    try {
        // Refresh managers if not focused
        const active = document.activeElement;
        if (!active || (!active.classList.contains('portfolio-input') && active !== dAddWatchlistTicker)) {
            fetchPortfolio();
            fetchWatchlist();
        }

        const res = await fetch(`${API_BASE}/data`);
        const state = await res.json();

        // Handle initial boot/fetch progress bar
        if (state && state.boot_phase) {
            dIndicator.classList.add('active');
            let displayStatus = state.status || 'INITIALIZING...';
            dStatus.textContent = displayStatus;
            dStatus.style.color = 'var(--yellow)';

            // Disable Consult AI Council button
            const launcher = document.getElementById('launch-chat-btn');
            if (launcher) {
                launcher.disabled = true;
                launcher.style.opacity = '0.4';
                launcher.style.pointerEvents = 'none';
                launcher.title = 'Consulting the AI Council is locked until all ticker data has loaded.';
            }

            // Show container & blur table
            if (dProgressContainer) dProgressContainer.style.display = 'block';
            if (dTableContainer) dTableContainer.classList.add('blurred-view');
            
            const phase = state.boot_phase;
            const progress = state.boot_progress || 0;
            const total = state.boot_total || 100;
            const ticker = state.boot_ticker || '';

            let overallPercent = 0;
            let phaseTitle = 'Initializing System';
            let phaseDesc = 'Setting up real-time stock scanners...';

            if (phase === 'STARTING_UP') {
                overallPercent = 5;
                phaseTitle = 'Starting Daemon...';
                phaseDesc = 'Preparing historical data structures';
            } else if (phase === 'TECHNICAL_ANALYSIS') {
                // Phase 1 maps to 5% to 50%
                overallPercent = Math.round(5 + (progress / total) * 45);
                phaseTitle = 'Phase 1: Loading Technical History';
                phaseDesc = `Downloading 200-day daily charts to calculate SMAs and ATR%`;
            } else if (phase === 'GEX_PROFILES') {
                if (ticker === 'COMPLETE') {
                    // Hold window at 100% before dismissal
                    overallPercent = 100;
                    phaseTitle = '✅ System Ready';
                    phaseDesc = `All market data loaded. Launching dashboard...`;
                } else {
                    // Phase 2 maps to 50% to 95%
                    overallPercent = Math.round(50 + (progress / total) * 45);
                    phaseTitle = 'Phase 2: Compiling Option GEX Profiles';
                    phaseDesc = `Fetching option chains & computing synthetic Gamma curves`;
                }
            }

            // Ensure constraints
            overallPercent = Math.max(0, Math.min(100, overallPercent));
            
            // Prevent backward jumps due to async race conditions (persists across refreshes)
            let maxBoot = parseInt(sessionStorage.getItem('max_boot_percent') || '0');
            if (overallPercent > maxBoot) {
                sessionStorage.setItem('max_boot_percent', overallPercent);
            } else {
                overallPercent = maxBoot;
            }

            if (dProgressPhase) dProgressPhase.textContent = phaseTitle;
            if (dProgressStatus) dProgressStatus.textContent = phaseDesc;
            if (dProgressBar) dProgressBar.style.width = `${overallPercent}%`;
            if (dProgressPercent) dProgressPercent.textContent = `${overallPercent}%`;
            
            if (dProgressDetails) {
                if (ticker === 'COMPLETE') {
                    dProgressDetails.innerHTML = `<span class="loading-ticker">All systems ready — initializing live data stream</span>`;
                } else if (ticker && ticker !== 'SYSTEM') {
                    dProgressDetails.innerHTML = `Loading ticker data: <span class="loading-ticker">${ticker}</span> [${progress}/${total}]`;
                } else {
                    dProgressDetails.innerHTML = `Synchronizing state with SSoT database...`;
                }
            }

            // Do not render table data yet
            return;
        } else {
            // Enable Consult AI Council button
            const launcher = document.getElementById('launch-chat-btn');
            if (launcher) {
                launcher.disabled = false;
                launcher.style.opacity = '1';
                launcher.style.pointerEvents = 'auto';
                launcher.title = 'Consult AI Council';
            }

            // Initialization is complete, hide container & remove blur
            if (dProgressContainer) dProgressContainer.style.display = 'none';
            if (dTableContainer) dTableContainer.classList.remove('blurred-view');
            sessionStorage.removeItem('max_boot_percent');
        }

        // Calculate GEX deltas for the current poll cycle
        if (state && state.tickers) {
            const newGexCache = {};
            state.tickers.forEach(t => {
                const sym = t.ticker;
                const currentGex = t.net_gex_total || 0;
                if (prevGex[sym] !== undefined) {
                    t.gex_diff = currentGex - prevGex[sym];
                } else {
                    t.gex_diff = 0;
                }
                newGexCache[sym] = currentGex;
            });
            prevGex = newGexCache;
        }

        if (state && Object.keys(state).length > 0) {
            dIndicator.classList.add('active');
            let displayStatus = state.status || 'FETCHING DATA...';
            if (displayStatus === 'UNKNOWN') displayStatus = 'FETCHING DATA...';
            dStatus.textContent = displayStatus;
            
            let statColor = 'var(--green)';
            if(state.status === 'PRE-MARKET') statColor = 'var(--accent)';
            else if(state.status === 'AFTER-HOURS') statColor = 'var(--purple)';
            else if(state.status === 'CLOSED') statColor = 'var(--red)';
            else if(displayStatus === 'FETCHING DATA...') statColor = 'var(--yellow)';
            dStatus.style.color = statColor;

            // Updated time
            const ts = new Date(state._meta?.timestamp_iso || Date.now());
            dUpdated.textContent = ts.toLocaleTimeString();

            // Volatility Regime pill
            updateVolatilityRegimePill(state);

            if (state.precomputed_analytics && typeof state.precomputed_analytics === 'object') {
                _lastQuantAnalytics = state.precomputed_analytics;
                renderQuantAnalyticsPanel(_lastQuantAnalytics, _lastBacktestSummary);
            }

            // Heavy Refresh indicator
            const refreshContainer = document.getElementById('refresh-status-container');
            if (refreshContainer) {
                refreshContainer.style.display = state.is_heavy_refresh ? 'flex' : 'none';
            }

            // Render table with bifurcated sections
            renderTable(state.tickers, state);
            
            // Auto-capture screenshot for open chart instances
            captureAllChartScreenshots();
            
            // Render Macro HUD dynamic cards
            if (state.tickers) {
                state.tickers.forEach(t => {
                    if (t && t.ticker) {
                        latestTickersMap[t.ticker.toUpperCase()] = t;
                    }
                });
                if (!document.activeElement || !document.activeElement.closest('.manager-table')) {
                    updatePortfolioTotals();
                }
                let hudHtml = '';
                
                currentMacroTickers.forEach(tickerStr => {
                    const row = state.tickers.find(t => t.ticker === tickerStr);
                    const displayTicker = tickerStr.replace(/^\^/, '');
                    const symUpper = tickerStr.toUpperCase();
                    const isMacroVeto = Boolean(
                        row && (
                            (symUpper === '^VIX' && Number(row.price) > 20) ||
                            (symUpper === '^VVIX' && Number(row.price) > 105)
                        )
                    );
                    const label = MACRO_LABELS[tickerStr] || tickerStr;
                    const subtitleRaw = MACRO_SUBTITLES[tickerStr] || (label !== tickerStr ? label : '');
                    const subtitle = subtitleRaw && subtitleRaw.toUpperCase() !== displayTicker.toUpperCase() ? subtitleRaw : '';
                    const title = subtitle ? `${subtitle} (${tickerStr})` : tickerStr;
                    const vetoChip = isMacroVeto ? `<span class="macro-veto-chip" title="Threshold breach — active long-risk veto">VETO</span>` : '';
                    const headerHtml = `
                        <div class="macro-card-header" title="${title}">
                            <span class="macro-ticker">${displayTicker}${vetoChip}</span>
                            ${subtitle ? `<span class="macro-subtitle">${subtitle}</span>` : ''}
                        </div>
                    `;
                    
                    if (row) {
                        latestMacroData[tickerStr] = row;
                        const changeVal = row.session_change_pct || 0;
                        const changeStr = (changeVal > 0 ? '+' : '') + changeVal.toFixed(2) + '%';
                        const changeColor = changeVal > 0 ? 'text-green' : 'text-red';
                        
                        const INDEX_GEX_TICKERS = new Set(['SPY', 'QQQ', 'IWM', 'DIA']);
                        let gexLine = '';
                        const rawGex = Number(row.net_gex_total);
                        if (INDEX_GEX_TICKERS.has(symUpper)) {
                            if (Number.isFinite(rawGex) && rawGex !== 0) {
                                const gexSign = rawGex > 0 ? '+' : '';
                                const gexVal = `${gexSign}${rawGex.toFixed(3)}`;
                                let gexColor = 'text-muted';
                                if (rawGex > 0.005) {
                                    gexColor = 'text-green';
                                } else if (rawGex < -0.005) {
                                    gexColor = 'text-red';
                                }
                                
                                const diff = row.gex_diff || 0;
                                let chevron = '';
                                if (diff > 0.005) {
                                    chevron = `<span class="text-green" style="margin-left: 2px; font-weight: bold;">▲</span>`;
                                } else if (diff < -0.005) {
                                    chevron = `<span class="text-red" style="margin-left: 2px; font-weight: bold;">▼</span>`;
                                }
                                gexLine = `<div class="macro-gex ${gexColor}">GEX: ${gexVal}${chevron}</div>`;
                            } else {
                                gexLine = `<div class="macro-gex text-muted">GEX: —</div>`;
                            }
                        }

                        hudHtml += `
                            <div class="macro-card glass-panel${isMacroVeto ? ' macro-card-alert' : ''}" id="macro-card-${tickerStr.replace(/[^a-zA-Z0-9]/g, '')}">
                                ${headerHtml}
                                <div class="macro-val">${(row.price || 0).toFixed(2)}</div>
                                <div class="macro-gap ${changeColor}"><span>${changeStr}</span>${gexLine}</div>
                            </div>
                        `;
                    } else {
                        hudHtml += `
                            <div class="macro-card glass-panel empty-card" id="macro-card-${tickerStr.replace(/[^a-zA-Z0-9]/g, '')}">
                                ${headerHtml}
                                <div class="macro-val text-muted" style="font-size: 1.2rem; color: #666; margin: 4px 0;">—</div>
                                <div class="macro-gap text-muted" style="color: #666; font-size: 0.9rem;">Awaiting data...</div>
                            </div>
                        `;
                    }
                });
                
                dDynamicMacroCards.innerHTML = hudHtml;
                
                // Alerts mapping
                const alertsContent = document.getElementById('alerts-content');
                const topBar = document.getElementById('top-alert-bar');
                const vix = state.tickers.find(t => t.ticker === '^VIX');
                const ief = state.tickers.find(t => t.ticker === 'IEF');
                let alertsHtml = '';
                const alertKeys = [];
                const dynamicUpdates = {};
                const renderedAlertTickers = new Set();
                
                const vixChangePct = vix
                    ? (Number.isFinite(Number(vix.session_change_pct)) ? Number(vix.session_change_pct) : Number(vix.gap_percent))
                    : NaN;
                if (vix && Number(vix.price) > 20 && Number.isFinite(vixChangePct) && vixChangePct > 2.0) {
                    const vixSign = vixChangePct >= 0 ? '+' : '';
                    const vixLiveText = `${Number(vix.price).toFixed(2)} (${vixSign}${vixChangePct.toFixed(2)}%)`;
                    alertsHtml += `<div class="alert-item critical" data-ticker="^VIX"><span>⚠️ FEAR ALERT: VIX SPIKING <span class="vix-spike-pct">${vixLiveText}</span></span></div>`;
                    renderedAlertTickers.add('^VIX');
                    alertKeys.push('^VIX:FEAR');
                    dynamicUpdates['.vix-spike-pct'] = vixLiveText;
                }
                const iefChangePct = ief
                    ? (Number.isFinite(Number(ief.session_change_pct)) ? Number(ief.session_change_pct) : Number(ief.gap_percent))
                    : NaN;
                if (ief && Number.isFinite(iefChangePct) && iefChangePct < -0.15) {
                    const iefPctText = `${iefChangePct.toFixed(2)}%`;
                    alertsHtml += `<div class="alert-item warning" data-ticker="IEF"><span>📉 BOND ALERT: YIELDS RISING (IEF <span class="ief-bond-pct">${iefPctText}</span>)</span></div>`;
                    renderedAlertTickers.add('IEF');
                    alertKeys.push('IEF:BOND');
                    dynamicUpdates['.ief-bond-pct'] = iefPctText;
                }

                const alertState = state.alerts || {};
                const rawUnacked = Array.isArray(alertState.unacknowledged_events) ? alertState.unacknowledged_events : [];
                const sevRankMap = { critical: 3, warning: 2, info: 1 };
                const dedupOrder = [];
                const dedupBest = new Map();
                rawUnacked.forEach((ev, idx) => {
                    if (!ev || ev.acknowledged) return;
                    const sym = String(ev.ticker || '').trim().toUpperCase();
                    if (sym && renderedAlertTickers.has(sym)) return;
                    const key = sym || `__event__:${ev.event_id || idx}`;
                    const sev = (ev.severity === 'critical' || ev.severity === 'warning') ? ev.severity : 'info';
                    const rank = sevRankMap[sev] || 1;
                    const prev = dedupBest.get(key);
                    if (!prev) {
                        dedupOrder.push(key);
                        dedupBest.set(key, { rank, ev });
                    } else if (rank > prev.rank) {
                        dedupBest.set(key, { rank, ev });
                    }
                });
                const unackedEvents = dedupOrder.map((k) => dedupBest.get(k).ev);
                unackedEvents.forEach((ev) => {
                    const sev = (ev.severity === 'critical' || ev.severity === 'warning') ? ev.severity : 'info';
                    const icon = sev === 'critical' ? '🚨' : (sev === 'warning' ? '⚠️' : '🔔');
                    const evId = _escAlertHtml(ev.event_id || '');
                    const evTicker = _escAlertHtml(String(ev.ticker || '').trim().toUpperCase());
                    const evMsg = _escAlertHtml(ev.message || `${ev.ticker || ''} triggered`);
                    alertsHtml += `<div class="alert-item ${sev}" data-event-id="${evId}" data-ticker="${evTicker}"><span>${icon} ${evMsg}</span><button type="button" class="alert-dismiss-btn" onclick="ackAlertEvent('${evId}')" title="Acknowledge alert">&times;</button></div>`;
                    alertKeys.push(`ev:${ev.event_id || evTicker}:${sev}:${evMsg}`);
                    _notifyNewAlertEvent(ev);
                });

                if (Array.isArray(alertState.rules)) {
                    renderAlertRules(alertState.rules);
                }
                
                renderAlertTicker(alertsContent, alertsHtml, alertKeys.join('|'), dynamicUpdates);
                if (topBar) topBar.classList.toggle('has-alerts', Boolean(alertsHtml));
            }

        } else {
             dIndicator.classList.remove('active');
             dStatus.textContent = 'FETCHING DATA...';
             dStatus.style.color = 'var(--yellow)';
        }

    } catch (e) {
        console.error("Polling error", e);
        dIndicator.classList.remove('active');
        dStatus.textContent = 'DISCONNECTED';
        dStatus.style.color = 'var(--red)';
    }
}

// Start
init();

// Watchlist Logic
async function fetchWatchlist() {
    if (document.activeElement === dAddWatchlistTicker) return;
    try {
        const res = await fetch(`${API_BASE}/watchlist`);
        const data = await res.json();
        renderWatchlist(data);
    } catch (e) { console.error("Watchlist fetch failed", e); }
}

function renderWatchlist(list) {
    if (!dWatchlistContainer || !Array.isArray(list)) return;
    let html = '';
    list.forEach((ticker, index) => {
        html += `
            <div class="watch-tag">
                <span>${ticker}</span>
                <button class="delete-btn" onclick="deleteFromWatchlist(${index})" style="font-size: 0.9rem;">&times;</button>
            </div>
        `;
    });
    dWatchlistContainer.innerHTML = html;
}

async function addToWatchlist() {
    const ticker = dAddWatchlistTicker.value.trim().toUpperCase();
    if (!ticker) return;
    try {
        const res = await fetch(`${API_BASE}/watchlist`);
        const list = await res.json();
        if (list.includes(ticker)) return;
        list.push(ticker);
        await saveWatchlist(list);
        dAddWatchlistTicker.value = '';
    } catch (e) {}
}

async function deleteFromWatchlist(index) {
    try {
        const res = await fetch(`${API_BASE}/watchlist`);
        const list = await res.json();
        list.splice(index, 1);
        await saveWatchlist(list);
    } catch (e) {}
}

async function saveWatchlist(list) {
    try {
        const res = await fetch(`${API_BASE}/watchlist`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(list)
        });
        if (res.ok) {
            await fetchWatchlist();
            dDataStatus.textContent = "Watchlist updated! (Table refreshing...)";
            dDataStatus.className = "status-message text-green";
            setTimeout(() => { dDataStatus.textContent = ""; }, 3000);
            pollData(); // Force immediate refresh
        } else {
            // e.g. 400 invalid ticker, 503 persistence failure: say so and resync to what is stored
            const data = await res.json().catch(() => ({}));
            await fetchWatchlist();
            dDataStatus.textContent = `Watchlist not saved: ${data.detail || `HTTP ${res.status}`}`;
            dDataStatus.className = "status-message text-red";
        }
    } catch (e) {
        console.error("Watchlist update failed", e);
        dDataStatus.textContent = "Watchlist not saved: connection failed.";
        dDataStatus.className = "status-message text-red";
    }
}

// Toggle section visibility
function toggleSection(id, header) {
    const el = document.getElementById(id);
    const chevron = header.querySelector('.chevron');
    const card = header.closest('.sidebar-card');
    
    if (el.style.display === 'none') {
        el.style.display = 'block';
        if (chevron) chevron.style.transform = 'rotate(0deg)';
        if (card) card.classList.remove('minimized');
    } else {
        el.style.display = 'none';
        if (chevron) chevron.style.transform = 'rotate(-90deg)';
        if (card) card.classList.add('minimized');
    }
}

// Scout Logic
const VERIFIED_SCOUT_SECTORS = [
    "Technology", "Healthcare", "Financials", "Energy", "Industrials", 
    "Consumer Discretionary", "Consumer Staples", "Utilities", 
    "Real Estate", "Materials", "Communication Services",
    "AI & Data", "Aerospace & Defense", "Biotech", "Semiconductors"
];
let activeScoutCategories = [];

async function fetchScoutCategories() {
    try {
        const res = await fetch(`${API_BASE}/scout_categories`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const activeCategories = await res.json();
        renderScoutCategories(Array.isArray(activeCategories) ? activeCategories : []);
    } catch (e) { console.error("Scout categories fetch failed", e); }
}

function renderScoutCategories(activeList) {
    if (!dScoutContainer) return;
    const normalized = Array.isArray(activeList) ? activeList : [];
    activeScoutCategories = [...normalized];
    const activeSet = new Set(normalized);
    
    let html = '';
    VERIFIED_SCOUT_SECTORS.forEach(sector => {
        const isActive = activeSet.has(sector);
        const style = isActive 
            ? 'background: rgba(0, 255, 148, 0.2); border-color: var(--green); color: var(--green);' 
            : 'background: rgba(255, 255, 255, 0.03); border-color: var(--panel-border); color: var(--text-dim);';
        
        html += `
            <button type="button"
                    class="scout-toggle-btn${isActive ? ' active' : ''}"
                    aria-pressed="${isActive ? 'true' : 'false'}"
                    data-sector="${sector}"
                    onclick="toggleScoutCategory('${sector}')" 
                    style="padding: 4px 8px; border-radius: 4px; border: 1px solid; font-size: 0.65rem; font-weight: 600; cursor: pointer; transition: all 0.2s; ${style}">
                ${sector}
            </button>
        `;
    });
    dScoutContainer.innerHTML = `<div style="display: flex; flex-wrap: wrap; gap: 6px;">${html}</div>`;
}

async function toggleScoutCategory(sector) {
    try {
        let list = [...activeScoutCategories];
        try {
            const res = await fetch(`${API_BASE}/scout_categories`);
            if (res.ok) {
                const serverList = await res.json();
                if (Array.isArray(serverList)) list = serverList;
            }
        } catch (fetchErr) {
            console.warn("Using cached scout categories during toggle:", fetchErr);
        }
        
        if (list.includes(sector)) {
            list = list.filter(c => c !== sector);
        } else {
            list.push(sector);
        }
        
        await saveScoutCategories(list);
    } catch (e) {
        console.error("Toggle failed", e);
        showFeedback(null, '', e.message || 'Failed to toggle scout sector.', true, dAiScoutStatus);
    }
}

async function saveScoutCategories(list) {
    try {
        const res = await fetch(`${API_BASE}/scout_categories`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(list)
        });
        let data = null;
        try { data = await res.json(); } catch (_) { data = null; }
        if (res.ok) {
            const updatedList = data && Array.isArray(data.categories) ? data.categories : list;
            renderScoutCategories(updatedList);
            if (dAiScoutStatus) {
                dAiScoutStatus.textContent = '';
                dAiScoutStatus.className = 'status-message inline-feedback';
            }
            pollData(); // Force immediate refresh to pull newly scanned scouts
        } else {
            const errMsg = (data && (data.detail || data.message)) || `Failed to save scout sector (HTTP ${res.status})`;
            console.error("Scout categories save rejected:", res.status, errMsg);
            showFeedback(null, '', errMsg, true, dAiScoutStatus);
        }
    } catch (e) {
        console.error("Scout categories update failed", e);
        showFeedback(null, '', e.message || 'Scout categories update failed.', true, dAiScoutStatus);
    }
}

async function fetchScoutConfig() {
    try {
        const res = await fetch(`${API_BASE}/scout_config`);
        const config = await res.json();
        if (dAiScoutLimitSelect && config.scout_limit !== undefined) {
            dAiScoutLimitSelect.value = config.scout_limit;
        }
        if (dAiScoutMaxRsiSelect && config.scout_max_rsi !== undefined) {
            dAiScoutMaxRsiSelect.value = config.scout_max_rsi;
        }
    } catch (e) {
        console.error("Failed to fetch scout config:", e);
    }
}

async function saveScoutConfig() {
    try {
        const limit = dAiScoutLimitSelect ? parseInt(dAiScoutLimitSelect.value) : 2;
        const max_rsi = dAiScoutMaxRsiSelect ? parseInt(dAiScoutMaxRsiSelect.value) : 75;
        const res = await fetch(`${API_BASE}/scout_config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scout_limit: limit, scout_max_rsi: max_rsi })
        });
        if (res.ok) {
            const updated = await res.json();
            console.log("Scout config saved successfully:", updated);
        }
    } catch (e) {
        console.error("Failed to save scout config:", e);
    }
}

if (dAiScoutLimitSelect) {
    dAiScoutLimitSelect.addEventListener('change', saveScoutConfig);
}
if (dAiScoutMaxRsiSelect) {
    dAiScoutMaxRsiSelect.addEventListener('change', saveScoutConfig);
}


// Global Exports
window.deleteFromWatchlist = deleteFromWatchlist;
window.toggleScoutCategory = toggleScoutCategory;
window.toggleSection = toggleSection;
window.toggleSidebarPanel = toggleSidebarPanel;
window.copySessionReviewPayload = copySessionReviewPayload;
window.runScout = runScout;

// ─── Key-Level Triggers & Low-Power Sentinel Controllers ───
const _seenAlertEventIds = new Set();
let _alertRulesCache = [];
let _sentinelStateCache = null;

function _escAlertHtml(str) {
    return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function _notifyNewAlertEvent(ev) {
    if (!ev || !ev.event_id || _seenAlertEventIds.has(ev.event_id)) return;
    _seenAlertEventIds.add(ev.event_id);
    try {
        if (typeof window.Notification === 'function' && window.Notification.permission === 'granted') {
            new window.Notification(`GEM Alert: ${ev.ticker || 'Level'}`, { body: ev.message || ev.label || '' });
        }
    } catch (_) { /* ignore headless/jsdom notification errors */ }
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (AudioCtx) {
            const ctx = new AudioCtx();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.value = ev.severity === 'critical' ? 880 : 660;
            gain.gain.value = 0.04;
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.14);
        }
    } catch (_) { /* ignore audio context restrictions */ }
}

function renderAlertRules(rules) {
    const container = document.getElementById('alerts-rules-container');
    const countPill = document.getElementById('alerts-active-count');
    const list = Array.isArray(rules) ? rules : [];
    _alertRulesCache = list;
    const activeCount = list.filter((r) => r.enabled !== false).length;
    if (countPill) countPill.textContent = String(activeCount);
    if (!container) return;
    if (!list.length) {
        container.innerHTML = '<span class="empty-msg">No active triggers armed.</span>';
        return;
    }
    container.innerHTML = list.map((r) => {
        const id = _escAlertHtml(r.rule_id || '');
        const ticker = _escAlertHtml(r.ticker || '');
        const label = _escAlertHtml(r.label || '');
        const src = (r.source === 'council' || r.source === 'sentinel') ? r.source : 'manual';
        const metric = _escAlertHtml(r.metric || 'price');
        const op = _escAlertHtml(r.op || '>=');
        const tgt = r.target_ref ? _escAlertHtml(String(r.target_ref).toUpperCase()) : (r.target != null ? Number(r.target).toFixed(2) : '—');
        const enabled = r.enabled !== false;
        const toggleLabel = enabled ? 'Pause' : 'Arm';
        return `
            <div class="alert-rule-row${enabled ? '' : ' disabled'}" data-rule-id="${id}">
                <div class="alert-rule-meta">
                    <div class="alert-rule-title" title="${label}">
                        <span>${ticker}</span>
                        <span class="alert-source-pill ${src}">${src}</span>
                        <span>${label}</span>
                    </div>
                    <div class="alert-rule-sub">${metric} ${op} ${tgt}${r.trigger_count ? ` · fired ${r.trigger_count}x` : ''}</div>
                </div>
                <div class="alert-rule-actions">
                    <button type="button" class="alert-mini-btn" onclick="toggleAlertRule('${id}')">${toggleLabel}</button>
                    <button type="button" class="alert-mini-btn" onclick="deleteAlertRule('${id}')" title="Delete trigger">&times;</button>
                </div>
            </div>
        `;
    }).join('');
}

function renderSentinelStatus(sentinel) {
    if (!sentinel || typeof sentinel !== 'object') return;
    _sentinelStateCache = sentinel;
    const toggleBtn = document.getElementById('sentinel-toggle-btn');
    const providerSel = document.getElementById('sentinel-provider-select');
    const intervalSel = document.getElementById('sentinel-interval-select');
    const peakToggle = document.getElementById('sentinel-peak-toggle');
    const statusText = document.getElementById('sentinel-status-text');

    const enabled = Boolean(sentinel.enabled);
    if (toggleBtn) {
        toggleBtn.textContent = enabled ? 'ON' : 'OFF';
        toggleBtn.setAttribute('aria-pressed', enabled ? 'true' : 'false');
        toggleBtn.classList.toggle('active', enabled);
    }
    if (providerSel && sentinel.provider) providerSel.value = sentinel.provider;
    if (intervalSel && sentinel.interval_sec) intervalSel.value = String(sentinel.interval_sec);
    if (peakToggle) peakToggle.checked = Boolean(sentinel.peak_predictor_enabled);
    if (statusText) {
        const last = sentinel.last_run || {};
        const chainTop = Array.isArray(sentinel.model_chain) && sentinel.model_chain.length ? sentinel.model_chain[0] : 'gemini-2.5-flash-lite';
        const activeModel = last.model_used || chainTop;
        const keyNote = (sentinel.provider === 'gemini_free' && !sentinel.api_key_configured)
            ? 'GEMINI_API_KEY not set'
            : (last.error ? `Degraded: ${last.error}` : (last.skipped_reason ? `Idle (${last.skipped_reason})` : `Model: ${activeModel}`));
        const fbSuffix = last.fallback_count ? ` · ${last.fallback_count} fallback(s)` : '';
        statusText.textContent = `${keyNote}${fbSuffix}`;
    }
}

async function fetchAlertsAndSentinel() {
    try {
        const res = await fetch(`${API_BASE}/alerts`);
        if (!res.ok) return;
        const data = await res.json();
        if (Array.isArray(data.rules)) renderAlertRules(data.rules);
        if (data.sentinel) renderSentinelStatus(data.sentinel);
    } catch (e) { console.error('Failed to load alerts & sentinel status:', e); }
}

async function addManualAlertRule() {
    const tickerEl = document.getElementById('alert-ticker-input');
    const metricEl = document.getElementById('alert-metric-select');
    const opEl = document.getElementById('alert-op-select');
    const targetEl = document.getElementById('alert-target-input');
    const statusEl = document.getElementById('alerts-panel-status');
    const ticker = tickerEl ? tickerEl.value.trim().toUpperCase() : '';
    const target = targetEl ? Number(targetEl.value) : NaN;
    if (!ticker || !Number.isFinite(target)) {
        showFeedback(null, '', 'Enter ticker and numeric target.', true, statusEl);
        return;
    }
    try {
        const res = await fetch(`${API_BASE}/alerts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ticker,
                metric: metricEl ? metricEl.value : 'price',
                op: opEl ? opEl.value : 'crosses_above',
                target,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        if (tickerEl) tickerEl.value = '';
        if (targetEl) targetEl.value = '';
        if (data.alerts && Array.isArray(data.alerts.rules)) renderAlertRules(data.alerts.rules);
        showFeedback(null, '', `Armed trigger for ${ticker}`, false, statusEl);
        pollData();
    } catch (e) {
        showFeedback(null, '', e.message || 'Failed to arm trigger', true, statusEl);
    }
}

async function deleteAlertRule(ruleId) {
    try {
        const res = await fetch(`${API_BASE}/alerts/${encodeURIComponent(ruleId)}`, { method: 'DELETE' });
        if (!res.ok) return;
        const data = await res.json();
        if (data.alerts && Array.isArray(data.alerts.rules)) renderAlertRules(data.alerts.rules);
        pollData();
    } catch (e) { console.error('Delete alert rule failed:', e); }
}

async function toggleAlertRule(ruleId) {
    try {
        const res = await fetch(`${API_BASE}/alerts/${encodeURIComponent(ruleId)}/toggle`, { method: 'POST' });
        if (!res.ok) return;
        const data = await res.json();
        if (data.alerts && Array.isArray(data.alerts.rules)) renderAlertRules(data.alerts.rules);
        pollData();
    } catch (e) { console.error('Toggle alert rule failed:', e); }
}

async function ackAlertEvent(eventId) {
    try {
        const res = await fetch(`${API_BASE}/alerts/events/${encodeURIComponent(eventId)}/ack`, { method: 'POST' });
        if (!res.ok) return;
        // Remove the item and its marquee clone, then re-measure whether the bar still overflows.
        document.querySelectorAll(`.alert-item[data-event-id="${CSS.escape(eventId)}"]`).forEach(el => el.remove());
        const alertsEl = document.getElementById('alerts-content');
        if (alertsEl) {
            delete alertsEl.dataset.renderedKey;
            delete alertsEl.dataset.renderedHtml;
        }
        updateAlertMarquee();
        pollData();
    } catch (e) { console.error('Ack alert failed:', e); }
}

// --- Top alert bar ticker -------------------------------------------------------------
// Alerts scroll as a seamless marquee only when they overflow the bar; hovering or focusing
// the bar pauses it so the acknowledge (×) buttons stay clickable. Reduced-motion users get a
// static, manually scrollable bar instead.
const MARQUEE_PX_PER_SEC = 45;

function renderAlertTicker(container, alertsHtml, structureKey, dynamicUpdates) {
    if (!container) return;
    const html = alertsHtml || '<span class="empty-state">NO ACTIVE ALERTS</span>';
    const key = (typeof structureKey === 'string' && structureKey.length > 0) ? structureKey : html;
    // Compare against the structural key so live numeric jitter (e.g. VIX %) updates in place
    // without tearing down .ticker-track or restarting the scroll animation.
    if (container.dataset.renderedKey === key) {
        if (dynamicUpdates && typeof dynamicUpdates === 'object') {
            Object.entries(dynamicUpdates).forEach(([selector, text]) => {
                container.querySelectorAll(selector).forEach((el) => {
                    if (el.textContent !== text) el.textContent = text;
                });
            });
        }
        return;
    }
    container.dataset.renderedKey = key;
    container.dataset.renderedHtml = html;
    container.innerHTML = `<div class="ticker-track"><div class="ticker-group">${html}</div></div>`;
    updateAlertMarquee();
}

function updateAlertMarquee() {
    const container = document.getElementById('alerts-content');
    const track = container && container.querySelector('.ticker-track');
    const group = track && track.querySelector('.ticker-group:not([aria-hidden])');
    if (!container || !track || !group) return;

    track.querySelectorAll('.ticker-group[aria-hidden="true"]').forEach(el => el.remove());
    container.classList.remove('is-scrolling', 'is-single-pass');
    container.style.removeProperty('--marquee-start');
    container.style.removeProperty('--marquee-delay');

    const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const hasAlerts = Boolean(group.querySelector('.alert-item'));
    if (!hasAlerts || reduceMotion) return;

    let padX = 0;
    try {
        const cs = window.getComputedStyle ? window.getComputedStyle(container) : null;
        if (cs) padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    } catch (_) {}
    const availWidth = Math.max(0, container.clientWidth - padX);
    const groupWidth = group.scrollWidth;

    if (groupWidth > availWidth) {
        container.classList.add('is-scrolling'); // adds trailing spacing, so measure after this
        const clone = group.cloneNode(true);
        clone.setAttribute('aria-hidden', 'true');
        clone.querySelectorAll('button').forEach(btn => { btn.tabIndex = -1; });
        track.appendChild(clone);
        const seconds = Math.max(12, group.scrollWidth / MARQUEE_PX_PER_SEC);
        container.style.setProperty('--marquee-duration', `${seconds.toFixed(1)}s`);
    } else {
        // Single-pass marquee: smoothly scroll alerts without cloning (never duplicates a ticker).
        container.classList.add('is-scrolling', 'is-single-pass');
        const startPx = Math.max(availWidth, 320);
        const travelPx = startPx + Math.max(groupWidth, 1);
        const seconds = Math.max(12, travelPx / MARQUEE_PX_PER_SEC);
        const initOffsetPx = Math.max(0, startPx - Math.min(startPx * 0.12, 120));
        const delaySec = initOffsetPx / MARQUEE_PX_PER_SEC;
        container.style.setProperty('--marquee-start', `${startPx}px`);
        container.style.setProperty('--marquee-duration', `${seconds.toFixed(1)}s`);
        container.style.setProperty('--marquee-delay', `-${delaySec.toFixed(1)}s`);
    }
}

let _alertMarqueeResizeTimer = null;
window.addEventListener('resize', () => {
    clearTimeout(_alertMarqueeResizeTimer);
    _alertMarqueeResizeTimer = setTimeout(updateAlertMarquee, 150);
});

async function saveSentinelConfig(overrides = {}) {
    const providerSel = document.getElementById('sentinel-provider-select');
    const intervalSel = document.getElementById('sentinel-interval-select');
    const peakToggle = document.getElementById('sentinel-peak-toggle');
    const statusEl = document.getElementById('alerts-panel-status');
    const payload = {
        provider: providerSel ? providerSel.value : 'gemini_free',
        interval_sec: intervalSel ? Number(intervalSel.value) : 600,
        peak_predictor_enabled: peakToggle ? Boolean(peakToggle.checked) : false,
        ...overrides,
    };
    try {
        const res = await fetch(`${API_BASE}/alert_sentinel/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        if (data.sentinel) renderSentinelStatus(data.sentinel);
    } catch (e) {
        showFeedback(null, '', e.message || 'Failed to update Sentinel', true, statusEl);
    }
}

async function toggleSentinelEnabled() {
    const currentlyEnabled = Boolean(_sentinelStateCache && _sentinelStateCache.enabled);
    await saveSentinelConfig({ enabled: !currentlyEnabled });
}

async function runSentinelNow() {
    const btn = document.getElementById('sentinel-run-now-btn');
    const statusEl = document.getElementById('alerts-panel-status');
    if (btn) btn.disabled = true;
    try {
        const res = await fetch(`${API_BASE}/alert_sentinel/run`, { method: 'POST' });
        const data = await res.json();
        if (data.sentinel) renderSentinelStatus(data.sentinel);
        if (data.alerts && Array.isArray(data.alerts.rules)) renderAlertRules(data.alerts.rules);
        const out = data.outcome || {};
        if (out.status === 'OK') {
            showFeedback(null, '', `Sentinel (${out.model_used}) armed ${out.armed_count} trigger(s).`, false, statusEl);
        } else {
            showFeedback(null, '', out.error || out.skipped_reason || 'Sentinel run did not update.', true, statusEl);
        }
        pollData();
    } catch (e) {
        showFeedback(null, '', e.message || 'Sentinel run failed.', true, statusEl);
    } finally {
        if (btn) btn.disabled = false;
    }
}

const _addAlertBtn = document.getElementById('add-alert-rule-btn');
if (_addAlertBtn) _addAlertBtn.addEventListener('click', addManualAlertRule);
const _sentinelToggleBtn = document.getElementById('sentinel-toggle-btn');
if (_sentinelToggleBtn) _sentinelToggleBtn.addEventListener('click', toggleSentinelEnabled);
const _sentinelProviderSel = document.getElementById('sentinel-provider-select');
if (_sentinelProviderSel) _sentinelProviderSel.addEventListener('change', () => saveSentinelConfig());
const _sentinelIntervalSel = document.getElementById('sentinel-interval-select');
if (_sentinelIntervalSel) _sentinelIntervalSel.addEventListener('change', () => saveSentinelConfig());
const _sentinelPeakToggle = document.getElementById('sentinel-peak-toggle');
if (_sentinelPeakToggle) _sentinelPeakToggle.addEventListener('change', () => saveSentinelConfig());
const _sentinelRunNowBtn = document.getElementById('sentinel-run-now-btn');
if (_sentinelRunNowBtn) _sentinelRunNowBtn.addEventListener('click', runSentinelNow);

// ─── Phase 3: Quant Analytics, Decision Backtest & SEC 72h Dilution Badges ───
const secDilutionCache = {};
const secDilutionInFlight = new Set();
const _SEC_ETF_SKIP = new Set(['SPY', 'QQQ', 'IWM', 'DIA', 'VIXY', 'IEF', 'UUP', 'GDX']);
let _lastQuantAnalytics = null;
let _lastBacktestSummary = null;

function _buildSecBadgeHtml(sym) {
    const key = String(sym || '').trim().toUpperCase();
    const info = secDilutionCache[key];
    if (!info || !info.dilution_warning_72h) return '';
    const filings = Array.isArray(info.filings) ? info.filings : [];
    const dil = filings.find(f => f && f.is_dilution_form) || filings[0] || {};
    const form = _escAlertHtml(info.latest_dilution_form || dil.form || 'DILUTION');
    const fdate = _escAlertHtml(info.latest_dilution_date || dil.filing_date || '');
    const rawUrl = String(dil.url || '');
    const safeUrl = /^https:\/\/[^\s"'<>]+$/i.test(rawUrl)
        ? rawUrl
        : `https://www.sec.gov/edgar/searchedgar/companysearch`;
    const title = fdate
        ? `SEC 72h dilution filing (${form} filed ${fdate}) — opens EDGAR`
        : `SEC 72h dilution filing (${form}) — opens EDGAR`;
    return `<a class="sec-dilution-badge" href="${_escAlertHtml(safeUrl)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()" title="${title}">⚠️ SEC ${form}</a>`;
}

function _injectSecBadgeForSymbol(sym) {
    const key = String(sym || '').trim().toUpperCase();
    const badgeHtml = _buildSecBadgeHtml(key);
    if (!badgeHtml) return;
    document.querySelectorAll(`td.ticker-cell[data-sec-ticker="${CSS.escape(key)}"]`).forEach((cell) => {
        const existing = cell.querySelector('.sec-dilution-badge');
        if (existing) existing.remove();
        cell.insertAdjacentHTML('beforeend', badgeHtml);
    });
}

function prefetchSecDilutionBadges(symbols) {
    if (!Array.isArray(symbols)) return;
    symbols.forEach((rawSym) => {
        const sym = String(rawSym || '').trim().toUpperCase();
        if (!sym || sym.startsWith('^') || _SEC_ETF_SKIP.has(sym)) return;
        if (Object.prototype.hasOwnProperty.call(secDilutionCache, sym) || secDilutionInFlight.has(sym)) return;
        secDilutionInFlight.add(sym);
        fetch(`${API_BASE}/analytics/sec/${encodeURIComponent(sym)}`)
            .then((res) => (res && res.ok ? res.json() : null))
            .then((data) => {
                if (data && typeof data === 'object') {
                    secDilutionCache[sym] = data;
                    if (data.dilution_warning_72h) _injectSecBadgeForSymbol(sym);
                }
            })
            .catch(() => { /* fail-open quietly if SEC lookup offline */ })
            .finally(() => {
                secDilutionInFlight.delete(sym);
            });
    });
}

function updateMacroCalendarPill(macroCal) {
    const pill = document.getElementById('macro-calendar-pill');
    if (!pill || !macroCal || typeof macroCal !== 'object') return;
    const nextEv = macroCal.next_event;
    if (!nextEv || !nextEv.name) {
        pill.className = 'macro-cal-pill macro-cal-clear';
        pill.textContent = '📅 Clear';
        pill.title = 'No upcoming Tier-1 macro releases queued';
        return;
    }
    const shortName = String(nextEv.name)
        .replace(' Rate Decision', '')
        .replace('Non-Farm Payrolls', 'NFP')
        .replace('US ', '');
    const hrs = Number(nextEv.hours_away);
    let timeTag = '';
    if (nextEv.in_release_window || (Number.isFinite(hrs) && hrs <= 0)) {
        timeTag = 'LIVE';
    } else if (Number.isFinite(hrs)) {
        if (hrs < 1) {
            timeTag = `${Math.max(1, Math.round(hrs * 60))}m`;
        } else if (hrs < 48) {
            timeTag = `${Math.round(hrs)}h`;
        } else {
            timeTag = `${Math.round(hrs / 24)}d`;
        }
    }
    if (macroCal.shield_active) {
        pill.className = 'macro-cal-pill macro-cal-shield';
        pill.textContent = `🛡️ ${shortName} ${timeTag} (-50% stops)`.trim();
    } else {
        pill.className = 'macro-cal-pill macro-cal-clear';
        pill.textContent = `📅 ${shortName} ${timeTag}`.trim();
    }
    const upcomingLines = (Array.isArray(macroCal.upcoming_events) ? macroCal.upcoming_events : [])
        .map((e) => `${e.name}: ${e.date} ${e.time_et} ET (${e.hours_away}h)`)
        .join(' | ');
    pill.title = `${macroCal.shield_active ? 'ENH_259 Shield ACTIVE (-50% stops) · ' : ''}${upcomingLines}`;
}

function renderPortfolioCommandStrip(analytics) {
    const strip = document.getElementById('portfolio-command-strip');
    if (!strip) return;
    const eqEl = document.getElementById('cmd-equity-val');
    const pnlEl = document.getElementById('cmd-pnl-val');
    const cashEl = document.getElementById('cmd-cash-val');
    const vetoEl = document.getElementById('cmd-vetoes-val');
    const actionsEl = document.getElementById('cmd-strip-actions');

    const data = analytics && typeof analytics === 'object' ? analytics : (_lastQuantAnalytics || {});
    const positions = Array.isArray(data.positions) ? data.positions : [];
    const vetoes = Array.isArray(data.global_vetoes) ? data.global_vetoes : [];

    let totalMvUsd = 0;
    let totalMvEur = 0;
    let totalCostUsd = 0;
    let hasMvUsd = false;
    let hasMvEur = false;
    let hasPnl = false;
    let totalPnlUsd = 0;

    positions.forEach((p) => {
        if (Number.isFinite(Number(p.market_value_usd)) && p.market_value_usd !== null) {
            totalMvUsd += Number(p.market_value_usd);
            hasMvUsd = true;
        }
        if (Number.isFinite(Number(p.market_value_eur)) && p.market_value_eur !== null) {
            totalMvEur += Number(p.market_value_eur);
            hasMvEur = true;
        }
        if (Number.isFinite(Number(p.unrealized_pnl_usd)) && p.unrealized_pnl_usd !== null) {
            totalPnlUsd += Number(p.unrealized_pnl_usd);
            hasPnl = true;
            // WAC is in EUR; the server supplies the USD cost basis converted at the live rate.
            if (Number.isFinite(Number(p.cost_basis_usd)) && p.cost_basis_usd !== null) {
                totalCostUsd += Number(p.cost_basis_usd);
            }
        }
    });

    if (eqEl) {
        if (hasMvUsd && hasMvEur) {
            eqEl.textContent = `$${totalMvUsd.toFixed(0)} (€${totalMvEur.toFixed(0)})`;
        } else if (hasMvUsd) {
            eqEl.textContent = `$${totalMvUsd.toFixed(2)}`;
        } else if (positions.length > 0) {
            eqEl.textContent = `${positions.length} Held`;
        } else {
            eqEl.textContent = '—';
        }
    }

    if (pnlEl) {
        if (hasPnl) {
            const pct = totalCostUsd > 0 ? (totalPnlUsd / totalCostUsd) * 100 : null;
            const sign = totalPnlUsd >= 0 ? '+' : '';
            const arrow = totalPnlUsd >= 0 ? '▲' : '▼';
            const pctTxt = pct !== null ? ` (${sign}${pct.toFixed(1)}%)` : '';
            const absUsd = Math.abs(totalPnlUsd).toLocaleString('en-US', { maximumFractionDigits: 0 });
            pnlEl.textContent = `${arrow} ${totalPnlUsd >= 0 ? '+' : '−'}$${absUsd}${pctTxt}`;
            pnlEl.className = totalPnlUsd >= 0 ? 'text-green' : 'text-red';
        } else {
            pnlEl.textContent = '—';
            pnlEl.className = 'text-muted';
        }
    }

    if (cashEl) {
        const cashEurEl = document.getElementById('cash-val-eur');
        const cashUsdEl = document.getElementById('cash-val-usd');
        const eurTxt = cashEurEl ? cashEurEl.textContent.trim() : '';
        const usdTxt = cashUsdEl ? cashUsdEl.textContent.trim() : '';
        if (eurTxt && eurTxt !== '—') {
            cashEl.textContent = usdTxt && usdTxt !== '—' ? `${eurTxt} (${usdTxt})` : eurTxt;
        } else if (usdTxt && usdTxt !== '—') {
            cashEl.textContent = usdTxt;
        } else {
            cashEl.textContent = '€0.00';
        }
    }

    if (vetoEl) {
        vetoEl.textContent = String(vetoes.length);
        vetoEl.className = vetoes.length > 0 ? 'cmd-veto-badge active' : 'cmd-veto-badge clear';
        vetoEl.title = vetoes.length > 0 ? vetoes.map(v => `${v.rule_id}: ${v.reason || ''}`).join(' | ') : 'No global risk vetoes active';
    }

    if (actionsEl) {
        const planBtns = positions.map((p) => {
            const sym = _escAlertHtml(p.ticker || '');
            return `<button type="button" class="cmd-plan-btn" onclick="openExecutionPlanModal('${sym}')" title="Open Execution &amp; Risk Ticket for ${sym}">⚡ ${sym} Plan</button>`;
        }).join('');
        actionsEl.innerHTML = planBtns;
    }
}

function renderQuantAnalyticsPanel(analytics, backtest) {
    const panel = document.getElementById('analytics-quant-panel');
    const data = analytics && typeof analytics === 'object' ? analytics : {};
    if (data.macro_calendar) {
        updateMacroCalendarPill(data.macro_calendar);
    }
    if (data.regime && data.regime.volatility_state) {
        updateVolatilityRegimePill({ precomputed_analytics: data });
    }
    renderPortfolioCommandStrip(data);
    if (!panel) return;
    const regime = data.regime && typeof data.regime === 'object' ? data.regime : {};
    const perm = regime.strategy_permissibility && typeof regime.strategy_permissibility === 'object'
        ? regime.strategy_permissibility
        : {};
    const vetoes = Array.isArray(data.global_vetoes) ? data.global_vetoes : [];
    const positions = Array.isArray(data.positions) ? data.positions : [];
    const watchlistSignals = Array.isArray(data.watchlist_signals) ? data.watchlist_signals : [];
    const macroCal = data.macro_calendar && typeof data.macro_calendar === 'object' ? data.macro_calendar : null;

    const volState = _escAlertHtml(regime.volatility_state || 'UNKNOWN');
    const permTags = [
        ['Breakout', Boolean(perm.Momentum_Breakouts)],
        ['EP', Boolean(perm.Episodic_Pivots)],
        ['Pullback', Boolean(perm.Pullback_Continuations)],
        ['Mean-Rev', Boolean(perm.Mean_Reversion)],
    ].map(([label, ok]) => `<span class="quant-perm-tag ${ok ? 'allowed' : 'blocked'}">${ok ? '✓' : '✕'} ${label}</span>`).join('');

    let macroRowHtml = '';
    if (macroCal && macroCal.next_event) {
        const ne = macroCal.next_event;
        const cls = macroCal.shield_active ? 'quant-veto-item' : 'quant-ok-line';
        const prefix = macroCal.shield_active ? '🛡️ Shield Active (-50% stops)' : '📅 Clear';
        macroRowHtml = `<div class="${cls}" style="margin-top: 4px;">${prefix} · ${_escAlertHtml(ne.name)} (${_escAlertHtml(String(ne.hours_away))}h)</div>`;
    }

    const vetoesHtml = vetoes.length
        ? vetoes.map((v) => {
            const rid = _escAlertHtml(v.rule_id || 'VETO');
            const msg = _escAlertHtml(v.reason || v.action || '');
            return `<div class="quant-veto-item"><strong>${rid}</strong> ${msg}</div>`;
        }).join('')
        : '<div class="quant-ok-line">✓ No active global vetoes</div>';

    const tranchesHtml = positions.length
        ? positions.map((p) => {
            const sym = _escAlertHtml(p.ticker || '');
            const tr = p.tranches || {};
            const t25 = tr['25pct'];
            const t50 = tr['50pct'];
            const mand = Array.isArray(p.triggered_mandates) ? p.triggered_mandates : [];
            const mandBadge = mand.length
                ? ` <span class="quant-mandate-pill">${mand.map(m => _escAlertHtml(m.rule_id || '')).join(', ')}</span>`
                : '';
            const planBtn = `<button type="button" class="quant-plan-btn" onclick="openExecutionPlanModal('${sym}')" title="Open Execution &amp; Risk Ticket">📋 Plan</button>`;
            if (!t25 && !t50) {
                return `<div class="quant-tranche-row"><span><strong>${sym}</strong>${mandBadge}: awaiting live price</span>${planBtn}</div>`;
            }
            const s25 = t25 ? `${t25.sell_shares}sh` : '—';
            const s50 = t50 ? `${t50.sell_shares}sh` : '—';
            const lim = p.sweep_limit_usd != null ? `$${Number(p.sweep_limit_usd).toFixed(2)}` : '—';
            return `<div class="quant-tranche-row"><span><strong>${sym}</strong>${mandBadge} · 25%: ${s25} · 50%: ${s50} · Sweep ${lim}</span>${planBtn}</div>`;
        }).join('')
        : '<div class="empty-msg">No held positions</div>';

    const setupsHtml = watchlistSignals.length
        ? watchlistSignals.map((w) => {
            const sym = _escAlertHtml(w.ticker || '');
            const rawSetup = String(w.setup || 'RANGE_WAIT');
            const shortSetup = _escAlertHtml(rawSetup.replace('PULLBACK_CONTINUATION', 'PULLBACK').replace('MOMENTUM_BREAKOUT', 'BREAKOUT').replace('EPISODIC_PIVOT', 'EP').replace('MEAN_REVERSION', 'MEAN_REV').replace('RANGE_WAIT', 'WAIT'));
            const ok = Boolean(w.strategy_permissible);
            const rvolTxt = w.rvol != null ? ` · ${Number(w.rvol).toFixed(1)}x` : '';
            return `<div class="quant-setup-row"><strong>${sym}</strong><span class="quant-setup-pill ${ok ? 'ok' : 'blocked'}">${shortSetup}${rvolTxt}</span></div>`;
        }).join('')
        : '<div class="empty-msg">No watchlist telemetry yet</div>';

    const bt = backtest && typeof backtest === 'object' ? backtest : null;
    const btHtml = bt
        ? `<div class="quant-backtest-grid">
                <div><span>Logged</span><strong>${Number(bt.total_logged || 0)}</strong></div>
                <div><span>Eval</span><strong>${Number(bt.evaluated_count || 0)}</strong></div>
                <div><span>Hit%</span><strong>${bt.hit_rate_pct != null ? `${Number(bt.hit_rate_pct).toFixed(1)}%` : '—'}</strong></div>
                <div><span>5D Avg</span><strong>${bt.avg_ret_5d_pct != null ? `${Number(bt.avg_ret_5d_pct) > 0 ? '+' : ''}${Number(bt.avg_ret_5d_pct).toFixed(2)}%` : '—'}</strong></div>
           </div>`
        : '<div class="empty-msg">Backtest awaiting logged trades</div>';

    panel.innerHTML = `
        <div class="quant-section">
            <div class="quant-section-title">Regime: <strong>${volState}</strong></div>
            <div class="quant-perm-grid">${permTags}</div>
            ${macroRowHtml}
        </div>
        <div class="quant-section">
            <div class="quant-section-title">Global Vetoes</div>
            ${vetoesHtml}
        </div>
        <div class="quant-section">
            <div class="quant-section-title">Position Trim &amp; Execution</div>
            ${tranchesHtml}
        </div>
        <div class="quant-section">
            <div class="quant-section-title">Watchlist Setups</div>
            ${setupsHtml}
        </div>
        <div class="quant-section">
            <div class="quant-section-title">Decision Backtest</div>
            ${btHtml}
        </div>
    `;
}

async function copyExecutionOrderText(orderText, btn) {
    try {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(orderText);
        }
        if (btn) {
            const orig = btn.textContent;
            btn.textContent = '✓ Copied';
            setTimeout(() => { btn.textContent = orig; }, 1200);
        }
    } catch (_) { /* clipboard optional in headless/http contexts */ }
}

async function armQuickTriggerFromPlan(ticker, op, target, label) {
    const fb = document.getElementById('exec-modal-feedback');
    try {
        const res = await fetch(`${API_BASE}/alerts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker, metric: 'price', op, target: Number(target), label }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        if (data.alerts && Array.isArray(data.alerts.rules)) renderAlertRules(data.alerts.rules);
        showFeedback(null, '', `Armed alert: ${ticker} ${op} $${Number(target).toFixed(2)}`, false, fb);
        pollData();
    } catch (e) {
        showFeedback(null, '', e.message || 'Failed to arm trigger', true, fb);
    }
}

function closeExecutionPlanModal() {
    const overlay = document.getElementById('execution-plan-modal-overlay');
    if (overlay) closeModal(overlay);
}

async function openExecutionPlanModal(ticker) {
    const overlay = document.getElementById('execution-plan-modal-overlay');
    const titleSym = document.getElementById('exec-modal-ticker');
    const bodyEl = document.getElementById('exec-modal-body');
    const fbEl = document.getElementById('exec-modal-feedback');
    if (!overlay || !bodyEl) return;

    const sym = String(ticker || '').trim().toUpperCase();
    if (titleSym) titleSym.textContent = sym || 'PORTFOLIO';
    if (fbEl) { fbEl.textContent = ''; fbEl.className = 'status-message inline-feedback'; }
    bodyEl.innerHTML = '<div class="empty-msg">Computing deterministic execution plan...</div>';
    openModal(overlay);

    try {
        const [execRes, peakRes] = await Promise.allSettled([
            fetch(`${API_BASE}/analytics/execution`),
            sym ? fetch(`${API_BASE}/analytics/peak/${encodeURIComponent(sym)}`) : Promise.resolve(null),
        ]);
        if (execRes.status !== 'fulfilled' || !execRes.value || !execRes.value.ok) {
            throw new Error(`HTTP ${execRes.status === 'fulfilled' && execRes.value ? execRes.value.status : 'error'}`);
        }
        const plan = await execRes.value.json();
        const peakData = (peakRes.status === 'fulfilled' && peakRes.value && peakRes.value.ok)
            ? await peakRes.value.json()
            : null;
        const positions = Array.isArray(plan.positions) ? plan.positions : [];
        const pos = positions.find((p) => String(p.ticker || '').toUpperCase() === sym) || positions[0];
        const cash = plan.cash_reconciliation || {};
        const macroCal = plan.macro_calendar || {};
        if (!pos) {
            bodyEl.innerHTML = `<div class="empty-msg">No active position sizing for ${_escAlertHtml(sym)}.</div>`;
            return;
        }

        const pSym = _escAlertHtml(pos.ticker || '');
        const stop = pos.trailing_stop || {};
        const ladder = Array.isArray(pos.fib_limit_ladder) ? pos.fib_limit_ladder : [];
        const directives = Array.isArray(pos.order_directives) ? pos.order_directives : [];

        const stopPriceStr = stop.effective_stop_price != null ? `$${Number(stop.effective_stop_price).toFixed(2)}` : '—';
        const anchorStr = stop.anchor_price != null ? `$${Number(stop.anchor_price).toFixed(2)}` : '—';
        const armStopBtn = stop.effective_stop_price != null
            ? `<button type="button" class="exec-mini-btn arm" onclick="armQuickTriggerFromPlan('${pSym}', '<=', ${Number(stop.effective_stop_price)}, 'MANDATE_36 Trailing Stop')">🔔 Arm Stop (${stopPriceStr})</button>`
            : '';

        let peakHtml = '';
        if (peakData && peakData.status === 'ok') {
            const prof = peakData.profile || {};
            const exScore = Number(peakData.exhaustion_score || 0);
            const exState = String(peakData.exhaustion_state || 'BUILDING');
            const exCls = exState === 'SELL_ZONE' ? 'sell-zone' : (exState === 'APPROACHING_PEAK' ? 'approaching' : 'building');
            const frLim = peakData.front_run_sell_limit_usd != null ? Number(peakData.front_run_sell_limit_usd).toFixed(2) : null;
            const zoneTxt = (peakData.predicted_peak_low_usd != null && peakData.predicted_peak_high_usd != null)
                ? `$${Number(peakData.predicted_peak_low_usd).toFixed(2)} – $${Number(peakData.predicted_peak_high_usd).toFixed(2)}`
                : '—';
            const t25Shares = (pos.tranches && pos.tranches['25pct']) ? pos.tranches['25pct'].sell_shares : null;
            const copyPeakOrd = (frLim && t25Shares) ? `SELL ${t25Shares} ${pSym} LMT $${frLim}` : (frLim ? `SELL ${pSym} LMT $${frLim}` : '');
            const sigChips = (Array.isArray(peakData.signals) ? peakData.signals : [])
                .map((s) => `<span class="peak-signal-chip">${_escAlertHtml(s)}</span>`)
                .join('');
            const peakBtns = frLim
                ? `<div class="exec-actions-cell" style="margin-top:6px;">
                        <button type="button" class="exec-mini-btn arm" onclick="armQuickTriggerFromPlan('${pSym}', '>=', ${Number(frLim)}, 'Predicted Daily Peak Sell')">🔔 Arm Peak Sell ($${frLim})</button>
                        <button type="button" class="exec-mini-btn" onclick="copyExecutionOrderText('${_escAlertHtml(copyPeakOrd)}', this)">📋 Copy Peak Limit</button>
                   </div>`
                : '';

            peakHtml = `
                <div class="exec-ticket-section">
                    <div class="quant-section-title">🎯 Daily Peak Window &amp; Exhaustion Predictor (${Number(prof.sessions_analyzed || 0)}d 5m RTH)</div>
                    <div class="peak-stat-grid">
                        <div><span>Median HOD</span><strong>${_escAlertHtml(prof.median_peak_time_et || '—')} ET</strong></div>
                        <div><span>Peak Window</span><strong>${_escAlertHtml(prof.peak_window_start_et || '—')}–${_escAlertHtml(prof.peak_window_end_et || '—')}</strong></div>
                        <div><span>Before 10:30</span><strong>${prof.pct_peaking_before_1030 != null ? `${prof.pct_peaking_before_1030}%` : '—'}</strong></div>
                        <div><span>Avg HOD Fade</span><strong>${prof.avg_fade_from_peak_pct != null ? `${prof.avg_fade_from_peak_pct}%` : '—'}</strong></div>
                    </div>
                    <div class="exec-kv-row" style="margin-top:6px;">
                        <span>Peak Zone: <strong>${zoneTxt}</strong></span>
                        <span>Front-Run Limit: <strong>${frLim ? `$${frLim}` : '—'}</strong></span>
                        <span>Exhaustion: <strong>${exScore}/100 (${_escAlertHtml(exState)})</strong></span>
                    </div>
                    <div class="peak-meter-track"><div class="peak-meter-fill ${exCls}" style="width:${Math.max(4, Math.min(100, exScore))}%"></div></div>
                    ${sigChips ? `<div class="peak-signal-chips">${sigChips}</div>` : ''}
                    ${peakBtns}
                </div>`;
        }

        const ladderRows = ladder.length
            ? ladder.map((lvl) => {
                const ordTxt = _escAlertHtml(lvl.order_text || '');
                const limF = Number(lvl.limit_usd).toFixed(2);
                const proc = lvl.est_proceeds_eur != null
                    ? `$${Number(lvl.est_proceeds_usd).toFixed(0)} / €${Number(lvl.est_proceeds_eur).toFixed(0)}`
                    : `$${Number(lvl.est_proceeds_usd).toFixed(0)}`;
                return `
                    <tr>
                        <td><strong>${_escAlertHtml(lvl.tier)}</strong> (${lvl.tranche_pct}%)</td>
                        <td>${lvl.sell_shares} sh</td>
                        <td>$${limF}</td>
                        <td>${proc}</td>
                        <td class="exec-actions-cell">
                            <button type="button" class="exec-mini-btn arm" onclick="armQuickTriggerFromPlan('${pSym}', '>=', ${Number(lvl.limit_usd)}, '${_escAlertHtml(lvl.tier)} Fib Limit')">🔔 Arm</button>
                            <button type="button" class="exec-mini-btn" onclick="copyExecutionOrderText('${ordTxt}', this)">📋 Copy</button>
                        </td>
                    </tr>`;
            }).join('')
            : '<tr><td colspan="5" class="empty-msg">Awaiting live price for Fib ladder</td></tr>';

        const t25 = (pos.tranches || {})['25pct'];
        const t50 = (pos.tranches || {})['50pct'];
        const sweepLim = pos.sweep_limit_usd != null ? Number(pos.sweep_limit_usd).toFixed(2) : null;
        const sweepRows = (t25 || t50) && sweepLim
            ? [ ['25% Defensive Trim', t25], ['50% Heavy Trim', t50] ]
                .filter(([, t]) => t && t.sell_shares)
                .map(([lbl, t]) => {
                    const ordTxt = `SELL ${t.sell_shares} ${pSym} LMT $${sweepLim}`;
                    return `
                        <div class="exec-sweep-row">
                            <span><strong>${lbl}:</strong> ${t.sell_shares} sh @ $${sweepLim} (Sweep -0.50%)</span>
                            <button type="button" class="exec-mini-btn" onclick="copyExecutionOrderText('${_escAlertHtml(ordTxt)}', this)">📋 Copy Order</button>
                        </div>`;
                }).join('')
            : '<div class="empty-msg">Awaiting live price for sweep trims</div>';

        const directivesBanner = directives.length
            ? `<div class="exec-ticket-section alert">
                    <div class="quant-section-title">🚨 Triggered Risk Directives</div>
                    ${directives.map(d => `<div class="quant-veto-item"><strong>${_escAlertHtml(d.rule_id)}</strong>: ${_escAlertHtml(d.order_text)}</div>`).join('')}
               </div>`
            : '';

        bodyEl.innerHTML = `
            ${directivesBanner}
            ${peakHtml}
            <div class="exec-ticket-section">
                <div class="quant-section-title">🛡️ MANDATE_36 Trailing Stop Audit${macroCal.shield_active ? ' (ENH_259 -50% Active)' : ''}</div>
                <div class="exec-kv-row">
                    <span>Anchor: <strong>${anchorStr}</strong></span>
                    <span>Dist: <strong>${stop.pct_distance_from_anchor != null ? `${Number(stop.pct_distance_from_anchor).toFixed(2)}%` : '—'}</strong></span>
                    <span>Stop (${stop.effective_stop_pct || 2.0}%): <strong>${stopPriceStr}</strong></span>
                    ${armStopBtn}
                </div>
            </div>
            <div class="exec-ticket-section">
                <div class="quant-section-title">🎯 ENH_96 Fibonacci Front-Run Limit Ladder (-0.25%)</div>
                <table class="exec-ticket-table">
                    <thead><tr><th>Tier</th><th>Shares</th><th>Limit</th><th>Est. Proceeds</th><th>Actions</th></tr></thead>
                    <tbody>${ladderRows}</tbody>
                </table>
            </div>
            <div class="exec-ticket-section">
                <div class="quant-section-title">⚡ Defensive Sweep Trim Tickets</div>
                ${sweepRows}
            </div>
            <div class="exec-ticket-section">
                <div class="quant-section-title">💶 MANDATE_06 Cash Reconciliation</div>
                <div class="quant-tranche-row">${_escAlertHtml(cash.proof || 'Cash proof unavailable')}</div>
            </div>
        `;
    } catch (e) {
        bodyEl.innerHTML = `<div class="quant-veto-item">Failed to load execution ticket: ${_escAlertHtml(e.message || 'error')}</div>`;
    }
}

const _closeExecModalBtn = document.getElementById('close-exec-modal');
if (_closeExecModalBtn) _closeExecModalBtn.addEventListener('click', closeExecutionPlanModal);
const _execModalOverlay = document.getElementById('execution-plan-modal-overlay');
if (_execModalOverlay) {
    _execModalOverlay.addEventListener('click', (e) => {
        if (e.target === _execModalOverlay) closeExecutionPlanModal();
    });
}
window.openExecutionPlanModal = openExecutionPlanModal;
window.armQuickTriggerFromPlan = armQuickTriggerFromPlan;
window.copyExecutionOrderText = copyExecutionOrderText;

async function fetchAnalyticsAndBacktest() {
    const btn = document.getElementById('refresh-analytics-btn');
    if (btn) btn.disabled = true;
    try {
        const [aRes, bRes] = await Promise.allSettled([
            fetch(`${API_BASE}/analytics`),
            fetch(`${API_BASE}/analytics/backtest`),
        ]);
        if (aRes.status === 'fulfilled' && aRes.value && aRes.value.ok) {
            _lastQuantAnalytics = await aRes.value.json();
        }
        if (bRes.status === 'fulfilled' && bRes.value && bRes.value.ok) {
            _lastBacktestSummary = await bRes.value.json();
        }
        if (_lastQuantAnalytics || _lastBacktestSummary) {
            renderQuantAnalyticsPanel(_lastQuantAnalytics, _lastBacktestSummary);
        }
    } catch (_) {
        /* ignore offline quant refresh errors */
    } finally {
        if (btn) btn.disabled = false;
    }
}

const _refreshAnalyticsBtn = document.getElementById('refresh-analytics-btn');
if (_refreshAnalyticsBtn) _refreshAnalyticsBtn.addEventListener('click', fetchAnalyticsAndBacktest);

window.ackAlertEvent = ackAlertEvent;
window.deleteAlertRule = deleteAlertRule;
window.toggleAlertRule = toggleAlertRule;

initSidebarToggle();

if (dAddWatchlistTicker) {
    dAddWatchlistTicker.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addToWatchlist();
    });
}

if (dAddToWatchlistBtn) dAddToWatchlistBtn.addEventListener('click', addToWatchlist);

// Shared scout runner — mode: 'sectors' | 'watchlist'
async function runScout(mode) {
    const isWatchlist = mode === 'watchlist';
    const btn = isWatchlist ? document.getElementById('scan-watchlist-btn') : dRunAiScoutBtn;
    const statusEl = isWatchlist ? document.getElementById('scan-watchlist-status') : dAiScoutStatus;
    
    if (!btn) return;
    btn.disabled = true;
    const originalHtml = btn.innerHTML;
    btn.innerHTML = isWatchlist ? '🎯 Scanning...' : '🔭 Scouting Sectors...';
    if (statusEl) {
        statusEl.textContent = isWatchlist ? 'Triggering watchlist refresh...' : 'Querying sector discovery...';
        statusEl.className = 'status-message inline-feedback active text-yellow';
    }

    try {
        const limit = dAiScoutLimitSelect ? parseInt(dAiScoutLimitSelect.value) : 2;
        const maxRsi = dAiScoutMaxRsiSelect ? parseInt(dAiScoutMaxRsiSelect.value) : 75;
        const res = await fetch(`${API_BASE}/ai_scout`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode, limit, max_rsi: maxRsi })
        });
        const data = await res.json();
        if (data.status === 'success') {
            const msg = data.message || (data.scouts && data.scouts.length > 0
                ? `Found: ${data.scouts.join(', ')}`
                : 'Scan triggered — table will update shortly.');
            showFeedback(btn, isWatchlist ? '🎯 Done! ✅' : '🔭 Triggered! ✅', msg, false, statusEl);
            pollData();
        } else {
            throw new Error(data.message);
        }
    } catch (e) {
        console.error('Scout Error:', e);
        showFeedback(btn, '❌ Failed', e.message || 'Scout request failed.', true, statusEl);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
    }
}

if (dRunAiScoutBtn) {
    dRunAiScoutBtn.addEventListener('click', () => runScout('sectors'));
}

const dScanWatchlistBtn = document.getElementById('scan-watchlist-btn');
if (dScanWatchlistBtn) {
    dScanWatchlistBtn.addEventListener('click', () => runScout('watchlist'));
}


async function copySessionReviewPayload(triggerBtn, statusEl) {
    const btn = triggerBtn || document.getElementById('btn-session-review');
    const targetStatus = statusEl || document.getElementById('outbound-review-status');
    try {
        const response = await fetch('/api/session_review_payload');
        const data = await response.json();
        
        await navigator.clipboard.writeText(data.payload);
        showFeedback(btn, "✅ Copied!", "Audit & Rule Review prompt ready! (Gem_Store holds SSoT context)", false, targetStatus);
    } catch (error) {
        console.error("Failed to copy Review payload:", error);
        showFeedback(btn, "❌ Error", "Failed to copy review prompt", true, targetStatus);
    }
}

// Clear Decision Log Handler
const dClearLogBtn = document.getElementById('clear-log-btn');
if (dClearLogBtn) {
    dClearLogBtn.addEventListener('click', async () => {
        if (!confirm("Are you sure you want to clear the entire Decision Log? This cannot be undone.")) return;
        
        const statusEl = document.getElementById('clear-log-status');
        dClearLogBtn.disabled = true;
        try {
            const res = await fetch(`${API_BASE}/clear_decision_log`, {
                method: 'POST'
            });
            const data = await res.json();
            if (data.status === 'success') {
                showFeedback(dClearLogBtn, "✅ Cleared!", "Decision log wiped.", false, statusEl);
            } else {
                throw new Error(data.message);
            }
        } catch (e) {
            console.error("Clear Log Error: ", e);
            showFeedback(dClearLogBtn, "❌ Error", e.message || "Failed to clear log.", true, statusEl);
        } finally {
            dClearLogBtn.disabled = false;
        }
    });
}

// Google Drive sync disabled in this repo



// ─── Mobile Quick Action Bridge ───
const dMobileCopyBtn = document.getElementById('mobile-copy-json-btn');
const dMobilePasteBtn = document.getElementById('mobile-paste-payload-btn');
const dMobileCopySessionBtn = document.getElementById('mobile-copy-session-btn');
const dMobileReviewBtn = document.getElementById('mobile-btn-session-review');

if (dMobileCopyBtn) dMobileCopyBtn.addEventListener('click', () => copyMarketSnapshot(dMobileCopyBtn, dMobileStatus));
if (dMobilePasteBtn) dMobilePasteBtn.addEventListener('click', () => ingestExecutionPayload(dMobilePasteBtn, dMobileStatus));
if (dMobileCopySessionBtn) dMobileCopySessionBtn.addEventListener('click', () => copySessionBoot(dMobileCopySessionBtn, dMobileStatus));
if (dMobileReviewBtn) dMobileReviewBtn.addEventListener('click', () => copySessionReviewPayload(dMobileReviewBtn, dMobileStatus));

// ─── Chart Modal: TradingView Lightweight Charts Integration ───

// Registry of open chart instances (symbol -> { chart, candleSeries })
const chartInstances = {};

// Registry of latest captured screenshots (symbol -> base64 PNG string, no prefix)
const chartScreenshots = {};

// Fib Extension State
let activeFibPriceLines = [];
let activeFibMarkers = [];
let activeFibState = null;
let fibPickingMode = false;
let fibPickPoints = [];

// DOM refs for the chart modal
const dChartModalOverlay = document.getElementById('chart-modal-overlay');
const dChartModalClose   = document.getElementById('chart-modal-close');
const dChartModalTicker  = document.getElementById('chart-modal-ticker');
const dChartModalMeta    = document.getElementById('chart-modal-meta');
const dChartContainer    = document.getElementById('chart-container');

if (dChartModalClose) {
    dChartModalClose.addEventListener('click', closeChartModal);
}
if (dChartModalOverlay) {
    dChartModalOverlay.addEventListener('click', (e) => {
        if (e.target === dChartModalOverlay) closeChartModal();
    });
}
// ESC key closes chart modal too
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && dChartModalOverlay && dChartModalOverlay.classList.contains('active')) {
        closeChartModal();
    }
});

/**
 * Automatically detects intraday swing points from 1m OHLCV bars:
 * PA: Session Low (impulse origin)
 * PB: Impulse High
 * PC: Pullback Floor (retracement low)
 */
function detectIntradaySwings(bars) {
    if (!bars || bars.length < 5) return null;

    let maxHigh = -Infinity, maxHighIdx = -1;
    let minLow = Infinity, minLowIdx = -1;

    for (let i = 0; i < bars.length; i++) {
        if (bars[i].high > maxHigh) {
            maxHigh = bars[i].high;
            maxHighIdx = i;
        }
        if (bars[i].low < minLow) {
            minLow = bars[i].low;
            minLowIdx = i;
        }
    }

    let pA = 0, pB = 0, pC = 0;
    let idxA = 0, idxB = 0, idxC = 0;

    if (minLowIdx < maxHighIdx) {
        idxA = minLowIdx;
        pA = bars[idxA].low;
        idxB = maxHighIdx;
        pB = bars[idxB].high;

        if (idxB < bars.length - 1) {
            let postLow = Infinity, postLowIdx = idxB;
            for (let j = idxB; j < bars.length; j++) {
                if (bars[j].low < postLow) {
                    postLow = bars[j].low;
                    postLowIdx = j;
                }
            }
            idxC = postLowIdx;
            pC = postLow;
        } else {
            idxC = bars.length - 1;
            pC = Math.min(bars[idxC].low, bars[idxC].close);
        }
    } else {
        let bestImpulse = 0;
        let bA = 0, bB = 0;
        let bIdxA = 0, bIdxB = 0;

        for (let i = 0; i < bars.length - 2; i++) {
            for (let j = i + 1; j < bars.length; j++) {
                const imp = bars[j].high - bars[i].low;
                if (imp > bestImpulse) {
                    bestImpulse = imp;
                    bIdxA = i;
                    bIdxB = j;
                    bA = bars[i].low;
                    bB = bars[j].high;
                }
            }
        }

        if (bestImpulse > 0) {
            idxA = bIdxA; pA = bA;
            idxB = bIdxB; pB = bB;
            if (idxB < bars.length - 1) {
                let postLow = Infinity, postLowIdx = idxB;
                for (let j = idxB; j < bars.length; j++) {
                    if (bars[j].low < postLow) {
                        postLow = bars[j].low;
                        postLowIdx = j;
                    }
                }
                idxC = postLowIdx; pC = postLow;
            } else {
                idxC = bars.length - 1; pC = bars[idxC].low;
            }
        } else {
            idxA = minLowIdx; pA = minLow;
            idxB = Math.min(bars.length - 1, minLowIdx + 1); pB = Math.max(pA + 0.05, bars[idxB].high);
            idxC = bars.length - 1; pC = bars[idxC].close;
        }
    }

    if (pB - pA <= 0) {
        pB = pA + Math.max(0.05, pA * 0.02);
    }

    return { pA, pB, pC, idxA, idxB, idxC };
}

/**
 * Render Trend-Based Fibonacci Extension price lines, markers, and HUD.
 */
function renderFibExtension(pA, pB, pC, candleSeries, bars, idxA = null, idxB = null, idxC = null) {
    if (!candleSeries || !bars || bars.length === 0) return;

    // Remove previous price lines
    activeFibPriceLines.forEach(line => {
        try { candleSeries.removePriceLine(line); } catch (_) {}
    });
    activeFibPriceLines = [];

    const impulse = pB - pA;
    if (impulse <= 0) return;

    const curPrice = bars[bars.length - 1].close;

    // Daily trading peak ratios (optimized for tight, realistic intraday profit taking)
    const ratioDefs = [
        { ratio: 0.236, label: '0.236 Trim', role: 'Early Breakout Trim', color: '#80d8ff', style: 2, width: 1 },
        { ratio: 0.382, label: '0.382 Trim', role: 'Conservative Day Trim', color: '#ffb74d', style: 2, width: 1 },
        { ratio: 0.500, label: '0.500 Mid', role: '50% Measured Expansion', color: '#ffd54f', style: 2, width: 1 },
        { ratio: 0.618, label: '0.618 Golden', role: 'Golden Ratio Trim', color: '#4caf50', style: 0, width: 2 },
        { ratio: 0.786, label: '0.786 Ext', role: '78.6% Extension', color: '#26a69a', style: 2, width: 1 },
        { ratio: 1.000, label: '1.000 Move', role: '100% Measured Move', color: '#40c4ff', style: 0, width: 2 },
        { ratio: 1.272, label: '1.272 Exp', role: '127.2% Expansion Peak', color: '#e040fb', style: 2, width: 1 },
        { ratio: 1.618, label: '1.618 Max', role: 'Max Daily Momentum Target', color: '#ff5252', style: 0, width: 2 }
    ];

    const levels = [];
    let nextRes = null;
    let nextResIdx = -1;

    // If current price is below the Daily Peak (PB), PB is the immediate resistance ceiling
    if (pB > curPrice) {
        const pbDist = Number((((pB - curPrice) / curPrice) * 100).toFixed(1));
        const pbDistStr = `${pbDist >= 0 ? '+' : ''}${pbDist}%`;
        const pLine = candleSeries.createPriceLine({
            price: pB,
            color: '#ff5252',
            lineWidth: 2,
            lineStyle: 0, // Solid
            axisLabelVisible: true,
            title: `Daily Peak $${pB.toFixed(2)} (${pbDistStr})`
        });
        activeFibPriceLines.push(pLine);

        levels.push({
            ratio: 'Peak',
            label: 'Daily Peak',
            role: 'Day High Retest (Peak)',
            price: pB,
            distPct: pbDist,
            distStr: pbDistStr,
            color: '#ff5252'
        });
        nextRes = pB;
        nextResIdx = 0;
    }

    ratioDefs.forEach((def) => {
        const lvlPrice = Number((pC + (def.ratio * impulse)).toFixed(2));
        const distPct = Number((((lvlPrice - curPrice) / curPrice) * 100).toFixed(1));
        const distStr = `${distPct >= 0 ? '+' : ''}${distPct}%`;

        const pLine = candleSeries.createPriceLine({
            price: lvlPrice,
            color: def.color,
            lineWidth: def.width,
            lineStyle: def.style,
            axisLabelVisible: true,
            title: `${def.label} $${lvlPrice.toFixed(2)} (${distStr})`
        });
        activeFibPriceLines.push(pLine);

        levels.push({
            ratio: def.ratio,
            label: def.label,
            role: def.role,
            price: lvlPrice,
            distPct: distPct,
            distStr: distStr,
            color: def.color
        });

        if (lvlPrice > curPrice && nextRes === null) {
            nextRes = lvlPrice;
            nextResIdx = levels.length - 1;
        }
    });

    // Set markers on candles
    const markers = [];
    if (idxA !== null && bars[idxA]) {
        markers.push({ time: bars[idxA].time, position: 'belowBar', color: '#4caf50', shape: 'arrowUp', text: `A: Low $${pA.toFixed(2)}` });
    }
    if (idxB !== null && bars[idxB]) {
        markers.push({ time: bars[idxB].time, position: 'aboveBar', color: '#ff5252', shape: 'arrowDown', text: `B: High $${pB.toFixed(2)}` });
    }
    if (idxC !== null && bars[idxC]) {
        markers.push({ time: bars[idxC].time, position: 'belowBar', color: '#2962ff', shape: 'arrowUp', text: `C: Floor $${pC.toFixed(2)}` });
    }
    try {
        candleSeries.setMarkers(markers);
        activeFibMarkers = markers;
    } catch (_) {}

    // Update Status Bar & HUD
    const dAnchorsText = document.getElementById('chart-fib-anchors-text');
    const dImpulseText = document.getElementById('chart-fib-hud-impulse');
    const dGrid = document.getElementById('chart-fib-levels-grid');

    if (dAnchorsText) {
        dAnchorsText.textContent = `A: $${pA.toFixed(2)} | B: $${pB.toFixed(2)} | C: $${pC.toFixed(2)} | Impulse: $${impulse.toFixed(2)}`;
    }
    if (dImpulseText) {
        dImpulseText.textContent = `Impulse: $${impulse.toFixed(2)} ($${pA.toFixed(2)} → $${pB.toFixed(2)} | Floor $${pC.toFixed(2)})`;
    }

    if (dGrid) {
        dGrid.innerHTML = levels.map((lvl, idx) => {
            const isNext = (idx === nextResIdx);
            const ratioStr = typeof lvl.ratio === 'number' ? lvl.ratio.toFixed(3) : lvl.ratio;
            return `
                <div class="fib-level-card ${isNext ? 'next-target' : ''}">
                    <div class="fib-card-ratio">
                        <span>${ratioStr}</span>
                        ${isNext ? '<span class="next-tag">NEXT</span>' : ''}
                    </div>
                    <div class="fib-card-price" style="color:${lvl.color}">$${lvl.price.toFixed(2)}</div>
                    <div class="fib-card-dist ${lvl.distPct >= 0 ? 'positive' : 'negative'}">${lvl.distStr}</div>
                    <div class="fib-card-role" title="${lvl.role}">${lvl.label}</div>
                </div>
            `;
        }).join('');
    }

    // Save active state for composite screenshot export
    activeFibState = {
        pA, pB, pC, impulse, levels, nextRes, nextResIdx, curPrice
    };
}

/**
 * Open the chart modal for a given symbol.
 * Fetches 1m OHLCV bars, builds the Lightweight Charts instance,
 * and overlays EMA 9, EMA 30, EMA 200, VWAP, VWAP Session Bands, and Volume (with 20 SMA).
 */
async function openChartModal(symbol) {
    if (!dChartModalOverlay || !dChartContainer || typeof LightweightCharts === 'undefined') {
        console.warn('[Chart] Lightweight Charts library not loaded yet.');
        return;
    }

    // Destroy any existing chart for this symbol before re-rendering
    if (chartInstances[symbol]) {
        try { chartInstances[symbol].chart.remove(); } catch (_) {}
        delete chartInstances[symbol];
    }

    // Reset Fib state
    activeFibPriceLines = [];
    activeFibMarkers = [];
    activeFibState = null;
    fibPickingMode = false;
    fibPickPoints = [];

    // Show modal with loading state
    dChartModalTicker.textContent = symbol;
    dChartModalMeta.textContent = 'Loading chart data...';
    dChartContainer.innerHTML = '<div class="chart-loading">Fetching 1m bars...</div>';
    const dChartExecBtn = document.getElementById('chart-exec-plan-btn');
    if (dChartExecBtn) {
        const heldPositions = (_lastQuantAnalytics && Array.isArray(_lastQuantAnalytics.positions))
            ? _lastQuantAnalytics.positions
            : [];
        const isHeldChart = heldPositions.some((p) => String(p.ticker || '').toUpperCase() === symbol.toUpperCase());
        if (isHeldChart) {
            dChartExecBtn.style.display = 'inline-flex';
            dChartExecBtn.onclick = () => {
                closeChartModal();
                openExecutionPlanModal(symbol);
            };
        } else {
            dChartExecBtn.style.display = 'none';
            dChartExecBtn.onclick = null;
        }
    }
    openModal(dChartModalOverlay);

    try {
        const res = await fetch(`${API_BASE}/intraday/${encodeURIComponent(symbol)}`);
        const data = await res.json();
        const bars = data.bars || [];
        const warmupCloses = data.warmup_closes || [];

        if (bars.length === 0) {
            dChartContainer.innerHTML = '<div class="chart-loading">No intraday data available.</div>';
            dChartModalMeta.textContent = 'No data returned for this symbol.';
            return;
        }

        // Clear loading state
        dChartContainer.innerHTML = '';

        // Build Lightweight Charts instance matching TradingView template
        const chart = LightweightCharts.createChart(dChartContainer, {
            width:  dChartContainer.clientWidth  || 920,
            height: dChartContainer.clientHeight || 500,
            layout: {
                background: { color: '#131722' },
                textColor:  '#9598a1',
                fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, sans-serif',
                fontSize: 11
            },
            localization: {
                locale: 'en-US',
                timeFormatter: (time) => {
                    const d = new Date(time * 1000);
                    return d.toLocaleTimeString('en-US', {
                        timeZone: 'America/New_York',
                        hour: '2-digit',
                        minute: '2-digit',
                        hour12: false
                    });
                },
                dateFormatter: (time) => {
                    const d = new Date(time * 1000);
                    return d.toLocaleDateString('en-US', {
                        timeZone: 'America/New_York',
                        year: 'numeric',
                        month: 'short',
                        day: 'numeric'
                    });
                }
            },
            grid: {
                vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
                horzLines: { color: 'rgba(255, 255, 255, 0.04)' }
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: '#758696', width: 1, style: 3, labelBackgroundColor: '#2a2e39' },
                horzLine: { color: '#758696', width: 1, style: 3, labelBackgroundColor: '#2a2e39' },
            },
            rightPriceScale: {
                borderColor: '#2a2e39',
                scaleMargins: { top: 0.1, bottom: 0.25 },
            },
            timeScale: {
                borderColor: '#2a2e39',
                timeVisible: true,
                secondsVisible: false,
                tickMarkFormatter: (time) => {
                    const d = new Date(time * 1000);
                    return d.toLocaleTimeString('en-US', {
                        timeZone: 'America/New_York',
                        hour: '2-digit',
                        minute: '2-digit',
                        hour12: false
                    });
                }
            }
        });

        // ─── Volume Histogram & Volume 20 SMA ───
        const volumeSeries = chart.addHistogramSeries({
            priceFormat: { type: 'volume' },
            priceScaleId: 'vol',
        });
        chart.priceScale('vol').applyOptions({
            scaleMargins: { top: 0.78, bottom: 0.02 },
            borderVisible: false,
        });
        volumeSeries.setData(bars.map(b => ({
            time:  b.time,
            value: b.volume,
            color: b.close >= b.open ? 'rgba(8, 153, 129, 0.55)' : 'rgba(242, 54, 69, 0.55)'
        })));

        // Compute Volume 20 SMA
        const volSmaData = [];
        for (let i = 0; i < bars.length; i++) {
            const start = Math.max(0, i - 19);
            const slice = bars.slice(start, i + 1);
            const avgVol = slice.reduce((s, b) => s + b.volume, 0) / slice.length;
            volSmaData.push({ time: bars[i].time, value: avgVol });
        }
        const volSmaSeries = chart.addLineSeries({
            priceScaleId: 'vol',
            color: '#2962ff',
            lineWidth: 1.5,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });
        volSmaSeries.setData(volSmaData);

        // ─── Candlestick series ───
        const candleSeries = chart.addCandlestickSeries({
            upColor:          '#089981',
            downColor:        '#f23645',
            borderUpColor:    '#089981',
            borderDownColor:  '#f23645',
            wickUpColor:      '#089981',
            wickDownColor:    '#f23645',
            priceLineVisible: false
        });
        candleSeries.setData(bars);

        // ─── Moving Averages (EMA 9, EMA 30, EMA 200) ───
        const closes = bars.map(b => b.close);
        const times  = bars.map(b => b.time);
        const allCloses = [...warmupCloses, ...closes];
        const warmupLen = warmupCloses.length;

        function computeEMA(allC, period) {
            if (!allC || allC.length === 0) return [];
            const k = 2 / (period + 1);
            let ema = allC[0];
            const result = [ema];
            for (let i = 1; i < allC.length; i++) {
                ema = allC[i] * k + ema * (1 - k);
                result.push(ema);
            }
            return result;
        }

        function emaToSeries(emaValues, times, warmupLen) {
            const todayEma = emaValues.slice(warmupLen);
            const series = [];
            for (let i = 0; i < todayEma.length && i < times.length; i++) {
                if (todayEma[i] != null && !isNaN(todayEma[i])) {
                    series.push({ time: times[i], value: todayEma[i] });
                }
            }
            return series;
        }

        const ema9Data   = emaToSeries(computeEMA(allCloses, 9),   times, warmupLen);
        const ema30Data  = emaToSeries(computeEMA(allCloses, 30),  times, warmupLen);
        const ema200Data = emaToSeries(computeEMA(allCloses, 200), times, warmupLen);

        // EMA 9 — Red
        const ema9Series = chart.addLineSeries({
            color: '#f23645',
            lineWidth: 1.5,
            priceLineVisible: false,
            lastValueVisible: true,
        });
        ema9Series.setData(ema9Data);

        // EMA 30 — Blue
        const ema30Series = chart.addLineSeries({
            color: '#2962ff',
            lineWidth: 1.5,
            priceLineVisible: false,
            lastValueVisible: true,
        });
        ema30Series.setData(ema30Data);

        // EMA 200 — Solid White
        const ema200Series = chart.addLineSeries({
            color: '#ffffff',
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: true,
        });
        ema200Series.setData(ema200Data);

        // ─── VWAP with Session Standard Deviation Bands ───
        function computeVWAPBands(bars) {
            let cumPV = 0, cumVol = 0;
            const vwapList = [];
            const upperList = [];
            const lowerList = [];

            // First pass: VWAP baseline
            for (let i = 0; i < bars.length; i++) {
                const b = bars[i];
                const tp = (b.high + b.low + b.close) / 3;
                cumPV += tp * b.volume;
                cumVol += b.volume;
                const v = cumVol > 0 ? cumPV / cumVol : tp;
                vwapList.push(v);
            }

            // Second pass: Cumulative volume-weighted variance for standard deviation
            let cumVar = 0;
            let cumV = 0;
            for (let i = 0; i < bars.length; i++) {
                const b = bars[i];
                const tp = (b.high + b.low + b.close) / 3;
                const v = vwapList[i];
                cumV += b.volume;
                cumVar += b.volume * Math.pow(tp - v, 2);
                const stdev = cumV > 0 ? Math.sqrt(cumVar / cumV) : 0;
                
                upperList.push({ time: b.time, value: v + 1.25 * stdev });
                lowerList.push({ time: b.time, value: Math.max(0, v - 1.25 * stdev) });
            }

            const centerList = bars.map((b, i) => ({ time: b.time, value: vwapList[i] }));
            return { centerList, upperList, lowerList };
        }

        const { centerList, upperList, lowerList } = computeVWAPBands(bars);

        // Center VWAP Baseline (Orange #ff9800)
        const vwapCenterSeries = chart.addLineSeries({
            color: '#ff9800',
            lineWidth: 1.5,
            lineStyle: LightweightCharts.LineStyle.Solid,
            priceLineVisible: false,
            lastValueVisible: true,
            crosshairMarkerVisible: true,
        });
        vwapCenterSeries.setData(centerList);

        // Upper VWAP Band (Green #089981 dashed)
        const vwapUpperSeries = chart.addLineSeries({
            color: '#089981',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });
        vwapUpperSeries.setData(upperList);

        // Lower VWAP Band (Green #089981 dashed)
        const vwapLowerSeries = chart.addLineSeries({
            color: '#089981',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });
        vwapLowerSeries.setData(lowerList);

        // Fit view
        chart.timeScale().fitContent();

        // Store instance for screenshot capture + image copy
        chartInstances[symbol] = { chart, candleSeries };

        // ─── Setup Fib Toolbar & Auto Fib ───
        const dFibAutoBtn = document.getElementById('chart-fib-auto-btn');
        const dFibPickBtn = document.getElementById('chart-fib-pick-btn');
        const dFibClearBtn = document.getElementById('chart-fib-clear-btn');
        const dFibModeText = document.getElementById('chart-fib-mode-text');
        const dFibAnchorsText = document.getElementById('chart-fib-anchors-text');

        const runAutoFib = () => {
            fibPickingMode = false;
            fibPickPoints = [];
            if (dFibAutoBtn) dFibAutoBtn.classList.add('active');
            if (dFibPickBtn) dFibPickBtn.classList.remove('active');
            if (dFibModeText) dFibModeText.textContent = 'Mode: Auto Intraday Fib Extension';

            const swings = detectIntradaySwings(bars);
            if (swings) {
                renderFibExtension(swings.pA, swings.pB, swings.pC, candleSeries, bars, swings.idxA, swings.idxB, swings.idxC);
            } else if (dFibAnchorsText) {
                dFibAnchorsText.textContent = 'Insufficient bars for auto swing detection';
            }
        };

        if (dFibAutoBtn) dFibAutoBtn.onclick = runAutoFib;

        if (dFibPickBtn) {
            dFibPickBtn.onclick = () => {
                fibPickingMode = true;
                fibPickPoints = [];
                if (dFibAutoBtn) dFibAutoBtn.classList.remove('active');
                if (dFibPickBtn) dFibPickBtn.classList.add('active');
                if (dFibModeText) dFibModeText.textContent = 'Mode: Pick Point 1 (Swing Low A)';
                if (dFibAnchorsText) dFibAnchorsText.textContent = 'Click on chart for Point 1 (Swing Low A)';
            };
        }

        if (dFibClearBtn) {
            dFibClearBtn.onclick = () => {
                fibPickingMode = false;
                fibPickPoints = [];
                if (dFibAutoBtn) dFibAutoBtn.classList.remove('active');
                if (dFibPickBtn) dFibPickBtn.classList.remove('active');
                if (dFibModeText) dFibModeText.textContent = 'Mode: Cleared';
                if (dFibAnchorsText) dFibAnchorsText.textContent = 'No active Fib lines';
                const dGrid = document.getElementById('chart-fib-levels-grid');
                if (dGrid) dGrid.innerHTML = '';
                const dImp = document.getElementById('chart-fib-hud-impulse');
                if (dImp) dImp.textContent = 'Impulse: —';
                activeFibPriceLines.forEach(line => {
                    try { candleSeries.removePriceLine(line); } catch (_) {}
                });
                activeFibPriceLines = [];
                try { candleSeries.setMarkers([]); } catch (_) {}
                activeFibState = null;
            };
        }

        // Subscribe to chart clicks for 3-point picking
        chart.subscribeClick((param) => {
            if (!fibPickingMode || !param || !param.point || !param.time) return;
            let price = null;
            try {
                price = candleSeries.coordinateToPrice(param.point.y);
            } catch (_) {}
            if (!price && param.seriesData && param.seriesData.get(candleSeries)) {
                price = param.seriesData.get(candleSeries).close;
            }
            if (!price) return;

            let barIdx = bars.findIndex(b => b.time === param.time);
            if (barIdx === -1) barIdx = bars.length - 1;

            fibPickPoints.push({ price: Number(price.toFixed(2)), time: param.time, idx: barIdx });

            if (fibPickPoints.length === 1) {
                if (dFibModeText) dFibModeText.textContent = 'Mode: Pick Point 2 (Impulse High B)';
                if (dFibAnchorsText) dFibAnchorsText.textContent = `Point A: $${fibPickPoints[0].price} | Click Point 2 (Impulse High B)...`;
            } else if (fibPickPoints.length === 2) {
                if (dFibModeText) dFibModeText.textContent = 'Mode: Pick Point 3 (Pullback Floor C)';
                if (dFibAnchorsText) dFibAnchorsText.textContent = `A: $${fibPickPoints[0].price} | B: $${fibPickPoints[1].price} | Click Point 3 (Floor C)...`;
            } else if (fibPickPoints.length === 3) {
                const ptA = fibPickPoints[0];
                const ptB = fibPickPoints[1];
                const ptC = fibPickPoints[2];

                if (dFibModeText) dFibModeText.textContent = 'Mode: Manual 3-Point Fib';
                renderFibExtension(ptA.price, ptB.price, ptC.price, candleSeries, bars, ptA.idx, ptB.idx, ptC.idx);
                fibPickingMode = false;
                fibPickPoints = [];
            }
        });

        // Default to Auto Fib immediately on chart open
        runAutoFib();

        // Update footer with exchange time (ET)
        const formatNY = (ts) => new Date(ts * 1000).toLocaleTimeString('en-US', {
            timeZone: 'America/New_York',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        });
        const firstBar = bars[0];
        const lastBar  = bars[bars.length - 1];
        const firstTime = formatNY(firstBar.time);
        const lastTime  = formatNY(lastBar.time);
        dChartModalMeta.textContent = `${bars.length} bars · ${firstTime} – ${lastTime} ET`;

        // Wire up the "📋 Copy Image" button in the modal footer
        const dCopyChartBtn = document.getElementById('chart-copy-img-btn');
        if (dCopyChartBtn) {
            dCopyChartBtn.onclick = () => copyCurrentChartImage(symbol, dCopyChartBtn);
        }

        // Handle resize
        const resizeObserver = new ResizeObserver(() => {
            if (chartInstances[symbol]) {
                chart.applyOptions({
                    width:  dChartContainer.clientWidth,
                    height: dChartContainer.clientHeight
                });
            }
        });
        resizeObserver.observe(dChartContainer);

        // Immediately capture and stream screenshot for this symbol
        setTimeout(() => captureChartScreenshot(symbol), 250);

    } catch (err) {
        console.error('[Chart] Failed to load chart data:', err);
        dChartContainer.innerHTML = '<div class="chart-loading">Failed to load chart data.</div>';
        dChartModalMeta.textContent = 'Error fetching data.';
    }
}

/**
 * Close the chart modal and clean up the active chart instance.
 */
function closeChartModal() {
    closeModal(dChartModalOverlay);
    const sym = dChartModalTicker ? dChartModalTicker.textContent : null;
    if (sym && chartInstances[sym]) {
        try { chartInstances[sym].chart.remove(); } catch (_) {}
        delete chartInstances[sym];
    }
    activeFibPriceLines = [];
    activeFibMarkers = [];
    activeFibState = null;
    fibPickingMode = false;
    fibPickPoints = [];
    if (dChartContainer) dChartContainer.innerHTML = '';
}

/**
 * Capture a screenshot of a specific chart instance, store in chartScreenshots,
 * and stream it to the backend endpoint /api/save_chart_screenshots.
 * @param {string} symbol
 */
async function captureChartScreenshot(symbol) {
    const instance = chartInstances[symbol];
    if (!instance || !instance.chart) return;
    try {
        const canvas = instance.chart.takeScreenshot();
        if (!canvas) return;
        const dataUrl = canvas.toDataURL('image/png');
        // Strip data URI prefix for clean storage
        const rawB64 = dataUrl.replace(/^data:image\/png;base64,/, '');
        chartScreenshots[symbol] = rawB64;

        // Stream screenshot to backend for Gemini Multimodal prompt ingestion
        await fetch(`${API_BASE}/save_chart_screenshots`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ screenshots: { [symbol]: rawB64 } })
        });
    } catch (e) {
        console.warn(`[Chart] Screenshot capture/stream failed for ${symbol}:`, e);
    }
}

/**
 * Capture and stream screenshots for all currently open chart instances.
 * Called automatically at end of each pollData() cycle.
 */
function captureAllChartScreenshots() {
    Object.keys(chartInstances).forEach(sym => captureChartScreenshot(sym));
}

/**
 * Copy the current chart image to clipboard as a PNG and formatted markdown text.
 * Composites the Resistance Targets HUD table directly onto the chart canvas.
 * @param {string} symbol
 * @param {HTMLElement} btn
 */
async function copyCurrentChartImage(symbol, btn) {
    const instance = chartInstances[symbol];
    if (!instance || !instance.chart) {
        console.warn('[Chart] No active chart instance for', symbol);
        return;
    }
    const originalText = btn ? btn.textContent : '';
    try {
        if (btn) { btn.textContent = 'Copying...'; btn.disabled = true; }

        const srcCanvas = instance.chart.takeScreenshot();
        if (!srcCanvas) throw new Error('takeScreenshot returned null');

        // Create composite canvas matching original dimensions
        const compCanvas = document.createElement('canvas');
        compCanvas.width = srcCanvas.width;
        compCanvas.height = srcCanvas.height;
        const ctx = compCanvas.getContext('2d');

        // 1. Draw base chart screenshot
        ctx.drawImage(srcCanvas, 0, 0);

        let markdownTargets = `### ${symbol} · Trend-Based Fibonacci Extension Resistance Targets\n\n`;

        // 2. Draw stylized Resistance Targets HUD overlay onto canvas if activeFibState exists
        if (activeFibState && activeFibState.levels && activeFibState.levels.length > 0) {
            const state = activeFibState;
            markdownTargets += `**Anchors:** A=$${state.pA.toFixed(2)} | B=$${state.pB.toFixed(2)} | C=$${state.pC.toFixed(2)} | Impulse=$${state.impulse.toFixed(2)}\n\n`;
            markdownTargets += `| Level | Ratio | Target Price | Distance | Role |\n`;
            markdownTargets += `| :--- | :--- | :--- | :--- | :--- |\n`;
            state.levels.forEach(lvl => {
                const nextMarker = (lvl.price === state.nextRes) ? ' 🎯 NEXT' : '';
                const ratioStr = typeof lvl.ratio === 'number' ? lvl.ratio.toFixed(3) : lvl.ratio;
                markdownTargets += `| ${lvl.label}${nextMarker} | ${ratioStr} | $${lvl.price.toFixed(2)} | ${lvl.distStr} | ${lvl.role} |\n`;
            });

            // Draw HUD overlay card at top-left
            const hudX = 16;
            const hudY = 16;
            const hudW = 330;
            const hudH = 36 + (state.levels.length * 20) + 10;

            ctx.save();
            ctx.fillStyle = 'rgba(19, 23, 34, 0.94)';
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.16)';
            ctx.lineWidth = 1;
            if (typeof ctx.roundRect === 'function') {
                ctx.beginPath();
                ctx.roundRect(hudX, hudY, hudW, hudH, 8);
                ctx.fill();
                ctx.stroke();
            } else {
                ctx.fillRect(hudX, hudY, hudW, hudH);
                ctx.strokeRect(hudX, hudY, hudW, hudH);
            }

            // Title
            ctx.fillStyle = '#58a6ff';
            ctx.font = 'bold 12px Inter, sans-serif';
            ctx.fillText(`🎯 ${symbol} Fib Resistance Targets`, hudX + 12, hudY + 20);

            // Subtitle / Anchors
            ctx.fillStyle = '#8b949e';
            ctx.font = '10px monospace';
            ctx.fillText(`A:$${state.pA.toFixed(2)}  B:$${state.pB.toFixed(2)}  C:$${state.pC.toFixed(2)}  Imp:$${state.impulse.toFixed(2)}`, hudX + 12, hudY + 34);

            // Level Rows
            let rowY = hudY + 52;
            state.levels.forEach((lvl, idx) => {
                const isNext = (idx === state.nextResIdx);
                if (isNext) {
                    ctx.fillStyle = 'rgba(255, 215, 0, 0.15)';
                    ctx.fillRect(hudX + 8, rowY - 12, hudW - 16, 18);
                }

                // Level Label
                ctx.fillStyle = isNext ? '#ffd700' : '#e6edf3';
                ctx.font = isNext ? 'bold 10px Inter, sans-serif' : '10px Inter, sans-serif';
                ctx.fillText(lvl.label, hudX + 12, rowY);

                // Price
                ctx.fillStyle = lvl.color || '#ffffff';
                ctx.font = 'bold 11px monospace';
                ctx.fillText(`$${lvl.price.toFixed(2)}`, hudX + 175, rowY);

                // Distance %
                ctx.fillStyle = lvl.distPct >= 0 ? '#4caf50' : '#ff5252';
                ctx.font = '10px monospace';
                ctx.fillText(lvl.distStr, hudX + 245, rowY);

                if (isNext) {
                    ctx.fillStyle = '#ffd700';
                    ctx.font = 'bold 9px sans-serif';
                    ctx.fillText('NEXT', hudX + 295, rowY);
                }

                rowY += 20;
            });
            ctx.restore();
        }

        // Convert canvas to Blob
        const blob = await new Promise((resolve, reject) =>
            compCanvas.toBlob(b => b ? resolve(b) : reject(new Error('toBlob failed')), 'image/png')
        );

        // Write both image/png AND text/plain markdown to clipboard
        try {
            const textBlob = new Blob([markdownTargets], { type: 'text/plain' });
            await navigator.clipboard.write([
                new ClipboardItem({
                    'image/png': blob,
                    'text/plain': textBlob
                })
            ]);
        } catch (clipErr) {
            // Fallback to image-only clipboard write if text/plain dual-item is unsupported
            await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        }

        if (btn) { btn.textContent = '✅ Copied!'; }
        setTimeout(() => { if (btn) { btn.textContent = originalText; btn.disabled = false; } }, 1800);
    } catch (e) {
        console.error('[Chart] Image copy failed:', e);
        if (btn) { btn.textContent = '❌ Failed'; }
        setTimeout(() => { if (btn) { btn.textContent = originalText; btn.disabled = false; } }, 2000);
    }
}

/**
 * Render and capture up-to-date TradingView 1m candlestick charts for target tickers offscreen.
 * If targetTickers is not specified, it automatically resolves:
 * 1. All active portfolio tickers
 * 2. SPY benchmark
 * 3. Active watchlist tickers (top 5)
 * Streams all captured base64 PNGs to /api/save_chart_screenshots in one payload.
 */
async function captureTargetChartScreenshots(targetTickers = null) {
    if (typeof LightweightCharts === 'undefined') {
        console.warn('[Chart] LightweightCharts library not loaded, skipping automated capture.');
        return;
    }

    let tickers = [];
    if (Array.isArray(targetTickers) && targetTickers.length > 0) {
        tickers = targetTickers.map(t => t.toUpperCase());
    } else {
        let pTickers = [];
        try {
            if (typeof getCurrentPortfolio === 'function') {
                const port = getCurrentPortfolio();
                if (Array.isArray(port)) {
                    pTickers = port.map(p => (p.ticker || '').toUpperCase()).filter(Boolean);
                }
            }
        } catch (_) {}

        let wTickers = [];
        try {
            const wRes = await fetch(`${API_BASE}/watchlist`);
            const wData = await wRes.json();
            if (Array.isArray(wData)) {
                wTickers = wData.map(t => (typeof t === 'string' ? t : (t.ticker || '')).toUpperCase()).filter(Boolean);
            }
        } catch (_) {}
        
        if (wTickers.length === 0) {
            try {
                const wItems = document.querySelectorAll('.watchlist-item span');
                if (wItems && wItems.length > 0) {
                    wItems.forEach(el => {
                        const text = el.textContent.trim().toUpperCase();
                        if (text && !text.includes('NO TICKERS') && !text.includes('LOADING')) {
                            wTickers.push(text);
                        }
                    });
                }
            } catch (_) {}
        }

        tickers = [...new Set([...pTickers, ...wTickers, 'SPY'])];
    }

    if (tickers.length === 0) return;

    let offscreenContainer = document.getElementById('offscreen-chart-container');
    if (!offscreenContainer) {
        offscreenContainer = document.createElement('div');
        offscreenContainer.id = 'offscreen-chart-container';
        offscreenContainer.style.cssText = 'position: fixed; left: -9999px; top: -9999px; width: 920px; height: 500px; opacity: 0; pointer-events: none;';
        document.body.appendChild(offscreenContainer);
    }

    const capturedMap = {};

    // Fetch intraday data in parallel
    const fetchPromises = tickers.map(async (sym) => {
        try {
            const res = await fetch(`${API_BASE}/intraday/${encodeURIComponent(sym)}`);
            const data = await res.json();
            return { symbol: sym, data };
        } catch (e) {
            console.warn(`[Chart] Automated fetch failed for ${sym}:`, e);
            return { symbol: sym, data: null };
        }
    });

    const results = await Promise.all(fetchPromises);

    for (const { symbol, data } of results) {
        if (!data || !data.bars || data.bars.length === 0) continue;
        const bars = data.bars;
        const warmupCloses = data.warmup_closes || [];

        try {
            offscreenContainer.innerHTML = '';
            const chart = LightweightCharts.createChart(offscreenContainer, {
                width: 920,
                height: 500,
                layout: {
                    background: { color: '#131722' },
                    textColor: '#9598a1',
                    fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, sans-serif',
                    fontSize: 11
                },
                localization: {
                    locale: 'en-US',
                    timeFormatter: (time) => {
                        const d = new Date(time * 1000);
                        return d.toLocaleTimeString('en-US', {
                            timeZone: 'America/New_York',
                            hour: '2-digit',
                            minute: '2-digit',
                            hour12: false
                        });
                    }
                },
                grid: {
                    vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
                    horzLines: { color: 'rgba(255, 255, 255, 0.04)' }
                },
                crosshair: {
                    mode: LightweightCharts.CrosshairMode.Normal,
                },
                rightPriceScale: {
                    borderColor: '#2a2e39',
                    scaleMargins: { top: 0.1, bottom: 0.25 },
                },
                timeScale: {
                    borderColor: '#2a2e39',
                    timeVisible: true,
                    secondsVisible: false
                }
            });

            // Volume
            const volumeSeries = chart.addHistogramSeries({
                priceFormat: { type: 'volume' },
                priceScaleId: 'vol',
            });
            chart.priceScale('vol').applyOptions({
                scaleMargins: { top: 0.78, bottom: 0.02 },
                borderVisible: false,
            });
            volumeSeries.setData(bars.map(b => ({
                time: b.time,
                value: b.volume,
                color: b.close >= b.open ? 'rgba(8, 153, 129, 0.55)' : 'rgba(242, 54, 69, 0.55)'
            })));

            // Volume 20 SMA
            const volSmaData = [];
            for (let i = 0; i < bars.length; i++) {
                const start = Math.max(0, i - 19);
                const slice = bars.slice(start, i + 1);
                const avgVol = slice.reduce((s, b) => s + b.volume, 0) / slice.length;
                volSmaData.push({ time: bars[i].time, value: avgVol });
            }
            const volSmaSeries = chart.addLineSeries({
                priceScaleId: 'vol',
                color: '#2962ff',
                lineWidth: 1.5,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            volSmaSeries.setData(volSmaData);

            // Candlesticks
            const candleSeries = chart.addCandlestickSeries({
                upColor: '#089981',
                downColor: '#f23645',
                borderUpColor: '#089981',
                borderDownColor: '#f23645',
                wickUpColor: '#089981',
                wickDownColor: '#f23645',
                priceLineVisible: false
            });
            candleSeries.setData(bars);

            // EMAs (9, 30, 200)
            const closes = bars.map(b => b.close);
            const times = bars.map(b => b.time);
            const allCloses = [...warmupCloses, ...closes];
            const warmupLen = warmupCloses.length;

            function computeEMA(allC, period) {
                if (!allC || allC.length === 0) return [];
                const k = 2 / (period + 1);
                let ema = allC[0];
                const result = [ema];
                for (let i = 1; i < allC.length; i++) {
                    ema = allC[i] * k + ema * (1 - k);
                    result.push(ema);
                }
                return result;
            }

            function emaToSeries(emaValues, times, warmupLen) {
                const todayEma = emaValues.slice(warmupLen);
                const series = [];
                for (let i = 0; i < todayEma.length && i < times.length; i++) {
                    if (todayEma[i] != null && !isNaN(todayEma[i])) {
                        series.push({ time: times[i], value: todayEma[i] });
                    }
                }
                return series;
            }

            const ema9Data = emaToSeries(computeEMA(allCloses, 9), times, warmupLen);
            const ema30Data = emaToSeries(computeEMA(allCloses, 30), times, warmupLen);
            const ema200Data = emaToSeries(computeEMA(allCloses, 200), times, warmupLen);

            const ema9Series = chart.addLineSeries({ color: '#f23645', lineWidth: 1.5, priceLineVisible: false });
            ema9Series.setData(ema9Data);

            const ema30Series = chart.addLineSeries({ color: '#2962ff', lineWidth: 1.5, priceLineVisible: false });
            ema30Series.setData(ema30Data);

            const ema200Series = chart.addLineSeries({ color: '#ffffff', lineWidth: 2, priceLineVisible: false });
            ema200Series.setData(ema200Data);

            // VWAP Bands
            let cumPV = 0, cumVol = 0;
            const vwapList = [];
            const upperList = [];
            const lowerList = [];
            for (let i = 0; i < bars.length; i++) {
                const b = bars[i];
                const tp = (b.high + b.low + b.close) / 3;
                cumPV += tp * b.volume;
                cumVol += b.volume;
                vwapList.push(cumVol > 0 ? cumPV / cumVol : tp);
            }
            let cumVar = 0, cumV = 0;
            for (let i = 0; i < bars.length; i++) {
                const b = bars[i];
                const tp = (b.high + b.low + b.close) / 3;
                const v = vwapList[i];
                cumV += b.volume;
                cumVar += b.volume * Math.pow(tp - v, 2);
                const stdev = cumV > 0 ? Math.sqrt(cumVar / cumV) : 0;
                upperList.push({ time: b.time, value: v + 1.25 * stdev });
                lowerList.push({ time: b.time, value: Math.max(0, v - 1.25 * stdev) });
            }

            const centerList = bars.map((b, i) => ({ time: b.time, value: vwapList[i] }));

            const vwapCenterSeries = chart.addLineSeries({
                color: '#ff9800',
                lineWidth: 1.5,
                lineStyle: LightweightCharts.LineStyle.Solid,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            vwapCenterSeries.setData(centerList);

            const vwapUpperSeries = chart.addLineSeries({
                color: '#089981',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            vwapUpperSeries.setData(upperList);

            const vwapLowerSeries = chart.addLineSeries({
                color: '#089981',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            vwapLowerSeries.setData(lowerList);

            chart.timeScale().fitContent();

            // Take canvas screenshot
            const canvas = chart.takeScreenshot();
            if (canvas) {
                const rawB64 = canvas.toDataURL('image/png').replace(/^data:image\/png;base64,/, '');
                capturedMap[symbol] = rawB64;
                chartScreenshots[symbol] = rawB64;
            }

            chart.remove();
        } catch (renderErr) {
            console.warn(`[Chart] Offscreen render failed for ${symbol}:`, renderErr);
        }
    }

    offscreenContainer.innerHTML = '';

    // Stream all captured screenshots to the backend
    if (Object.keys(capturedMap).length > 0) {
        try {
            await fetch(`${API_BASE}/save_chart_screenshots`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ screenshots: capturedMap, replace_all: true })
            });
            console.log(`[Chart] Successfully captured & streamed ${Object.keys(capturedMap).length} fresh 1m chart(s):`, Object.keys(capturedMap));
        } catch (saveErr) {
            console.error('[Chart] Failed to stream captured screenshots to backend:', saveErr);
        }
    }
}

window.captureTargetChartScreenshots = captureTargetChartScreenshots;
