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
let currentEurUsdRate = 1.08; // Store EURUSD rate globally for real-time conversion
let latestTickersMap = {};

const dCopyBtn = document.getElementById('copy-json-btn');
const dCopySessionBtn = document.getElementById('copy-session-btn');
const dNewsScanBtn = document.getElementById('btn-news-scan');
const dPasteBtn = document.getElementById('paste-payload-btn');
const dMobileStatus = document.getElementById('mobile-data-status');

// Manager Elements
const dPortfolioBody = document.getElementById('portfolio-manager-body');
const dWatchlistContainer = document.getElementById('watchlist-manager-container');
const dAddPortfolioTicker = document.getElementById('add-portfolio-ticker');
const dAddToPortfolioBtn = document.getElementById('add-to-portfolio-btn');
const dSavePortfolioBtn = document.getElementById('save-portfolio-btn');
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
    if (btn.dataset.isFeedback === "true") return; // Prevent re-triggering during active feedback
    
    btn.dataset.isFeedback = "true";
    const originalHtml = btn.innerHTML;
    btn.innerHTML = btnText;
    btn.classList.add(isError ? 'btn-error' : 'btn-success');
    
    // Use per-button inline feedback if available, otherwise fallback
    const target = statusEl || dDataStatus;
    if (target) {
        target.textContent = statusMsg;
        target.className = `status-message inline-feedback active ${isError ? 'text-red' : 'text-green'}`;
    }
    
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

    const userWatchlist = new Set((state.watchlist || []).map(s => s.toUpperCase()));
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

    let html = '';

    const renderRow = (row) => {
        const sym = row.ticker;
        const p = row.price.toFixed(2);
        
        let pClass = '';
        if(prevPrices[sym]) {
            if(p > prevPrices[sym]) pClass = 'flash-up';
            else if(p < prevPrices[sym]) pClass = 'flash-down';
        }
        prevPrices[sym] = p;

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
            
            let tagClass = 't1';
            let tagPrefix = '🎯 T1';
            if (label.includes('Peak')) {
                tagClass = 'peak';
                tagPrefix = '🎯 Peak';
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
            
            const tooltip = `Fib Anchors: A=$${fib.pA || '—'}, B=$${fib.pB || '—'}, C=$${fib.pC || '—'} | Next: ${label} $${nextPrice} (${distStr}) | T1=$${fib.t1_100 || '—'} | T2=$${fib.t2_1618 || '—'} | T3=$${fib.t3_2618 || '—'}`;
            if (fib.daily_peak_target && Number(fib.daily_peak_target) > 0) {
                const peakTarget = `$${Number(fib.daily_peak_target).toFixed(2)}`;
                const atrConfBadge = fib.atr_confluence ? `<span class="fib-atr-confluence-badge" title="ATR Confluence detected">⚡ ATR</span>` : '';
                fibTargetHtml = `
                    <div class="fib-target-cell-wrapper">
                        <span class="fib-target-tag ${tagClass}" title="${tooltip}">${tagPrefix} $${nextPrice} (${distStr})</span>
                        <div class="fib-peak-indicator" title="Projected Daily Trading Peak Target: ${peakTarget}">Daily Peak: <strong>${peakTarget}</strong> ${atrConfBadge}</div>
                    </div>
                `;
            } else {
                fibTargetHtml = `<span class="fib-target-tag ${tagClass}" title="${tooltip}">${tagPrefix} $${nextPrice} (${distStr})</span>`;
            }
        }

        const scoutIndicator = row._isScout ? `<span class="scout-dot"></span>` : '';
        return `
            <tr class="chart-clickable" onclick="openChartModal('${sym}')" title="Click to view 1m TradingView chart">
                <td class="ticker-cell ${row._isScout ? 'is-scout' : ''}">
                    <span class="ticker-symbol ${dayColor}">${sym}</span>
                    ${scoutIndicator}
                </td>
                <td class="${pClass}">${p}</td>
                <td class="${dayColor}">${row.session_change_pct > 0 ? '+' : ''}${row.session_change_pct.toFixed(2)}%</td>
                <td class="${gapColor}">${row.gap_percent > 0 ? '+' : ''}${row.gap_percent.toFixed(2)}%</td>
                <td>${formatVol(row.volume)}</td>
                <td>${row.atr_percent.toFixed(2)}%</td>
                <td class="${rsiColor}">${row.rsi.toFixed(1)}</td>
                <td>${row.vwap > 0 ? row.vwap.toFixed(2) : '—'}</td>
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

    const renderHeader = (label, cls = '') => `
        <tr class="table-section-header ${cls}">
            <td colspan="13">${label}</td>
        </tr>
    `;

    if (groups.held.length > 0) {
        html += renderHeader('Your Portfolio', 'portfolio-header');
        groups.held.forEach(t => html += renderRow(t));
    }

    if (groups.watchlist.length > 0 || groups.scouts.length > 0) {
        html += renderHeader('Strategic Watchlist', 'watchlist-header');
        
        if (groups.watchlist.length > 0) {
            groups.watchlist.forEach(t => html += renderRow(t));
        }

        if (groups.scouts.length > 0) {
            html += `
                <tr class="table-sub-header scout-header">
                    <td colspan="13">Scout Intelligence Suggestions</td>
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
                    const label = MACRO_LABELS[tickerStr] || tickerStr;
                    const title = label === tickerStr ? tickerStr : `${label} (${tickerStr})`;
                    
                    if (row) {
                        latestMacroData[tickerStr] = row;
                        const changeVal = row.session_change_pct || 0;
                        const changeStr = (changeVal > 0 ? '+' : '') + changeVal.toFixed(2) + '%';
                        const changeColor = changeVal > 0 ? 'text-green' : 'text-red';
                        
                        let gexLine = '';
                        if (row.net_gex_total !== undefined && row.net_gex_total !== 0) {
                            const gexVal = row.net_gex_total.toFixed(3);
                            let gexColor = 'text-muted';
                            if (row.net_gex_total > 0.005) {
                                gexColor = 'text-green';
                            } else if (row.net_gex_total < -0.005) {
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
                        }

                        hudHtml += `
                            <div class="macro-card glass-panel" id="macro-card-${tickerStr.replace(/[^a-zA-Z0-9]/g, '')}">
                                <h3>${title}</h3>
                                <div class="macro-val">${(row.price || 0).toFixed(2)}</div>
                                <div class="macro-gap ${changeColor}"><span>${changeStr}</span>${gexLine}</div>
                            </div>
                        `;
                    } else {
                        hudHtml += `
                            <div class="macro-card glass-panel empty-card" id="macro-card-${tickerStr.replace(/[^a-zA-Z0-9]/g, '')}">
                                <h3>${title}</h3>
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
                
                if(vix && vix.price > 20 && vix.gap_percent > 2.0) {
                    alertsHtml += `<div class="alert-item critical">⚠️ FEAR ALERT: VIX SPIKING (+${vix.gap_percent.toFixed(2)}%)</div>`;
                }
                if(ief && ief.gap_percent < -0.15) {
                    alertsHtml += `<div class="alert-item warning">📉 BOND ALERT: YIELDS RISING</div>`;
                }
                
                if(alertsHtml) {
                    if (alertsContent.innerHTML !== alertsHtml) {
                        alertsContent.innerHTML = alertsHtml;
                    }
                    topBar.classList.add('has-alerts');
                } else {
                    const emptyState = '<span class="empty-state">NO ACTIVE ALERTS</span>';
                    if (alertsContent.innerHTML !== emptyState) {
                        alertsContent.innerHTML = emptyState;
                    }
                    topBar.classList.remove('has-alerts');
                }
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

// Portfolio Logic
async function fetchPortfolio(force = false) {
    if (!force && document.activeElement && document.activeElement.classList.contains('portfolio-input')) return;
    try {
        const res = await fetch(`${API_BASE}/basket`);
        const data = await res.json();
        renderPortfolio(data);
    } catch (e) { console.error("Portfolio fetch failed", e); }
}

function updatePortfolioTotals() {
    const portfolio = getCurrentPortfolio();
    const cashEur = getCurrentCash();
    const rate = currentEurUsdRate || 1.08;
    const cashUsd = cashEur * rate;

    const cashUsdEl = document.getElementById('cash-val-usd');
    if (cashUsdEl) {
        cashUsdEl.textContent = `$${cashUsd.toFixed(2)}`;
    }

    let totalUsd = cashUsd;
    portfolio.forEach(item => {
        const livePrice = (latestTickersMap[item.ticker] && latestTickersMap[item.ticker].price) || item.wac || 0;
        totalUsd += (item.shares || 0) * livePrice;
    });

    const totalEur = totalUsd / rate;

    const totalEurEl = document.getElementById('total-val-eur');
    const totalUsdEl = document.getElementById('total-val-usd');

    if (totalEurEl) totalEurEl.textContent = `€${Math.round(totalEur).toLocaleString()}`;
    if (totalUsdEl) totalUsdEl.textContent = `$${Math.round(totalUsd).toLocaleString()}`;
}

function renderPortfolio(data) {
    if (!dPortfolioBody) return;
    let html = '';
    const portfolio = data.portfolio || [];
    const cash = data.unallocated_cash_eur || 0;
    const rate = data.eurusd_rate || currentEurUsdRate || 1.08;
    currentEurUsdRate = rate; // Update global rate
    const cashUsd = (cash * rate).toFixed(2);
    
    let totalUsd = cash * rate;
    portfolio.forEach((item, index) => {
        const livePrice = (latestTickersMap[item.ticker] && latestTickersMap[item.ticker].price) || item.wac || 0;
        totalUsd += (item.shares || 0) * livePrice;
        html += `
            <tr data-index="${index}" class="portfolio-item-row">
                <td style="color: var(--accent); font-weight: 700; font-size: 0.8rem;">${item.ticker}</td>
                <td><input type="number" class="portfolio-input" data-key="shares" value="${item.shares || 0}" oninput="updatePortfolioTotals()" onchange="savePortfolio()"></td>
                <td><input type="number" step="0.01" class="portfolio-input" data-key="wac" value="${item.wac || 0}" oninput="updatePortfolioTotals()" onchange="savePortfolio()"></td>
                <td><button class="delete-btn" onclick="deleteFromPortfolio(${index})">&times;</button></td>
            </tr>
        `;
    });

    const totalEur = totalUsd / rate;

    html += `
        <tr class="cash-row" style="background: rgba(0, 255, 148, 0.05);">
            <td style="color: var(--green); font-weight: 700; font-size: 0.75rem;">CASH (€)</td>
            <td><input type="number" step="0.01" class="portfolio-input" id="cash-input-eur" value="${cash}" style="color: var(--green);" oninput="updatePortfolioTotals()" onchange="savePortfolio()"></td>
            <td colspan="2" id="cash-val-usd" style="font-size: 0.75rem; font-weight: 700; color: #fff; text-align: left; padding-left: 8px; font-family: var(--font-mono);">$${cashUsd}</td>
        </tr>
        <tr class="total-row" style="background: rgba(88, 166, 255, 0.08);">
            <td style="color: var(--accent); font-weight: 700; font-size: 0.75rem;">TOTAL</td>
            <td id="total-val-eur" style="font-size: 0.75rem; font-weight: 700; color: #fff; font-family: var(--font-mono);">€${Math.round(totalEur).toLocaleString()}</td>
            <td colspan="2" id="total-val-usd" style="font-size: 0.75rem; font-weight: 700; color: #fff; text-align: left; padding-left: 8px; font-family: var(--font-mono);">$${Math.round(totalUsd).toLocaleString()}</td>
        </tr>
    `;

    dPortfolioBody.innerHTML = html;
}

async function addToPortfolio() {
    const ticker = dAddPortfolioTicker.value.trim().toUpperCase();
    if (!ticker) return;
    const portfolio = getCurrentPortfolio();
    if (portfolio.find(i => i.ticker === ticker)) return;
    portfolio.push({ ticker, shares: 1, wac: 0 });
    dAddPortfolioTicker.value = '';
    renderPortfolio({
        portfolio: portfolio,
        unallocated_cash_eur: getCurrentCash(),
        eurusd_rate: currentEurUsdRate
    });
    updatePortfolioTotals();
    await savePortfolio(portfolio);
}

function getCurrentPortfolio() {
    const rows = dPortfolioBody.querySelectorAll('tr.portfolio-item-row');
    const portfolio = [];
    rows.forEach(row => {
        portfolio.push({
            ticker: (row.cells[0].textContent || '').trim().toUpperCase(),
            shares: parseFloat(row.querySelector('[data-key="shares"]').value) || 0,
            wac: parseFloat(row.querySelector('[data-key="wac"]').value) || 0
        });
    });
    return portfolio;
}

function getCurrentCash() {
    const cashInput = document.getElementById('cash-input-eur');
    return cashInput ? parseFloat(cashInput.value) || 0 : 0;
}

let isSavingPortfolio = false;
let pendingPortfolioSave = null;

async function savePortfolio(portfolioArr) {
    if (isSavingPortfolio) {
        pendingPortfolioSave = portfolioArr || getCurrentPortfolio();
        return;
    }
    isSavingPortfolio = true;
    const btn = dSavePortfolioBtn;
    if (btn) {
        btn.innerHTML = "WAIT...";
        btn.disabled = true;
    }
    try {
        const pArray = portfolioArr || getCurrentPortfolio();
        const cVal = getCurrentCash();
        const rate = currentEurUsdRate || 1.08;
        const payload = {
            portfolio: pArray,
            unallocated_cash_eur: cVal,
            unallocated_cash_usd: parseFloat((cVal * rate).toFixed(2))
        };
        const res = await fetch(`${API_BASE}/basket`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            const resData = await res.json();
            if (btn) {
                btn.innerHTML = "SYNC ✅";
                showFeedback(btn, "✅ Synced!", "Portfolio successfully updated!");
            }
            if (resData && resData.portfolio) {
                renderPortfolio({
                    portfolio: resData.portfolio,
                    unallocated_cash_eur: cVal,
                    eurusd_rate: rate
                });
            }
            updatePortfolioTotals();
            // Trigger an immediate background poll to update the main dashboard table
            await pollData();
        } else {
            if (btn) btn.innerHTML = "SYNC ❌";
        }
    } catch (e) { 
        console.error("Portfolio update failed", e); 
        if (btn) btn.innerHTML = "SYNC ❌";
    } finally {
        isSavingPortfolio = false;
        if (btn) {
            setTimeout(() => {
                btn.innerHTML = "SYNC";
                btn.disabled = false;
            }, 1000);
        }
        if (pendingPortfolioSave) {
            const next = pendingPortfolioSave;
            pendingPortfolioSave = null;
            savePortfolio(next);
        }
    }
}

async function deleteFromPortfolio(index) {
    const portfolio = getCurrentPortfolio();
    if (index >= 0 && index < portfolio.length) {
        portfolio.splice(index, 1);
        renderPortfolio({
            portfolio: portfolio,
            unallocated_cash_eur: getCurrentCash(),
            eurusd_rate: currentEurUsdRate
        });
        updatePortfolioTotals();
        await savePortfolio(portfolio);
    }
}

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
        }
    } catch (e) { console.error("Watchlist update failed", e); }
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

async function fetchScoutCategories() {
    try {
        const res = await fetch(`${API_BASE}/scout_categories`);
        const activeCategories = await res.json();
        renderScoutCategories(activeCategories);
    } catch (e) { console.error("Scout categories fetch failed", e); }
}

function renderScoutCategories(activeList) {
    if (!dScoutContainer) return;
    const activeSet = new Set(activeList);
    
    let html = '';
    VERIFIED_SCOUT_SECTORS.forEach(sector => {
        const isActive = activeSet.has(sector);
        const style = isActive 
            ? 'background: rgba(0, 255, 148, 0.2); border-color: var(--green); color: var(--green);' 
            : 'background: rgba(255, 255, 255, 0.03); border-color: var(--panel-border); color: var(--text-dim);';
        
        html += `
            <button class="scout-toggle-btn" 
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
        const res = await fetch(`${API_BASE}/scout_categories`);
        let list = await res.json();
        
        if (list.includes(sector)) {
            list = list.filter(c => c !== sector);
        } else {
            list.push(sector);
        }
        
        await saveScoutCategories(list);
    } catch (e) { console.error("Toggle failed", e); }
}

async function saveScoutCategories(list) {
    try {
        const res = await fetch(`${API_BASE}/scout_categories`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(list)
        });
        if (res.ok) {
            const updated = await res.json();
            renderScoutCategories(updated.categories);
            pollData(); // Force immediate refresh to pull newly scanned scouts
        }
    } catch (e) { console.error("Scout categories update failed", e); }
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
window.deleteFromPortfolio = deleteFromPortfolio;
window.deleteFromWatchlist = deleteFromWatchlist;
window.toggleScoutCategory = toggleScoutCategory;
window.toggleSection = toggleSection;
window.copySessionReviewPayload = copySessionReviewPayload;
window.runScout = runScout;
window.updatePortfolioTotals = updatePortfolioTotals;

if (dAddPortfolioTicker) {
    dAddPortfolioTicker.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addToPortfolio();
    });
}
if (dAddWatchlistTicker) {
    dAddWatchlistTicker.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addToWatchlist();
    });
}

if (dAddToPortfolioBtn) dAddToPortfolioBtn.addEventListener('click', addToPortfolio);
if (dSavePortfolioBtn) dSavePortfolioBtn.addEventListener('click', () => savePortfolio());
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


// Real-time conversion feedback as user types cash value
dPortfolioBody.addEventListener('input', (e) => {
    if (e.target.id === 'cash-input-eur') {
        const val = parseFloat(e.target.value) || 0;
        const usdVal = (val * currentEurUsdRate).toFixed(2);
        const usdDisplay = e.target.closest('tr').querySelector('td[colspan="2"]');
        if (usdDisplay) {
            usdDisplay.textContent = `$${usdVal}`;
        }
    }
});

// Auto-save on change/blur of portfolio inputs
dPortfolioBody.addEventListener('change', async (e) => {
    if (e.target.classList.contains('portfolio-input')) {
        await savePortfolio();
    }
});

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
