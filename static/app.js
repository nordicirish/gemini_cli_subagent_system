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
        const res = await fetch(`${API_BASE}/data`);
        const state = await res.json();
        
        const slimTickers = (state.tickers || []).map(t => ({
            ticker: t.ticker,
            price: t.price,
            gap_percent: t.gap_percent,
            vwap: t.vwap,
            rsi: t.rsi,
            atr_percent: t.atr_percent,
            net_gex_total: t.net_gex_total,
            dealer_posture: t.dealer_posture,
            score: t.score,
            trend: t.trend,
            signal: t.signal,
            historical_context: t.historical_context
        }));
        
        const ssot = state.local_storage_state || {};
        const ms = ssot.mutable_state || {};
        const portfolioFull = ms.portfolio_snapshot || [];
        
        const slimPortfolio = portfolioFull.map(p => {
            const entry = { ticker: p.ticker };
            if (p.shares !== undefined) entry.shares = p.shares;
            if (p.wac !== undefined) entry.wac = p.wac;
            if (p.status) entry.status = p.status;
            if (p.limit !== undefined) entry.limit = p.limit;
            if (p.action) entry.action = p.action;
            if (p.trade_state) entry.trade_state = p.trade_state;
            if (p.historical_context) entry.historical_context = p.historical_context;
            return entry;
        });
        
        const turnPayload = {
            _meta: state._meta,
            timestamp: state.timestamp,
            status: state.status,
            tickers: slimTickers,
            mutable_state: {
                unallocated_cash_eur: ms.unallocated_cash_eur || 0,
                unallocated_cash_usd: ms.unallocated_cash_usd || 0,
                total_liquidity_eur: ms.total_liquidity_eur || 0,
                risk_regime: (ms.state_context || {}).risk_regime || '',
                portfolio_snapshot: slimPortfolio
            }
        };
        
        let snapshotPrompt = "";
        try {
            const promptRes = await fetch(`${API_BASE}/prompts/snapshot`);
            const promptData = await promptRes.json();
            snapshotPrompt = promptData.prompt || "";
        } catch (pe) {
            console.warn("Failed to fetch dynamic snapshot prompt, using fallback:", pe);
        }
        if (!snapshotPrompt) {
            snapshotPrompt = [
                "SYSTEM DIRECTIVE: ROUTINE TURN EXECUTION",
                "",
                "You are receiving the latest Market Snapshot and Portfolio State.",
                "1. Parse the JSON payload and synchronize your local context.",
                "2. Evaluate current 'risk_regime' and 'dealer_posture' shifts.",
                "3. Route the data through the Consensus Pipeline (Data Analyst -> Council Debate -> Synthesis) for any required rebalancing, entries, or defensive trims.",
                "4. Conclude your turn by outputting the final EXECUTION_PAYLOAD."
            ].join("\n");
        }
        const jsonString = snapshotPrompt + "\n\n```json\n" + JSON.stringify(turnPayload, null, 2) + "\n```";
        await navigator.clipboard.writeText(jsonString);
        showFeedback(triggerBtn, "✅ Copied!", "Market snapshot ready!", false, statusEl);
    } catch (e) {
        console.error(e);
        showFeedback(triggerBtn, "❌ Error", "Failed to copy snapshot.", true, statusEl);
    }
}

async function copySessionBoot(triggerBtn, statusEl) {
    try {
        const res = await fetch(`${API_BASE}/data`);
        const state = await res.json();
        
        const ssot = state.local_storage_state || {};
        const ms = ssot.mutable_state || {};
        const portfolio = ms.portfolio_snapshot || [];
        const heldTickers = new Set(portfolio.map(p => p.ticker.toUpperCase()));
        
        const filteredTickers = (state.tickers || []).filter(t => {
            const sym = t.ticker.toUpperCase();
            const isHeld = heldTickers.has(sym);
            const isMacro = (currentMacroTickers || []).map(m => m.toUpperCase()).includes(sym);
            return isHeld || isMacro;
        });

        const now = new Date();
        const tOffset = -now.getTimezoneOffset();
        const offSign = tOffset >= 0 ? '+' : '-';
        const offPad = (num) => String(Math.floor(Math.abs(num))).padStart(2, '0');
        const localTS = now.getFullYear() +
            '-' + offPad(now.getMonth() + 1) +
            '-' + offPad(now.getDate()) +
            'T' + offPad(now.getHours()) +
            ':' + offPad(now.getMinutes()) +
            ':' + offPad(now.getSeconds()) +
            offSign + offPad(tOffset / 60) +
            ':' + offPad(tOffset % 60);

        const sessionPayload = {
            _meta: state._meta,
            timestamp: localTS,
            status: state.status,
            tickers: filteredTickers,
            local_storage_state: state.local_storage_state,
            trade_lessons: state.trade_lessons
        };

        if (sessionPayload.local_storage_state && 
            sessionPayload.local_storage_state.mutable_state && 
            sessionPayload.local_storage_state.mutable_state.state_context) {
            sessionPayload.local_storage_state.mutable_state.state_context.timestamp = localTS;
            sessionPayload.local_storage_state.mutable_state.state_context.status = state.status;
        }
        
        let bootPrompt = "";
        try {
            const promptRes = await fetch(`${API_BASE}/prompts/boot`);
            const promptData = await promptRes.json();
            bootPrompt = promptData.prompt || "";
        } catch (pe) {
            console.warn("Failed to fetch dynamic boot prompt, using fallback:", pe);
        }
        if (!bootPrompt) {
            bootPrompt = [
                "SYSTEM BOOT: COUNCIL SESSION INITIALIZATION",
                "",
                "You are receiving the full SSoT state and live market data for a new session.",
                "Execute the following Stage 0 Boot Sequence:",
                "",
                "1. BASELINE SYNC (ENH_31): Ground all portfolio prices via Google Search.",
                "   Verify each ticker's current price against the provided snapshot.",
                "2. CASH RECONCILIATION (MANDATE_31): Confirm unallocated_cash_eur matches",
                "   the SSoT. Output: math_proof_liquidity.",
                "3. REGIME CLASSIFICATION: Assess current risk regime (TRENDING/MEAN_REVERTING/",
                "   VOLATILE) based on VIX, VIXY velocity, and SPY structure.",
                "4. PORTFOLIO HEALTH AUDIT: Flag any positions with score < 0 or",
                "   status = IN_DISTRESS for immediate Council review.",
                "5. MARKET POSTURE: Provide a top-level posture assessment (RISK_ON/RISK_OFF/",
                "   NEUTRAL) with supporting forensic evidence.",
                "",
                "Emit the full EXECUTION_PAYLOAD with updated state_context upon completion."
            ].join("\n");
        }
        const jsonString = bootPrompt + "\n\n```json\n" + JSON.stringify(sessionPayload, null, 2) + "\n```";
        
        await navigator.clipboard.writeText(jsonString);
        showFeedback(triggerBtn, "✅ Copied!", "Session boot payload ready!", false, statusEl);
    } catch (e) {
        console.error(e);
        showFeedback(triggerBtn, "❌ Error", "Failed to copy session boot.", true, statusEl);
    }
}

async function copyNewsScan(triggerBtn, statusEl) {
    try {
        const res = await fetch(`${API_BASE}/data`);
        const state = await res.json();
        
        const ssot = state.local_storage_state || {};
        const ms = ssot.mutable_state || {};
        const portfolio = ms.portfolio_snapshot || [];
        const watchlist = state.watchlist || [];
        
        const targetTickers = new Set([
            ...portfolio.map(p => p.ticker.toUpperCase()),
            ...watchlist.map(w => w.toUpperCase())
        ]);
        
        const slimTickers = (state.tickers || [])
            .filter(t => targetTickers.has(t.ticker.toUpperCase()))
            .map(t => ({
                ticker: t.ticker,
                price: t.price,
                historical_context: t.historical_context
            }));
            
        const now = new Date();
        const tOffset = -now.getTimezoneOffset();
        const offSign = tOffset >= 0 ? '+' : '-';
        const offPad = (num) => String(Math.floor(Math.abs(num))).padStart(2, '0');
        const localTS = now.getFullYear() +
            '-' + offPad(now.getMonth() + 1) +
            '-' + offPad(now.getDate()) +
            'T' + offPad(now.getHours()) +
            ':' + offPad(now.getMinutes()) +
            ':' + offPad(now.getSeconds()) +
            offSign + offPad(tOffset / 60) +
            ':' + offPad(tOffset % 60);
            
        const scanPayload = {
            timestamp: localTS,
            status: state.status,
            tickers: slimTickers,
            macro_calendar_shield: ms.macro_calendar_shield || {},
            portfolio_snapshot: portfolio.map(p => ({
                ticker: p.ticker,
                shares: p.shares,
                wac: p.wac,
                status: p.status,
                trade_state: p.trade_state,
                historical_context: p.historical_context
            }))
        };
        
        let newsScanPrompt = "";
        try {
            const promptRes = await fetch(`${API_BASE}/prompts/news_scan`);
            const promptData = await promptRes.json();
            newsScanPrompt = promptData.prompt || "";
        } catch (pe) {
            console.warn("Failed to fetch news scan prompt:", pe);
        }
        if (!newsScanPrompt) {
            newsScanPrompt = "SYSTEM DIRECTIVE: MACRO & STOCK NEWS SCAN\nPerform a search for macroeconomic/political events and stock-specific news.";
        }
        const jsonString = newsScanPrompt + "\n\n```json\n" + JSON.stringify(scanPayload, null, 2) + "\n```";
        await navigator.clipboard.writeText(jsonString);
        showFeedback(triggerBtn, "✅ Copied!", "News scan prompt ready!", false, statusEl);
    } catch (e) {
        console.error(e);
        showFeedback(triggerBtn, "❌ Error", "Failed to copy news scan.", true, statusEl);
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
    const portfolio = ms.portfolio_snapshot || [];
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
        return `
            <tr>
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
                <td>${trendHtml}</td>
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
            <td colspan="11">${label}</td>
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
                    <td colspan="11">Scout Intelligence Suggestions</td>
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
            
            // Render Macro HUD dynamic cards
            if (state.tickers) {
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
    
    portfolio.forEach((item, index) => {
        html += `
            <tr data-index="${index}" class="portfolio-item-row">
                <td style="color: var(--accent); font-weight: 700; font-size: 0.8rem;">${item.ticker}</td>
                <td><input type="number" class="portfolio-input" data-key="shares" value="${item.shares || 0}"></td>
                <td><input type="number" step="0.01" class="portfolio-input" data-key="wac" value="${item.wac || 0}"></td>
                <td><button class="delete-btn" onclick="deleteFromPortfolio(${index})">&times;</button></td>
            </tr>
        `;
    });

    html += `
        <tr class="cash-row" style="background: rgba(0, 255, 148, 0.05);">
            <td style="color: var(--green); font-weight: 700; font-size: 0.75rem;">CASH (€)</td>
            <td><input type="number" step="0.01" class="portfolio-input" id="cash-input-eur" value="${cash}" style="color: var(--green);"></td>
            <td colspan="2" style="font-size: 0.75rem; color: var(--text-dim); text-align: left; padding-left: 8px;">$${usd}</td>
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
            showFeedback(btn, "✅ Synced!", "Portfolio successfully updated! (Table refreshing...)");
            await fetchPortfolio();
            pollData(); // Force immediate refresh
        }
    } catch (e) { console.error("Portfolio update failed", e); }
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

dAddToPortfolioBtn.addEventListener('click', addToPortfolio);
dSavePortfolioBtn.addEventListener('click', () => savePortfolio());
dAddToWatchlistBtn.addEventListener('click', addToWatchlist);

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
        showFeedback(btn, "✅ Copied!", "Audit & Rule Review payload ready!", false, targetStatus);
    } catch (error) {
        console.error("Failed to copy Review payload:", error);
        showFeedback(btn, "❌ Error", "Failed to fetch review log.", true, targetStatus);
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
        
        dSetupWizardStatus.innerHTML = '<span style="color: var(--red); font-weight: bold;">⚠️ credentials.json is missing. Upload it below to continue.</span>';
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
