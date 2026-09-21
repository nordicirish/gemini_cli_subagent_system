const API_BASE = '/api';

// DOM Elements
const dTableBody = document.getElementById('table-body');
const dStatus = document.getElementById('market-status');
const dUpdated = document.getElementById('last-updated');
const dIndicator = document.getElementById('live-indicator');


const dIndicesInput = document.getElementById('indices-input');
const dUpdateIndicesBtn = document.getElementById('update-indices-btn');
const dIStatus = document.getElementById('indices-status');
const dDynamicMacroCards = document.getElementById('dynamic-macro-cards');

let currentMacroTickers = [];
let MACRO_LABELS = {};
let currentEurUsdRate = 1.08; // Store EURUSD rate globally for real-time conversion

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
const dPortfolioStatus = document.getElementById('portfolio-status');
const dWatchlistStatus = document.getElementById('watchlist-status');
const dDataStatus = dPortfolioStatus; // Safe fallback alias

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
let latestTickersData = {};

// Generic modal helpers
function openModal(overlay) { overlay.classList.add('active'); }
function closeModal(overlay) { overlay.classList.remove('active'); }



// Indices Modal
dOpenIndicesBtn.addEventListener('click', () => {
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
    const html = currentMacroTickers.map(ticker => {
        const d = latestMacroData[ticker];
        const label = MACRO_LABELS[ticker] || ticker;
        
        if (!d) return `
            <div class="index-card">
                <div class="index-name">${label}</div>
                <div class="index-price text-muted">—</div>
                <div class="index-gap text-muted">Awaiting data</div>
            </div>`;

        const changeVal = d.session_change_pct || 0;
        const changeStr = (changeVal > 0 ? '+' : '') + changeVal.toFixed(2) + '%';
        const changeColor = changeVal > 0 ? 'text-green' : (changeVal < 0 ? 'text-red' : 'text-white');

        let details = '';
        if (d.rsi) details += `RSI ${d.rsi.toFixed(1)}`;
        if (d.atr_percent) details += ` · ATR ${d.atr_percent.toFixed(2)}%`;
        if (d.volume) details += ` · Vol ${formatVol(d.volume)}`;
        if (d.rvol !== undefined && d.rvol !== null && !isNaN(d.rvol)) details += ` · RVOL ${Number(d.rvol).toFixed(2)}x`;
        if (d.vwap && d.vwap > 0) details += ` · VWAP ${d.vwap.toFixed(2)}`;
        if (d.net_gex_total !== undefined && d.net_gex_total !== 0) {
            const gexVal = d.net_gex_total.toFixed(3);
            
            const diff = d.gex_diff || 0;
            let chevron = '';
            if (diff > 0.005) {
                chevron = `<span class="text-green" style="margin-left: 2px; font-weight: bold;">▲</span>`;
            } else if (diff < -0.005) {
                chevron = `<span class="text-red" style="margin-left: 2px; font-weight: bold;">▼</span>`;
            }
            
            details += ` · GEX ${gexVal}${chevron}`;
        }

        let trendStr = '';
        if (d.trend === 'UP') trendStr = ' · ▲ Up';
        else if (d.trend === 'DOWN') trendStr = ' · ▼ Down';
        else trendStr = ' · — Flat';

        return `
            <div class="index-card">
                <div class="index-name">${label} (${ticker})</div>
                <div class="index-price">${d.price.toFixed(2)}</div>
                <div class="index-gap ${changeColor}">${changeStr}${trendStr}</div>
                <div class="index-details">${details}</div>
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
    await fetchGDriveConfig();
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
        showFeedback(triggerBtn, "✅ Copied!", "Market snapshot prompt ready!", false, statusEl);
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
        showFeedback(triggerBtn, "✅ Copied!", "Session boot prompt ready! (SSoT JSON payload attached)", false, statusEl);
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
        showFeedback(triggerBtn, "✅ Copied!", "News scan prompt ready! (SSoT JSON payload attached)", false, statusEl);

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

    // Get held tickers from portfolio snapshot
    const ssot = state.local_storage_state || {};
    const ms = ssot.mutable_state || {};
    const portfolio = ms.portfolio_snapshot || ssot.portfolio_snapshot || [];
    const heldTickers = new Set(portfolio.map(p => p.ticker.toUpperCase()));

    // Get macro tickers to exclude
    const MACRO_TICKERS = currentMacroTickers && currentMacroTickers.length > 0 
        ? currentMacroTickers.map(t => t.toUpperCase())
        : ['SPY', '^VIX', 'UUP', 'IEF', 'GLD', 'GDX'].map(t => t.toUpperCase());

    // Group tickers
    const groups = {
        held: [],
        watchlist: [],
        scouts: []
    };

    const userWatchlist = new Set((state.watchlist || []).map(s => s.toUpperCase()));

    tickers.forEach(t => {
        const sym = t.ticker.toUpperCase();
        if (MACRO_TICKERS.includes(sym) || sym === 'EURUSD=X') return;

        if (heldTickers.has(sym)) {
            groups.held.push(t);
        } else if (userWatchlist.has(sym)) {
            groups.watchlist.push(t);
        } else {
            groups.scouts.push(t);
        }
    });

    // Guarantee every held portfolio stock is rendered even if polling tick is in progress
    portfolio.forEach(pItem => {
        const sym = (pItem.ticker || '').toUpperCase();
        if (!sym || MACRO_TICKERS.includes(sym)) return;
        if (!groups.held.some(t => t.ticker.toUpperCase() === sym)) {
            const existing = tickers.find(t => t.ticker.toUpperCase() === sym);
            if (existing) {
                groups.held.push(existing);
            } else {
                const p = parseFloat(pItem.price) || parseFloat(pItem.wac) || 0;
                groups.held.push({
                    ticker: sym,
                    price: p,
                    prev_close: p,
                    session_change_pct: 0,
                    change_from_open_pct: 0,
                    gap_percent: 0,
                    rsi: 50,
                    macd: 0.0,
                    macd_signal: 0.0,
                    macd_hist: 0.0,
                    macd_slope: 'EXPANDING_POSITIVE',
                    macd_status: 'NEUTRAL',
                    score: pItem.score || 0,
                    trend: 'FLAT',
                    note: 'Held'
                });
            }
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

        const scoutIndicator = row._isScout ? `<span class="scout-dot"></span>` : '';
        const rvolVal = (row.rvol !== undefined && row.rvol !== null && !isNaN(row.rvol)) ? Number(row.rvol) : null;
        let rvolStr = '—';
        let rvolColor = 'text-white';
        if (rvolVal !== null) {
            rvolStr = `${rvolVal.toFixed(2)}x`;
            if (rvolVal >= 2.0) {
                rvolColor = 'text-green';
            } else if (rvolVal < 0.5) {
                rvolColor = 'text-muted';
            }
        }

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
        let fibTargetHtml = '<span class="fib-target-tag none">—</span>';
        if (row.fib_forecast && row.fib_forecast.next_resistance) {
            const nextPrice = Number(row.fib_forecast.next_resistance).toFixed(2);
            const distPct = (row.fib_forecast.distance_to_next_pct !== undefined && row.fib_forecast.distance_to_next_pct !== null) ? Number(row.fib_forecast.distance_to_next_pct) : null;
            const distStr = distPct !== null ? ` (+${distPct.toFixed(1)}%)` : '';
            const label = row.fib_forecast.next_resistance_label || 'Target';
            
            let tagClass = 't1';
            let badgeText = '🎯 T1';
            if (label.includes('Peak') || label === 'Daily Peak') {
                tagClass = 'peak';
                badgeText = '🎯 Peak';
            } else if (label.includes('T2') || label.includes('1.618')) {
                tagClass = 't2';
                badgeText = '🎯 T2';
            } else if (label.includes('T3') || label.includes('2.618')) {
                tagClass = 't3';
                badgeText = '🎯 T3';
            } else if (label.includes('T1') || label.includes('1.000')) {
                tagClass = 't1';
                badgeText = '🎯 T1';
            } else if (label.includes('0.618')) {
                tagClass = 'conservative';
                badgeText = '🎯 Fib';
            } else if (label.includes('0.236') || label.includes('0.382') || label.includes('0.500')) {
                tagClass = 'conservative';
                badgeText = '🎯 Trim';
            }

            const pA = row.fib_forecast.pA ? `$${Number(row.fib_forecast.pA).toFixed(2)}` : '—';
            const pB = row.fib_forecast.pB ? `$${Number(row.fib_forecast.pB).toFixed(2)}` : '—';
            const pC = row.fib_forecast.pC ? `$${Number(row.fib_forecast.pC).toFixed(2)}` : '—';
            const t1 = row.fib_forecast.t1_100 ? `$${Number(row.fib_forecast.t1_100).toFixed(2)}` : '—';
            const t1L = row.fib_forecast.t1_limit ? `$${Number(row.fib_forecast.t1_limit).toFixed(2)}` : '—';
            const t2 = row.fib_forecast.t2_1618 ? `$${Number(row.fib_forecast.t2_1618).toFixed(2)}` : '—';
            const t2L = row.fib_forecast.t2_limit ? `$${Number(row.fib_forecast.t2_limit).toFixed(2)}` : '—';
            const t3 = row.fib_forecast.t3_2618 ? `$${Number(row.fib_forecast.t3_2618).toFixed(2)}` : '—';
            const t3L = row.fib_forecast.t3_limit ? `$${Number(row.fib_forecast.t3_limit).toFixed(2)}` : '—';
            const peakTarget = row.fib_forecast.daily_peak_target ? `$${Number(row.fib_forecast.daily_peak_target).toFixed(2)}` : t2;
            const peakStatus = row.fib_forecast.daily_peak_status || 'EXPANDING';
            const atrConf = row.fib_forecast.atr_confluence ? ' [ATR Confluence]' : '';
            const atrConfBadge = row.fib_forecast.atr_confluence ? `<span class="atr-confluence-badge" title="ATR Volatility Ceiling Confluence">ATR</span>` : '';

            const fibTooltip = `Daily Peak: ${peakTarget} (${peakStatus})${atrConf} | Limits (-0.25%): T1=${t1L}, T2=${t2L}, T3=${t3L} | Anchors: A=${pA}, B=${pB}, C=${pC} | Targets: T1=${t1}, T2=${t2}, T3=${t3}`;
            
            const peakSubtext = row.fib_forecast.daily_peak_target
                ? `<div class="fib-peak-indicator" title="Projected Daily Trading Peak Target: ${peakTarget} (${peakStatus})${atrConf}">Daily Peak: <strong>${peakTarget}</strong> ${atrConfBadge}</div>`
                : '';

            fibTargetHtml = `
                <div class="fib-target-cell-wrapper">
                    <span class="fib-target-tag ${tagClass}" title="${fibTooltip}">${badgeText} $${nextPrice}${distStr}</span>
                    ${peakSubtext}
                </div>
            `;
        }

        // Only portfolio (held) and watchlist tickers get clickable chart rows
        const isChartable = heldTickers.has(sym) || userWatchlist.has(sym);
        const chartRowAttr = isChartable ? `data-symbol="${sym}" class="chart-clickable"` : '';

        return `
            <tr ${chartRowAttr}>
                <td class="ticker-cell ${row._isScout ? 'is-scout' : ''}">
                    <span class="ticker-symbol ${dayColor}">${sym}</span>
                    ${scoutIndicator}
                </td>
                <td class="${pClass}">${p}</td>
                <td class="${dayColor}">${row.session_change_pct > 0 ? '+' : ''}${row.session_change_pct.toFixed(2)}%</td>
                <td class="${gapColor}">${row.gap_percent > 0 ? '+' : ''}${row.gap_percent.toFixed(2)}%</td>
                <td>${formatVol(row.volume)}</td>
                <td class="${rvolColor}">${rvolStr}</td>
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
            <td colspan="14">${label}</td>
        </tr>
    `;

    // Calculate Total Portfolio Value for Header
    const rate = currentEurUsdRate || 1.08;
    let stockValUsd = 0;
    portfolio.forEach(pItem => {
        const symUpper = pItem.ticker ? pItem.ticker.toUpperCase() : '';
        const liveTicker = tickers.find(t => t.ticker.toUpperCase() === symUpper);
        const price = liveTicker ? (liveTicker.price || 0) : (parseFloat(pItem.price) || parseFloat(pItem.wac) || 0);
        const shares = parseFloat(pItem.shares) || 0;
        stockValUsd += shares * price;
    });

    const cashEur = ms.unallocated_cash_eur !== undefined ? parseFloat(ms.unallocated_cash_eur) : (ssot.unallocated_cash_eur !== undefined ? parseFloat(ssot.unallocated_cash_eur) : 0);
    const cashUsd = ms.unallocated_cash_usd !== undefined ? parseFloat(ms.unallocated_cash_usd) : (cashEur * rate);

    const totalValUsd = stockValUsd + cashUsd;
    const totalValEur = totalValUsd / rate;

    const dHeaderPortfolioVal = document.getElementById('header-portfolio-val');
    if (dHeaderPortfolioVal) {
        dHeaderPortfolioVal.innerHTML = `€${Math.round(totalValEur).toLocaleString('en-US')} <span style="font-size: 0.78rem; color: #ffffff; font-weight: normal; margin-left: 4px;">$${Math.round(totalValUsd).toLocaleString('en-US')}</span>`;
    }

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

    // Attach click handlers to chart-clickable rows
    dTableBody.querySelectorAll('tr.chart-clickable').forEach(tr => {
        tr.addEventListener('click', (e) => {
            // Don't open chart if user clicked a button/input inside the row
            if (e.target.closest('button, input, select, a')) return;
            const sym = tr.dataset.symbol;
            if (sym) openChartModal(sym);
        });
    });
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

        // Calculate GEX deltas for the current poll cycle
        if (state && state.tickers) {
            const newGexCache = {};
            state.tickers.forEach(t => {
                latestTickersData[t.ticker] = t;
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
            
            // Render Macro HUD dynamic cards
            if (state.tickers) {
                let hudHtml = '';
                
                currentMacroTickers.forEach(tickerStr => {
                    const row = state.tickers.find(t => t.ticker === tickerStr);
                    const label = MACRO_LABELS[tickerStr] || tickerStr;
                    const title = label === tickerStr ? tickerStr : `${label} (${tickerStr})`;
                    
                    if (row) {
                        latestMacroData[tickerStr] = row;
                        let changeVal = row.session_change_pct || 0;
                        let labelSuffix = '';

                        
                        const changeStr = (changeVal > 0 ? '+' : '') + changeVal.toFixed(2) + '%' + labelSuffix;
                        const changeColor = changeVal > 0 ? 'text-green' : 'text-red';
                        
                        let gapExtra = '';
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
                            gapExtra = ` · <span class="${gexColor}" style="font-weight: 600;">GEX: ${gexVal}${chevron}</span>`;
                        }

                        hudHtml += `
                            <div class="macro-card glass-panel" id="macro-card-${tickerStr.replace(/[^a-zA-Z0-9]/g, '')}">
                                <h3>${title}</h3>
                                <div class="macro-val">${(row.price || 0).toFixed(2)}</div>
                                <div class="macro-gap ${changeColor}">${changeStr}${gapExtra}</div>
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

        // Auto-capture chart screenshots if any chart is open
        captureAllChartScreenshots();

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
async function fetchPortfolio() {
    if (document.activeElement && document.activeElement.closest('.manager-table')) return;
    try {
        const res = await fetch(`${API_BASE}/basket`);
        const data = await res.json();
        renderPortfolio(data);
    } catch (e) { console.error("Portfolio fetch failed", e); }
}

function renderPortfolio(data) {
    if (!dPortfolioBody) return;
    let html = '';
    const portfolio = data.portfolio || [];
    const cash = data.unallocated_cash_eur || 0;
    const rate = data.eurusd_rate || 1.08;
    currentEurUsdRate = rate; // Update global rate
    const usd = (cash * rate).toFixed(2);
    
    let totalStockUsd = 0;

    portfolio.forEach((item, index) => {
        const shares = parseFloat(item.shares) || 0;
        const price = parseFloat(item.price) || (prevPrices[item.ticker.toUpperCase()] ? parseFloat(prevPrices[item.ticker.toUpperCase()]) : 0) || parseFloat(item.wac) || 0;
        totalStockUsd += shares * price;

        html += `
            <tr data-index="${index}" class="portfolio-item-row">
                <td style="color: var(--accent); font-weight: 700; font-size: 0.8rem;">${item.ticker}</td>
                <td><input type="number" class="portfolio-input" data-key="shares" value="${item.shares || 0}"></td>
                <td><input type="number" step="0.01" class="portfolio-input" data-key="wac" value="${item.wac || 0}"></td>
                <td><button class="delete-btn" onclick="deleteFromPortfolio(${index})">&times;</button></td>
            </tr>
        `;
    });

    const cashUsd = cash * rate;
    const totalUsd = totalStockUsd + cashUsd;
    const totalEur = totalUsd / rate;

    html += `
        <tr class="cash-row" style="background: rgba(0, 255, 148, 0.05);">
            <td style="color: var(--green); font-weight: 700; font-size: 0.75rem;">CASH (€)</td>
            <td><input type="number" step="0.01" class="portfolio-input" id="cash-input-eur" value="${cash}" style="color: #ffffff; font-weight: 700;"></td>
            <td colspan="2" style="font-size: 0.75rem; color: #ffffff; font-weight: 700; text-align: left; padding-left: 8px;">$${usd}</td>
        </tr>
        <tr class="total-portfolio-row">
            <td style="color: var(--accent); font-weight: 700; font-size: 0.75rem;">TOTAL</td>
            <td id="portfolio-total-eur" style="font-weight: 700; font-size: 0.75rem; color: #ffffff; padding-left: 4px;">
                €${Math.round(totalEur).toLocaleString('en-US')}
            </td>
            <td colspan="2" id="portfolio-total-usd" style="font-size: 0.75rem; color: #ffffff; font-weight: 700; text-align: left; padding-left: 8px;">
                $${Math.round(totalUsd).toLocaleString('en-US')}
            </td>
        </tr>
    `;

    dPortfolioBody.innerHTML = html;
}

function updatePortfolioTotalInSidebar() {
    const eurEl = document.getElementById('portfolio-total-eur');
    const usdEl = document.getElementById('portfolio-total-usd');
    if (!eurEl || !usdEl) return;

    const rows = dPortfolioBody.querySelectorAll('tr.portfolio-item-row');
    let totalStockUsd = 0;
    rows.forEach(row => {
        const ticker = row.cells[0].textContent.trim().toUpperCase();
        const shares = parseFloat(row.querySelector('[data-key="shares"]').value) || 0;
        const wac = parseFloat(row.querySelector('[data-key="wac"]').value) || 0;
        const price = (prevPrices[ticker] ? parseFloat(prevPrices[ticker]) : 0) || wac;
        totalStockUsd += shares * price;
    });

    const cashInput = document.getElementById('cash-input-eur');
    const cashEur = cashInput ? (parseFloat(cashInput.value) || 0) : 0;
    const cashUsd = cashEur * (currentEurUsdRate || 1.08);

    const totalUsd = totalStockUsd + cashUsd;
    const totalEur = totalUsd / (currentEurUsdRate || 1.08);

    eurEl.textContent = `€${Math.round(totalEur).toLocaleString('en-US')}`;
    usdEl.textContent = `$${Math.round(totalUsd).toLocaleString('en-US')}`;

    const dHeaderPortfolioVal = document.getElementById('header-portfolio-val');
    if (dHeaderPortfolioVal) {
        dHeaderPortfolioVal.innerHTML = `€${Math.round(totalEur).toLocaleString('en-US')} <span style="font-size: 0.78rem; color: #ffffff; font-weight: normal; margin-left: 4px;">$${Math.round(totalUsd).toLocaleString('en-US')}</span>`;
    }
}

async function addToPortfolio() {
    const ticker = dAddPortfolioTicker.value.trim().toUpperCase();
    if (!ticker) return;
    const portfolio = getCurrentPortfolio();
    if (portfolio.find(i => i.ticker === ticker)) return;
    portfolio.push({ ticker, shares: 1, wac: 0 });
    dAddPortfolioTicker.value = '';
    await savePortfolio(portfolio);
}

function getCurrentPortfolio() {
    const rows = dPortfolioBody.querySelectorAll('tr.portfolio-item-row');
    const portfolio = [];
    rows.forEach(row => {
        portfolio.push({
            ticker: row.cells[0].textContent,
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
async function savePortfolio(portfolioArr) {
    const btn = dSavePortfolioBtn;
    const originalText = btn.innerHTML;
    btn.innerHTML = "WAIT...";
    btn.disabled = true;
    try {
        const pArray = portfolioArr || getCurrentPortfolio();
        const cVal = getCurrentCash();
        const payload = {
            portfolio: pArray,
            unallocated_cash_eur: cVal,
            unallocated_cash_usd: parseFloat((cVal * currentEurUsdRate).toFixed(2))
        };
        const res = await fetch(`${API_BASE}/basket`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            btn.innerHTML = "SYNC ✅";
            showFeedback(btn, "✅ Synced!", "Portfolio successfully updated! (Table refreshing...)", false, dPortfolioStatus);
            await fetchPortfolio();
            pollData(); // Force immediate refresh
        } else {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.message || `Server responded with HTTP ${res.status}`);
        }
    } catch (e) {
        console.error("Portfolio update failed", e);
        showFeedback(btn, "❌ Error", "Failed to update portfolio.", true, dPortfolioStatus);
    }
    finally {
        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }, 1500);
    }
}

async function deleteFromPortfolio(index) {
    const portfolio = getCurrentPortfolio();
    portfolio.splice(index, 1);
    await savePortfolio(portfolio);
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
            if (dWatchlistStatus) {
                dWatchlistStatus.textContent = "Watchlist updated! (Table refreshing...)";
                dWatchlistStatus.className = "status-message inline-feedback active text-green";
                setTimeout(() => {
                    dWatchlistStatus.textContent = "";
                    dWatchlistStatus.className = "status-message inline-feedback";
                }, 3000);
            }
            pollData(); // Force immediate refresh
        }
    } catch (e) {
        console.error("Watchlist update failed", e);
        if (dWatchlistStatus) {
            dWatchlistStatus.textContent = "Failed to update watchlist.";
            dWatchlistStatus.className = "status-message inline-feedback active text-red";
            setTimeout(() => {
                dWatchlistStatus.textContent = "";
                dWatchlistStatus.className = "status-message inline-feedback";
            }, 3000);
        }
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

dAddToPortfolioBtn.addEventListener('click', addToPortfolio);
dSavePortfolioBtn.addEventListener('click', () => savePortfolio());
dAddToWatchlistBtn.addEventListener('click', addToWatchlist);

if (dAddPortfolioTicker) {
    dAddPortfolioTicker.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            addToPortfolio();
        }
    });
}

if (dAddWatchlistTicker) {
    dAddWatchlistTicker.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            addToWatchlist();
        }
    });
}

if (dRunAiScoutBtn) {
    dRunAiScoutBtn.addEventListener('click', async () => {
        dRunAiScoutBtn.disabled = true;
        const originalHtml = dRunAiScoutBtn.innerHTML;
        dRunAiScoutBtn.innerHTML = "🔭 Scouting Breakouts...";
        dAiScoutStatus.textContent = "Querying Gemini API (Grounding Search)...";
        dAiScoutStatus.className = "status-message inline-feedback active text-yellow";
        
        try {
            const limit = dAiScoutLimitSelect ? dAiScoutLimitSelect.value : 2;
            const maxRsi = dAiScoutMaxRsiSelect ? dAiScoutMaxRsiSelect.value : 75;
            const res = await fetch(`${API_BASE}/ai_scout?limit=${limit}&max_rsi=${maxRsi}`, {
                method: 'POST'
            });
            const data = await res.json();
            if (data.status === 'success') {
                if (data.scouts && data.scouts.length > 0) {
                    showFeedback(dRunAiScoutBtn, "🔭 Scout Complete! ✅", `Found stocks: ${data.scouts.join(', ')}`, false, dAiScoutStatus);
                } else {
                    showFeedback(dRunAiScoutBtn, "🔭 Scout Complete! ✅", "No new breakout stocks found matching regime.", false, dAiScoutStatus);
                }
                pollData(); // Force immediate refresh to pull newly scanned scouts
            } else {
                throw new Error(data.message);
            }
        } catch (e) {
            console.error("AI Scout Error: ", e);
            showFeedback(dRunAiScoutBtn, "❌ Scouting Failed", e.message || "Failed to query AI Scout.", true, dAiScoutStatus);
        } finally {
            dRunAiScoutBtn.disabled = false;
            dRunAiScoutBtn.innerHTML = originalHtml;
        }
    });
}

// Real-time conversion feedback as user types cash or portfolio values
dPortfolioBody.addEventListener('input', (e) => {
    if (e.target.id === 'cash-input-eur') {
        const val = parseFloat(e.target.value) || 0;
        const usdVal = (val * currentEurUsdRate).toFixed(2);
        const usdDisplay = e.target.closest('tr').querySelector('td[colspan="2"]');
        if (usdDisplay) {
            usdDisplay.textContent = `$${usdVal}`;
        }
    }
    updatePortfolioTotalInSidebar();
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

// Google Drive Config & Sync Handlers
const dGDriveDisplayContainer = document.getElementById('gdrive-display-container');
const dGDriveFolderDisplay = document.getElementById('gdrive-folder-display');
const dUnlockGDriveBtn = document.getElementById('unlock-gdrive-btn');

const dGDriveEditContainer = document.getElementById('gdrive-edit-container');
const dGDriveSelect = document.getElementById('gdrive-folder-select');
const dGDriveInput = document.getElementById('gdrive-folder-input');
const dSaveGDriveBtn = document.getElementById('save-gdrive-btn');
const dCancelGDriveBtn = document.getElementById('cancel-gdrive-btn');
const dGDriveConfigStatus = document.getElementById('gdrive-config-status');

// Setup Modal Elements
const dGDriveSetupModalOverlay = document.getElementById('gdrive-setup-modal-overlay');
const dSetupFolderLoadingMsg = document.getElementById('setup-folder-loading-msg');
const dSetupFolderContainer = document.getElementById('setup-folder-container');
const dSetupFolderSelect = document.getElementById('setup-folder-select');
const dSetupFolderInput = document.getElementById('setup-folder-input');
const dSetupLinkBtn = document.getElementById('setup-link-btn');
const dSetupLinkBadge = document.getElementById('setup-link-badge');
const dSetupWizardStatus = document.getElementById('setup-wizard-status');
const dSetupSubmitBtn = document.getElementById('setup-submit-btn');

// Setup Upload Elements
const dSetupCredsUploadContainer = document.getElementById('setup-creds-upload-container');
const dSetupCredsFileInput = document.getElementById('setup-creds-file-input');
const dSetupUploadBtn = document.getElementById('setup-upload-btn');
const dSetupLinkContainer = document.getElementById('setup-link-container');

let gdriveStatusPollInterval = null;
let currentGDriveFolder = 'GeminiTradingSSoT';
let hasLoadedFoldersInModal = false;

// Dynamic Google Drive Folder Fetcher and Selector Binder
async function populateGDriveFolderSelect(selectEl, customInputEl, preselectedValue = "") {
    try {
        selectEl.innerHTML = '<option value="" disabled selected>Loading folders from Drive...</option>';
        const res = await fetch(`${API_BASE}/gdrive_folders`);
        const data = await res.json();
        
        selectEl.innerHTML = '';
        
        const folders = data.folders || [];
        if (folders.length > 0) {
            folders.forEach(f => {
                const opt = document.createElement('option');
                opt.value = f.name;
                opt.textContent = f.name;
                selectEl.appendChild(opt);
            });
        }
        
        // Add option to create a new folder
        const newOpt = document.createElement('option');
        newOpt.value = '__NEW_FOLDER__';
        newOpt.textContent = '➕ [Create New Folder...]';
        selectEl.appendChild(newOpt);
        
        // Bind dynamic visibility trigger for custom folder input
        selectEl.onchange = () => {
            if (selectEl.value === '__NEW_FOLDER__') {
                customInputEl.style.display = 'block';
                customInputEl.value = '';
                customInputEl.focus();
            } else {
                customInputEl.style.display = 'none';
                customInputEl.value = selectEl.value;
            }
            
            // Enable button if setup modal submit
            if (selectEl.id === 'setup-folder-select' && dSetupSubmitBtn) {
                dSetupSubmitBtn.disabled = !customInputEl.value.trim();
            }
        };
        
        // Handle preselection
        if (preselectedValue && preselectedValue.trim()) {
            const exists = Array.from(selectEl.options).some(o => o.value === preselectedValue);
            if (exists) {
                selectEl.value = preselectedValue;
                customInputEl.style.display = 'none';
                customInputEl.value = preselectedValue;
            } else {
                selectEl.value = '__NEW_FOLDER__';
                customInputEl.style.display = 'block';
                customInputEl.value = preselectedValue;
            }
        } else {
            if (folders.length > 0) {
                selectEl.value = folders[0].name;
                customInputEl.value = folders[0].name;
                customInputEl.style.display = 'none';
            } else {
                selectEl.value = '__NEW_FOLDER__';
                customInputEl.style.display = 'block';
                customInputEl.value = 'GeminiTradingSSoT';
            }
        }
        
        selectEl.dispatchEvent(new Event('change'));
        
    } catch (e) {
        console.error("Failed to fetch folders", e);
        selectEl.innerHTML = '<option value="__NEW_FOLDER__">[No existing folders - Create New]</option>';
        selectEl.value = '__NEW_FOLDER__';
        customInputEl.style.display = 'block';
        customInputEl.value = preselectedValue || 'GeminiTradingSSoT';
    }
}

// Fetch Google Drive folder name and render UI
async function fetchGDriveConfig() {
    try {
        const res = await fetch(`${API_BASE}/gdrive_status`);
        const status = await res.json();
        
        currentGDriveFolder = status.folder_name || 'GeminiTradingSSoT';
        if (dGDriveFolderDisplay) {
            dGDriveFolderDisplay.textContent = currentGDriveFolder;
        }
        if (dGDriveInput) {
            dGDriveInput.value = currentGDriveFolder;
        }
        
        // Trigger Setup Modal if first use / missing folder or oauth token
        if (status.needs_setup) {
            openGDriveSetupModal(status);
        }
    } catch (e) {
        console.error("Failed to fetch Google Drive status", e);
    }
}

// Open Google Drive onboarding modal and start polling
function openGDriveSetupModal(status) {
    if (!dGDriveSetupModalOverlay) return;
    openModal(dGDriveSetupModalOverlay);
    
    hasLoadedFoldersInModal = false;
    if (dSetupFolderInput) {
        dSetupFolderInput.value = status.folder_name || 'GeminiTradingSSoT';
    }
    
    updateSetupModalUI(status);
    
    // Start polling status to detect when user authorizes via browser
    if (gdriveStatusPollInterval) clearInterval(gdriveStatusPollInterval);
    gdriveStatusPollInterval = setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/gdrive_status`);
            const currentStatus = await res.json();
            updateSetupModalUI(currentStatus);
            
            // If setup is now completely satisfied, highlight it
            if (currentStatus.token_linked && dSetupFolderInput.value.trim()) {
                dSetupSubmitBtn.disabled = false;
            }
        } catch (e) {
            console.error("Setup polling failed", e);
        }
    }, 2000);
}

// Update modal elements based on current auth/credentials status
function updateSetupModalUI(status) {
    if (!dSetupLinkBadge) return;
    
    if (status.token_linked) {
        dSetupLinkBadge.textContent = 'LINKED ✅';
        dSetupLinkBadge.className = 'badge-status text-green';
        dSetupLinkBadge.style.fontWeight = 'bold';
        dSetupLinkBtn.disabled = true;
        dSetupLinkBtn.textContent = '🔒 Account Linked';
        
        if (dSetupFolderLoadingMsg) dSetupFolderLoadingMsg.style.display = 'none';
        if (dSetupFolderContainer) dSetupFolderContainer.style.display = 'flex';
        
        if (!hasLoadedFoldersInModal && dSetupFolderSelect && dSetupFolderInput) {
            hasLoadedFoldersInModal = true;
            populateGDriveFolderSelect(dSetupFolderSelect, dSetupFolderInput, status.folder_name || currentGDriveFolder);
        }
    } else {
        dSetupLinkBadge.textContent = 'NOT LINKED ⚠️';
        dSetupLinkBadge.className = 'badge-status text-muted';
        dSetupLinkBtn.disabled = false;
        dSetupLinkBtn.textContent = '🔗 Link Google Account';
        
        if (dSetupFolderLoadingMsg) dSetupFolderLoadingMsg.style.display = 'block';
        if (dSetupFolderContainer) dSetupFolderContainer.style.display = 'none';
        hasLoadedFoldersInModal = false;
    }
    
    // Credentials status warning and upload container visibility
    if (!status.credentials_uploaded) {
        if (dSetupCredsUploadContainer) dSetupCredsUploadContainer.style.display = 'block';
        if (dSetupLinkContainer) dSetupLinkContainer.style.display = 'none';
        
        dSetupWizardStatus.innerHTML = '<span style="color: var(--red); font-weight: bold;">⚠️ credentials.json is unpopulated or missing. Upload your Google Cloud OAuth credentials file to continue.</span>';
        dSetupLinkBtn.disabled = true;
    } else {
        if (dSetupCredsUploadContainer) dSetupCredsUploadContainer.style.display = 'none';
        if (dSetupLinkContainer) dSetupLinkContainer.style.display = 'block';
        
        if (!status.token_linked) {
            dSetupWizardStatus.textContent = 'credentials.json detected! Click Link Google Account to authorize Drive.';
            dSetupWizardStatus.className = 'status-message text-yellow';
        } else {
            dSetupWizardStatus.textContent = 'Ready to complete setup!';
            dSetupWizardStatus.className = 'status-message text-green';
        }
    }
}

// Handle Credentials file selection and upload
if (dSetupUploadBtn && dSetupCredsFileInput) {
    dSetupUploadBtn.addEventListener('click', () => dSetupCredsFileInput.click());
    
    dSetupCredsFileInput.addEventListener('change', async () => {
        const file = dSetupCredsFileInput.files[0];
        if (!file) return;
        
        if (file.name !== "credentials.json") {
            dSetupWizardStatus.textContent = "Error: File must be named precisely 'credentials.json'";
            dSetupWizardStatus.className = "status-message text-red";
            return;
        }
        
        dSetupUploadBtn.disabled = true;
        dSetupUploadBtn.textContent = "Uploading...";
        dSetupWizardStatus.textContent = "Uploading credentials.json...";
        dSetupWizardStatus.className = "status-message text-yellow";
        
        try {
            const formData = new FormData();
            formData.append("file", file);
            
            const res = await fetch(`${API_BASE}/upload_credentials`, {
                method: "POST",
                body: formData
            });
            const data = await res.json();
            if (data.status === "success") {
                dSetupUploadBtn.textContent = "📂 Choose credentials.json";
                dSetupUploadBtn.disabled = false;
                showFeedback(dSetupUploadBtn, "✅ Uploaded!", "Credentials uploaded!", false, dSetupWizardStatus);
                // Immediately check status again to refresh UI
                const statusRes = await fetch(`${API_BASE}/gdrive_status`);
                const status = await statusRes.json();
                updateSetupModalUI(status);
            } else {
                throw new Error(data.message);
            }
        } catch (e) {
            dSetupWizardStatus.textContent = `Upload failed: ${e.message}`;
            dSetupWizardStatus.className = "status-message text-red";
            dSetupUploadBtn.textContent = "📂 Choose credentials.json";
            dSetupUploadBtn.disabled = false;
        }
    });
}

// Setup Modal Trigger Event Link
if (dSetupLinkBtn) {
    dSetupLinkBtn.addEventListener('click', async () => {
        dSetupLinkBtn.disabled = true;
        dSetupLinkBtn.textContent = 'Launching Auth Flow...';
        try {
            const res = await fetch(`${API_BASE}/gdrive_link`, { method: 'POST' });
            const data = await res.json();
            if (data.status === 'success') {
                dSetupWizardStatus.textContent = 'Auth flow launched! Check browser to log in and authorize Google Drive.';
                dSetupWizardStatus.className = 'status-message text-yellow';
            } else {
                throw new Error(data.message);
            }
        } catch (e) {
            dSetupWizardStatus.textContent = `Link Failed: ${e.message}`;
            dSetupWizardStatus.className = 'status-message text-red';
            dSetupLinkBtn.disabled = false;
            dSetupLinkBtn.textContent = '🔗 Link Google Account';
        }
    });
}

// Complete Onboarding Setup Submission
if (dSetupSubmitBtn) {
    dSetupSubmitBtn.addEventListener('click', async () => {
        const folderName = (dSetupFolderSelect.value === '__NEW_FOLDER__' ? dSetupFolderInput.value : dSetupFolderSelect.value).trim();
        if (!folderName) {
            dSetupWizardStatus.textContent = 'Please select or enter a valid folder name.';
            dSetupWizardStatus.className = 'status-message text-red';
            return;
        }
        
        dSetupSubmitBtn.disabled = true;
        dSetupSubmitBtn.textContent = 'Finalizing...';
        try {
            const res = await fetch(`${API_BASE}/gdrive_config`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ folder_name: folderName })
            });
            const data = await res.json();
            if (data.status === 'success') {
                if (gdriveStatusPollInterval) clearInterval(gdriveStatusPollInterval);
                closeModal(dGDriveSetupModalOverlay);
                
                // Refresh dashboard admin values
                currentGDriveFolder = folderName;
                dGDriveFolderDisplay.textContent = folderName;
                dGDriveInput.value = folderName;
                
                // Show a successful toast feedback on the admin status block
                showFeedback(dUnlockGDriveBtn, "🔒", "Google Drive SSoT Setup Completed Successfully!", false, dGDriveConfigStatus);
            } else {
                throw new Error(data.message);
            }
        } catch (e) {
            dSetupWizardStatus.textContent = `Onboarding Failed: ${e.message}`;
            dSetupWizardStatus.className = 'status-message text-red';
            dSetupSubmitBtn.disabled = false;
            dSetupSubmitBtn.textContent = 'Complete Setup';
        }
    });
}

// Sidebar Admin Panel Locks / Unlocks Toggles
if (dUnlockGDriveBtn) {
    dUnlockGDriveBtn.addEventListener('click', () => {
        // Toggle input edit visibility
        dGDriveDisplayContainer.style.display = 'none';
        dGDriveEditContainer.style.display = 'flex';
        
        // Dynamically fetch and list folders in sidebar selector
        if (dGDriveSelect && dGDriveInput) {
            populateGDriveFolderSelect(dGDriveSelect, dGDriveInput, currentGDriveFolder);
        }
    });
}

if (dCancelGDriveBtn) {
    dCancelGDriveBtn.addEventListener('click', () => {
        // Revert editing mode
        dGDriveDisplayContainer.style.display = 'flex';
        dGDriveEditContainer.style.display = 'none';
        dGDriveConfigStatus.textContent = '';
    });
}

if (dSaveGDriveBtn) {
    dSaveGDriveBtn.addEventListener('click', async () => {
        const val = (dGDriveSelect.value === '__NEW_FOLDER__' ? dGDriveInput.value : dGDriveSelect.value).trim();
        if (!val) {
            showFeedback(dSaveGDriveBtn, "⚠️ Empty", "Folder name cannot be empty.", true, dGDriveConfigStatus);
            return;
        }
        dSaveGDriveBtn.disabled = true;
        try {
            const res = await fetch(`${API_BASE}/gdrive_config`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ folder_name: val })
            });
            const data = await res.json();
            if (data.status === 'success') {
                currentGDriveFolder = val;
                dGDriveFolderDisplay.textContent = val;
                
                // Close edit mode
                dGDriveDisplayContainer.style.display = 'flex';
                dGDriveEditContainer.style.display = 'none';
                
                showFeedback(dUnlockGDriveBtn, "🔒", "Folder updated & syncing in background!", false, dGDriveConfigStatus);
            } else {
                throw new Error(data.message);
            }
        } catch (e) {
            showFeedback(dSaveGDriveBtn, "❌ Error", e.message || "Failed to save.", true, dGDriveConfigStatus);
        } finally {
            dSaveGDriveBtn.disabled = false;
        }
    });
}



// ─── Mobile Quick Action Bridge ───
const dMobileCopyBtn = document.getElementById('mobile-copy-json-btn');
const dMobilePasteBtn = document.getElementById('mobile-paste-payload-btn');
const dMobileCopySessionBtn = document.getElementById('mobile-copy-session-btn');
const dMobileReviewBtn = document.getElementById('mobile-btn-session-review');

if (dMobileCopyBtn) dMobileCopyBtn.addEventListener('click', () => copyMarketSnapshot(dMobileCopyBtn, dMobileStatus));
if (dMobilePasteBtn) dMobilePasteBtn.addEventListener('click', () => ingestExecutionPayload(dMobilePasteBtn, dMobileStatus));
if (dMobileCopySessionBtn) dMobileCopySessionBtn.addEventListener('click', () => copySessionBoot(dMobileCopySessionBtn, dMobileStatus));
if (dMobileReviewBtn) dMobileReviewBtn.addEventListener('click', () => copySessionReviewPayload(dMobileReviewBtn, dMobileStatus));


// ─── Chart Modal: Lightweight Charts Integration ───

// Registry of open chart instances (symbol -> { chart, candleSeries })
const chartInstances = {};

// Registry of latest captured screenshots (symbol -> base64 PNG string, no prefix)
const chartScreenshots = {};

// DOM refs for the chart modal
const dChartModalOverlay = document.getElementById('chart-modal-overlay');
const dChartModalClose   = document.getElementById('chart-modal-close');
const dChartModalTicker  = document.getElementById('chart-modal-ticker');
const dChartModalMeta    = document.getElementById('chart-modal-meta');
const dChartContainer    = document.getElementById('chart-container');

// ─── Trend-Based Fibonacci Extension State & DOM ───
let currentFibMode = 'auto'; // 'auto' | 'manual' | 'none'
let manualPickStep = 0;      // 0: idle, 1: wait A (Low), 2: wait B (High), 3: wait C (Retrace)
let manualFibPoints = { pA: null, pB: null, pC: null };
let activeFibPriceLines = []; // store IPriceLine references
let activeFibBars = [];       // store current symbol's bars
let activeFibCandleSeries = null;
let activeFibChart = null;

const dChartFibAutoBtn     = document.getElementById('chart-fib-auto-btn');
const dChartFibPickBtn     = document.getElementById('chart-fib-pick-btn');
const dChartFibClearBtn    = document.getElementById('chart-fib-clear-btn');
const dChartFibStatusBar   = document.getElementById('chart-fib-status-bar');
const dChartFibStatusText  = document.getElementById('chart-fib-status-text');
const dChartFibAnchorsInfo = document.getElementById('chart-fib-anchors-info');
const dChartFibHud         = document.getElementById('chart-fib-hud');
const dChartFibLevelsGrid  = document.getElementById('chart-fib-levels-grid');

const FIB_RATIOS = [
    { ratio: 0.618, name: '0.618', label: 'Conservative Trim', role: 'Initial Resistance (Pre-emptive Trim)', color: '#ffb74d', isTarget: false, tranche: null },
    { ratio: 0.786, name: '0.786', label: '78.6% Extension',  role: 'Intermediate Resistance', color: '#4dd0e1', isTarget: false, tranche: null },
    { ratio: 1.000, name: '1.000', label: 'Target 1 (100%)',   role: 'T1: Measured Move (Trim 20-25%)', color: '#4caf50', isTarget: true, tranche: 'Tranche 1: 25% Trim' },
    { ratio: 1.272, name: '1.272', label: '127.2% Expansion', role: 'Expansion Resistance / Early Peak', color: '#29b6f6', isTarget: false, tranche: null },
    { ratio: 1.618, name: '1.618', label: 'Target 2 (Golden)', role: 'T2: Primary Institutional Daily Peak (Trim 50%)', color: '#e040fb', isTarget: true, tranche: 'Tranche 2: 50% Cumulative' },
    { ratio: 2.000, name: '2.000', label: '200% Extension',   role: 'Momentum Expansion Target', color: '#ff4081', isTarget: false, tranche: null },
    { ratio: 2.618, name: '2.618', label: 'Target 3 (2.618)',  role: 'T3: Runner Liquidation (Trim Remainder)', color: '#ff5252', isTarget: true, tranche: 'Tranche 3: Runner Liquidation' }
];

/**
 * Automatically detect intraday swing points:
 * Point 1 (pA): Swing Low
 * Point 2 (pB): Swing High
 * Point 3 (pC): Retracement Low
 */
function detectIntradaySwings(bars) {
    if (!bars || bars.length < 5) return null;

    let sessionMinIdx = 0;
    let sessionMaxIdx = 0;
    for (let i = 0; i < bars.length; i++) {
        if (bars[i].low < bars[sessionMinIdx].low) sessionMinIdx = i;
        if (bars[i].high > bars[sessionMaxIdx].high) sessionMaxIdx = i;
    }

    let pA = null, pB = null, pC = null;

    if (sessionMinIdx < sessionMaxIdx) {
        // Classic impulse leg: Low before High
        pA = { time: bars[sessionMinIdx].time, price: bars[sessionMinIdx].low, label: 'Swing Low (A)' };
        pB = { time: bars[sessionMaxIdx].time, price: bars[sessionMaxIdx].high, label: 'Swing High (B)' };

        if (sessionMaxIdx < bars.length - 1) {
            let retraceIdx = sessionMaxIdx + 1;
            for (let i = sessionMaxIdx + 1; i < bars.length; i++) {
                if (bars[i].low < bars[retraceIdx].low) retraceIdx = i;
            }
            pC = { time: bars[retraceIdx].time, price: bars[retraceIdx].low, label: 'Retracement Low (C)' };
        } else {
            // Session high is the latest bar (active breakout / peak thrust)
            // Look back over the preceding 3 to 10 bars to find the consolidation launch low
            let lookback = Math.min(10, sessionMaxIdx - sessionMinIdx);
            let dipIdx = Math.max(0, sessionMaxIdx - 1);
            if (lookback > 1) {
                for (let i = sessionMaxIdx - 1; i >= sessionMaxIdx - lookback; i--) {
                    if (bars[i].low < bars[dipIdx].low) dipIdx = i;
                }
            }
            pC = { time: bars[dipIdx].time, price: bars[dipIdx].low, label: 'Breakout Base (C)' };
        }
    } else {
        // Session high occurred before session low: bounce off session low
        pA = { time: bars[sessionMinIdx].time, price: bars[sessionMinIdx].low, label: 'Swing Low (A)' };
        if (sessionMinIdx < bars.length - 1) {
            let bounceIdx = sessionMinIdx + 1;
            for (let i = sessionMinIdx + 1; i < bars.length; i++) {
                if (bars[i].high > bars[bounceIdx].high) bounceIdx = i;
            }
            pB = { time: bars[bounceIdx].time, price: bars[bounceIdx].high, label: 'Bounce High (B)' };

            if (bounceIdx < bars.length - 1) {
                let retraceIdx = bounceIdx + 1;
                for (let i = bounceIdx + 1; i < bars.length; i++) {
                    if (bars[i].low < bars[retraceIdx].low) retraceIdx = i;
                }
                pC = { time: bars[retraceIdx].time, price: bars[retraceIdx].low, label: 'Retracement Low (C)' };
            } else {
                pC = { time: bars[sessionMinIdx].time, price: bars[sessionMinIdx].low, label: 'Base (C)' };
            }
        } else {
            const start = Math.max(0, bars.length - 20);
            let minI = start, maxI = start;
            for (let i = start; i < bars.length; i++) {
                if (bars[i].low < bars[minI].low) minI = i;
                if (bars[i].high > bars[maxI].high) maxI = i;
            }
            pA = { time: bars[minI].time, price: bars[minI].low, label: 'Swing Low (A)' };
            pB = { time: bars[maxI].time, price: bars[maxI].high, label: 'Swing High (B)' };
            pC = { time: bars[bars.length - 1].time, price: bars[bars.length - 1].low, label: 'Retracement Low (C)' };
        }
    }

    return { pA, pB, pC };
}

/**
 * Clear existing Fib price lines, markers, and HUD.
 */
function clearFibExtension() {
    if (activeFibCandleSeries) {
        activeFibPriceLines.forEach(line => {
            try { activeFibCandleSeries.removePriceLine(line); } catch (_) {}
        });
        try { activeFibCandleSeries.setMarkers([]); } catch (_) {}
    }
    activeFibPriceLines = [];
    if (dChartFibLevelsGrid) dChartFibLevelsGrid.innerHTML = '';
    if (dChartFibAnchorsInfo) dChartFibAnchorsInfo.textContent = '';
    if (dChartFibStatusText) dChartFibStatusText.textContent = 'Fib Extension: Cleared';
    if (dChartFibStatusBar) dChartFibStatusBar.classList.remove('picking');
    if (dChartContainer) dChartContainer.classList.remove('picking-points');
}

/**
 * Render Trend-Based Fib Extension levels on the chart and populate the HUD.
 */
function renderFibExtension(pA, pB, pC, candleSeries, bars) {
    if (!pA || !pB || !pC || !candleSeries || !bars || bars.length === 0) return;

    // Clear previous lines
    activeFibPriceLines.forEach(line => {
        try { candleSeries.removePriceLine(line); } catch (_) {}
    });
    activeFibPriceLines = [];

    const impulse = pB.price - pA.price;
    if (impulse <= 0) {
        if (dChartFibStatusText) dChartFibStatusText.textContent = 'Fib Warning: Impulse move (B - A) must be positive.';
        return;
    }

    const currentPrice = bars[bars.length - 1].close;

    // Set markers on the 3 anchor points
    candleSeries.setMarkers([
        { time: pA.time, position: 'belowBar', color: '#ff9800', shape: 'arrowUp',   text: `A: Low $${pA.price.toFixed(2)}` },
        { time: pB.time, position: 'aboveBar', color: '#2962ff', shape: 'arrowDown', text: `B: High $${pB.price.toFixed(2)}` },
        { time: pC.time, position: 'belowBar', color: '#4caf50', shape: 'arrowUp',   text: `C: Retrace $${pC.price.toFixed(2)}` }
    ]);

    const currentSym = dChartModalTicker ? dChartModalTicker.textContent.trim() : null;
    const currentTickerData = currentSym ? latestTickersData[currentSym] : null;
    const currentFibForecast = currentTickerData ? currentTickerData.fib_forecast : null;
    const dailyPeakTarget = currentFibForecast?.daily_peak_target ? Number(currentFibForecast.daily_peak_target) : null;
    const atrConfluence = currentFibForecast?.atr_confluence || false;
    const dailyPeakAtr = currentFibForecast?.daily_peak_atr ? Number(currentFibForecast.daily_peak_atr) : null;

    // Calculate extension levels
    let nextTargetFound = false;
    let hudCardsHtml = '';

    FIB_RATIOS.forEach(fib => {
        const levelPrice = pC.price + (fib.ratio * impulse);
        const pctGain = ((levelPrice - currentPrice) / currentPrice) * 100;
        const isAbove = levelPrice > currentPrice;
        let isNextTarget = false;

        if (isAbove && !nextTargetFound) {
            isNextTarget = true;
            nextTargetFound = true;
        }

        const isPeakTarget = dailyPeakTarget && Math.abs(levelPrice - dailyPeakTarget) / dailyPeakTarget <= 0.018;
        const peakBadgeHtml = isPeakTarget
            ? `<div class="fib-card-peak-badge">🏔️ DAILY PEAK${atrConfluence ? ' (ATR)' : ''}</div>`
            : '';

        // Add price line to chart
        const line = candleSeries.createPriceLine({
            price: levelPrice,
            color: isPeakTarget ? '#e040fb' : fib.color,
            lineWidth: (fib.isTarget || isPeakTarget) ? 2 : 1,
            lineStyle: (fib.isTarget || isPeakTarget) ? LightweightCharts.LineStyle.Solid : LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: `Fib ${fib.name}: $${levelPrice.toFixed(2)} (${pctGain >= 0 ? '+' : ''}${pctGain.toFixed(1)}%)${isPeakTarget ? ' [DAILY PEAK]' : ''}`,
        });
        activeFibPriceLines.push(line);

        // Build HUD Card
        const cardClass = `fib-level-card ${isNextTarget ? 'next-target' : ''} ${!isAbove ? 'surpassed' : ''}`;
        const pctDisplay = isAbove ? `+${pctGain.toFixed(1)}%` : `Reached ($${levelPrice.toFixed(2)})`;
        const frontRunPrice = (levelPrice * 0.9975).toFixed(2);
        const trancheBadge = fib.tranche ? `<div class="fib-card-tranche">${fib.tranche}</div>` : '';
        const frontRunHtml = `<div class="fib-card-frontrun">Limit (-0.25%): <strong>$${frontRunPrice}</strong></div>`;

        hudCardsHtml += `
            <div class="${cardClass}">
                <div class="fib-card-ratio" style="color:${fib.color};">
                    <span>Fib ${fib.name}</span>
                    ${isNextTarget ? '<span style="color:#2962ff;font-size:0.55rem;">NEXT</span>' : ''}
                </div>
                <div class="fib-card-price">$${levelPrice.toFixed(2)}</div>
                <div class="fib-card-pct">${pctDisplay}</div>
                ${frontRunHtml}
                ${trancheBadge}
                ${peakBadgeHtml}
                <div class="fib-card-role">${fib.role}</div>
            </div>
        `;
    });

    if (dChartFibLevelsGrid) dChartFibLevelsGrid.innerHTML = hudCardsHtml;
    if (dChartFibAnchorsInfo) {
        const peakInfo = dailyPeakTarget ? ` | Peak: $${dailyPeakTarget.toFixed(2)}${atrConfluence ? ' [ATR Confluence]' : ''}` : '';
        dChartFibAnchorsInfo.textContent = `A: $${pA.price.toFixed(2)} → B: $${pB.price.toFixed(2)} → C: $${pC.price.toFixed(2)} (Impulse: +$${impulse.toFixed(2)})${peakInfo}`;
    }
    if (dChartFibStatusText) {
        dChartFibStatusText.textContent = `Trend-Based Fib Extension: Anchored (${currentFibMode === 'auto' ? 'Auto-detected' : 'Manual'})`;
    }
    if (dChartFibStatusBar) dChartFibStatusBar.classList.remove('picking');
    if (dChartContainer) dChartContainer.classList.remove('picking-points');
}

/**
 * Apply Auto Fib Extension mode using session swings.
 */
function applyAutoFib() {
    if (!activeFibCandleSeries || !activeFibBars || activeFibBars.length === 0) return;
    currentFibMode = 'auto';
    manualPickStep = 0;

    if (dChartFibAutoBtn) dChartFibAutoBtn.classList.add('active');
    if (dChartFibPickBtn) dChartFibPickBtn.classList.remove('active');

    const swings = detectIntradaySwings(activeFibBars);
    if (swings && swings.pA && swings.pB && swings.pC) {
        renderFibExtension(swings.pA, swings.pB, swings.pC, activeFibCandleSeries, activeFibBars);
    } else {
        if (dChartFibStatusText) dChartFibStatusText.textContent = 'Not enough price action to auto-detect swings.';
    }
}

/**
 * Start Manual 3-Click Picking mode.
 */
function startManualPick() {
    if (!activeFibCandleSeries || !activeFibBars || activeFibBars.length === 0) return;
    currentFibMode = 'manual';
    manualPickStep = 1;
    manualFibPoints = { pA: null, pB: null, pC: null };

    if (dChartFibPickBtn) dChartFibPickBtn.classList.add('active');
    if (dChartFibAutoBtn) dChartFibAutoBtn.classList.remove('active');
    if (dChartFibStatusBar) dChartFibStatusBar.classList.add('picking');
    if (dChartContainer) dChartContainer.classList.add('picking-points');

    if (dChartFibStatusText) {
        dChartFibStatusText.textContent = 'Click 1 of 3: Click Swing Low (Point A) on any candle.';
    }
    if (dChartFibAnchorsInfo) dChartFibAnchorsInfo.textContent = 'Waiting for Point A...';
    // Clear existing price lines during picking
    activeFibPriceLines.forEach(line => {
        try { activeFibCandleSeries.removePriceLine(line); } catch (_) {}
    });
    activeFibPriceLines = [];
}

/**
 * Handle chart clicks for manual 3-point Fib selection.
 */
function handleFibChartClick(param) {
    if (currentFibMode !== 'manual' || manualPickStep === 0) return;
    if (!param || !param.time || !activeFibBars) return;

    // Find the bar clicked
    let bar = null;
    if (param.seriesData && activeFibCandleSeries) {
        bar = param.seriesData.get(activeFibCandleSeries);
    }
    if (!bar) {
        bar = activeFibBars.find(b => b.time === param.time);
    }
    if (!bar) return;

    if (manualPickStep === 1) {
        manualFibPoints.pA = { time: bar.time, price: bar.low, label: 'Swing Low (A)' };
        manualPickStep = 2;
        if (dChartFibStatusText) {
            dChartFibStatusText.textContent = `Point A ($${bar.low.toFixed(2)}) set. Click 2 of 3: Click Swing High (Point B).`;
        }
        if (dChartFibAnchorsInfo) dChartFibAnchorsInfo.textContent = `A: $${bar.low.toFixed(2)} → [Select B]`;
        activeFibCandleSeries.setMarkers([
            { time: bar.time, position: 'belowBar', color: '#ff9800', shape: 'arrowUp', text: `A: Low $${bar.low.toFixed(2)}` }
        ]);
    } else if (manualPickStep === 2) {
        manualFibPoints.pB = { time: bar.time, price: bar.high, label: 'Swing High (B)' };
        manualPickStep = 3;
        if (dChartFibStatusText) {
            dChartFibStatusText.textContent = `Point B ($${bar.high.toFixed(2)}) set. Click 3 of 3: Click Retracement Low (Point C).`;
        }
        if (dChartFibAnchorsInfo) dChartFibAnchorsInfo.textContent = `A: $${manualFibPoints.pA.price.toFixed(2)} → B: $${bar.high.toFixed(2)} → [Select C]`;
        activeFibCandleSeries.setMarkers([
            { time: manualFibPoints.pA.time, position: 'belowBar', color: '#ff9800', shape: 'arrowUp',   text: `A: Low $${manualFibPoints.pA.price.toFixed(2)}` },
            { time: bar.time,                position: 'aboveBar', color: '#2962ff', shape: 'arrowDown', text: `B: High $${bar.high.toFixed(2)}` }
        ]);
    } else if (manualPickStep === 3) {
        manualFibPoints.pC = { time: bar.time, price: bar.low, label: 'Retracement Low (C)' };
        manualPickStep = 0;
        renderFibExtension(manualFibPoints.pA, manualFibPoints.pB, manualFibPoints.pC, activeFibCandleSeries, activeFibBars);
    }
}

// Attach Fib toolbar button listeners
if (dChartFibAutoBtn) {
    dChartFibAutoBtn.addEventListener('click', applyAutoFib);
}
if (dChartFibPickBtn) {
    dChartFibPickBtn.addEventListener('click', startManualPick);
}
if (dChartFibClearBtn) {
    dChartFibClearBtn.addEventListener('click', () => {
        currentFibMode = 'none';
        manualPickStep = 0;
        if (dChartFibAutoBtn) dChartFibAutoBtn.classList.remove('active');
        if (dChartFibPickBtn) dChartFibPickBtn.classList.remove('active');
        clearFibExtension();
    });
}

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
 * Open the chart modal for a given symbol.
 * Fetches 1m OHLCV bars, builds the Lightweight Charts instance,
 * and overlays EMA 9, EMA 30, EMA 50, EMA 200, and VWAP line series.
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

        // ─── Moving Averages (EMA 9, EMA 30, EMA 50, EMA 200) ───
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
        const ema50Data  = emaToSeries(computeEMA(allCloses, 50),  times, warmupLen);
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

        // EMA 50 — Purple (#b388ff)
        const ema50Series = chart.addLineSeries({
            color: '#b388ff',
            lineWidth: 1.5,
            priceLineVisible: false,
            lastValueVisible: true,
        });
        ema50Series.setData(ema50Data);

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

        // Upper VWAP Band (Green #089981)
        const vwapUpperSeries = chart.addLineSeries({
            color: '#089981',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });
        vwapUpperSeries.setData(upperList);

        // Lower VWAP Band (Green #089981)
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

        // Trend-Based Fib Extension Integration
        activeFibBars = bars;
        activeFibCandleSeries = candleSeries;
        activeFibChart = chart;
        chart.subscribeClick(handleFibChartClick);

        // Auto mode as default
        applyAutoFib();

        // Store instance for screenshot capture + image copy
        chartInstances[symbol] = { chart, candleSeries };

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

        // Immediately capture a screenshot for this symbol
        setTimeout(() => captureChartScreenshot(symbol), 200);

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
    clearFibExtension();
    activeFibBars = [];
    activeFibCandleSeries = null;
    activeFibChart = null;
    currentFibMode = 'auto';
    manualPickStep = 0;
    // Remove only the currently displayed chart instance (identified by the modal ticker label)
    const sym = dChartModalTicker ? dChartModalTicker.textContent : null;
    if (sym && chartInstances[sym]) {
        try { chartInstances[sym].chart.remove(); } catch (_) {}
        delete chartInstances[sym];
    }
    if (dChartContainer) dChartContainer.innerHTML = '';
}

/**
 * Capture a screenshot of a specific chart instance and store it in chartScreenshots.
 * @param {string} symbol
 */
function captureChartScreenshot(symbol) {
    const instance = chartInstances[symbol];
    if (!instance || !instance.chart) return;
    try {
        const canvas = instance.chart.takeScreenshot();
        if (!canvas) return;
        const dataUrl = canvas.toDataURL('image/png');
        // Strip the data URI prefix — store raw base64 only
        chartScreenshots[symbol] = dataUrl.replace(/^data:image\/png;base64,/, '');
    } catch (e) {
        console.warn(`[Chart] Screenshot capture failed for ${symbol}:`, e);
    }
}

/**
 * Capture screenshots for all currently open chart instances.
 * Called automatically at end of each pollData() cycle and before copyMarketSnapshot.
 */
function captureAllChartScreenshots() {
    Object.keys(chartInstances).forEach(sym => captureChartScreenshot(sym));
}

/**
 * Copy the current chart image to clipboard as a PNG so it can be pasted
 * directly into Gemini's chat window.
 * @param {string} symbol
 * @param {HTMLElement} btn  - the button element to show feedback on
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

        const canvas = instance.chart.takeScreenshot();
        if (!canvas) throw new Error('takeScreenshot returned null');

        // Extract resistance targets from HUD
        let targetsText = `=== Trend-Based Fib Resistance Targets: ${symbol} ===\n`;
        const hudCards = document.querySelectorAll('#chart-fib-levels-grid .fib-level-card');
        const targetItems = [];
        
        hudCards.forEach(card => {
            const r = card.querySelector('.fib-card-ratio')?.textContent.trim() || '';
            const p = card.querySelector('.fib-card-price')?.textContent.trim() || '';
            const pct = card.querySelector('.fib-card-pct')?.textContent.trim() || '';
            const role = card.querySelector('.fib-card-role')?.textContent.trim() || '';
            const isNext = card.classList.contains('next-target');
            targetItems.push({ ratio: r, price: p, pct: pct, role: role, isNext: isNext });
            targetsText += `${r}: ${p} (${pct}) - ${role}\n`;
        });

        // Composite the Resistance Targets HUD table directly onto the chart canvas
        if (targetItems.length > 0) {
            const ctx = canvas.getContext('2d');
            const hudWidth = 380;
            const hudHeight = 36 + (targetItems.length * 17) + 8;
            const startX = 14;
            const startY = 14;

            ctx.save();
            ctx.fillStyle = 'rgba(19, 23, 34, 0.92)';
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.15)';
            ctx.lineWidth = 1;
            if (ctx.roundRect) {
                ctx.beginPath();
                ctx.roundRect(startX, startY, hudWidth, hudHeight, 6);
                ctx.fill();
                ctx.stroke();
            } else {
                ctx.fillRect(startX, startY, hudWidth, hudHeight);
                ctx.strokeRect(startX, startY, hudWidth, hudHeight);
            }

            // Header title
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.fillStyle = '#ffffff';
            ctx.fillText(`🎯 ${symbol} RESISTANCE TARGETS (Fib Extension)`, startX + 12, startY + 20);

            // Targets list
            ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace';
            let lineY = startY + 38;
            targetItems.forEach(item => {
                ctx.fillStyle = item.isNext ? '#29b6f6' : (item.pct.includes('Reached') ? '#787b86' : '#d1d4dc');
                const nextMarker = item.isNext ? '▶ ' : '  ';
                const text = `${nextMarker}${item.ratio.padEnd(10)} ${item.price.padEnd(8)} ${item.pct.padEnd(12)} ${item.role}`;
                ctx.fillText(text, startX + 10, lineY);
                lineY += 17;
            });
            ctx.restore();
        }

        // Convert canvas to Blob and write to clipboard
        const blob = await new Promise((resolve, reject) =>
            canvas.toBlob(b => b ? resolve(b) : reject(new Error('toBlob failed')), 'image/png')
        );

        const clipboardItems = { 'image/png': blob };
        if (targetsText) {
            clipboardItems['text/plain'] = new Blob([targetsText], { type: 'text/plain' });
        }
        await navigator.clipboard.write([new ClipboardItem(clipboardItems)]);

        if (btn) { btn.textContent = '✅ Copied!'; }
        setTimeout(() => { if (btn) { btn.textContent = originalText; btn.disabled = false; } }, 1800);
    } catch (e) {
        console.error('[Chart] Image copy failed:', e);
        if (btn) { btn.textContent = '❌ Failed'; }
        setTimeout(() => { if (btn) { btn.textContent = originalText; btn.disabled = false; } }, 2000);
    }
}
