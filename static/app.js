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
let MACRO_LABELS = {};
let currentEurUsdRate = 1.08; // Store EURUSD rate globally for real-time conversion

const dCopyBtn = document.getElementById('copy-json-btn');
const dCopySessionBtn = document.getElementById('copy-session-btn');
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

        const gapStr = (d.gap_percent > 0 ? '+' : '') + d.gap_percent.toFixed(2) + '%';
        const gapColor = d.gap_percent > 0 ? 'text-green' : (d.gap_percent < 0 ? 'text-red' : 'text-white');

        let details = '';
        if (d.rsi) details += `RSI ${d.rsi.toFixed(1)}`;
        if (d.atr_percent) details += ` · ATR ${d.atr_percent.toFixed(2)}%`;
        if (d.volume) details += ` · Vol ${formatVol(d.volume)}`;

        let trendStr = '';
        if (d.trend === 'UP') trendStr = ' · ▲ Up';
        else if (d.trend === 'DOWN') trendStr = ' · ▼ Down';
        else trendStr = ' · — Flat';

        return `
            <div class="index-card">
                <div class="index-name">${label} (${ticker})</div>
                <div class="index-price">${d.price.toFixed(2)}</div>
                <div class="index-gap ${gapColor}">${gapStr}${trendStr}</div>
                <div class="index-details">${details}</div>
            </div>`;
    }).join('');

    dIndicesGrid.innerHTML = html;
}

// Cache previous state to flash updates
let prevPrices = {};

// Initialization
async function init() {
    await fetchTickers();
    await fetchPortfolio();
    await fetchWatchlist();
    await fetchScoutCategories();
    pollData();
    window._pollInterval = setInterval(pollData, 3000); // 3 sec polling
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
                "4. PORTFOLIO HEALTH AUDIT: Flag any positions with health_score < 50 or",
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
if (dPasteBtn) dPasteBtn.addEventListener('click', () => ingestExecutionPayload(dPasteBtn, document.getElementById('inbound-paste-status')));



// ... (cleaned up)

// Format large numbers (Volume)
function formatVol(vol) {
    if (vol === null || vol === undefined) return '—';
    if (typeof vol === 'string') return vol;
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

    const userWatchlist = new Set(((ms.watched_tickers || state.watchlist) || []).map(s => s.toUpperCase()));

    tickers.forEach(t => {
        const sym = t.ticker.toUpperCase();
        if (MACRO_TICKERS.includes(sym) || sym === 'EURUSD=X') return;

        if (heldTickers.has(sym)) {
            groups.held.push(t);
        } else if (userWatchlist.has(sym)) {
            groups.watchlist.push(t);
        } else if (t._isScout) {
            groups.scouts.push(t);
        }
    });

    let html = '';

    const renderRow = (row) => {
        const sym = row.ticker;
        
        if (row.status === 'NO_DATA') {
            const noteHtml = row.note ? `<span class="note-tag danger">${row.note}</span>` : '<span class="note-tag danger">NO DATA</span>';
            return `
                <tr class="no-data-row">
                    <td class="ticker-cell ${row._isScout ? 'is-scout' : ''}">
                        <span class="ticker-symbol text-muted">${sym}</span>
                    </td>
                    <td class="text-muted">—</td>
                    <td class="text-muted">—</td>
                    <td class="text-muted">—</td>
                    <td class="text-muted">—</td>
                    <td class="text-muted">—</td>
                    <td class="text-muted">—</td>
                    <td class="text-muted">—</td>
                    <td><span class="trend-tag flat">— Flat</span></td>
                    <td><span class="dealer-badge dealer-neutral">NEUTRAL</span></td>
                    <td class="score-col">
                        <span class="score-badge neutral">0</span>${noteHtml}
                    </td>
                </tr>
            `;
        }

        const p = (row.price || 0).toFixed(2);
        
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
                <td class="${dayColor}">${row.session_change_pct > 0 ? '+' : ''}${(row.session_change_pct || 0).toFixed(2)}%</td>
                <td class="${gapColor}">${row.gap_percent > 0 ? '+' : ''}${(row.gap_percent || 0).toFixed(2)}%</td>
                <td>${formatVol(row.volume)}</td>
                <td>${(row.atr_percent || 0).toFixed(2)}%</td>
                <td class="${rsiColor}">${(row.rsi || 0).toFixed(1)}</td>
                <td>${row.vwap > 0 ? row.vwap.toFixed(2) : '—'}</td>
                <td>${trendHtml}</td>
                <td>${(() => {
                    const dp = row.dealer_posture || 'NEUTRAL';
                    let dpClass = 'dealer-neutral';
                    let dpLabel = dp;
                    if (dp === 'LONG_GAMMA') { dpClass = 'dealer-long'; dpLabel = 'LONG γ'; }
                    else if (dp === 'SHORT_GAMMA') { dpClass = 'dealer-short'; dpLabel = 'SHORT γ'; }
                    else { dpClass = 'dealer-neutral'; dpLabel = 'NEUTRAL'; }
                    return `<span class="dealer-badge ${dpClass}">${dpLabel}</span>`;
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

let currentFetchId = 0;
let lastProcessedFetchId = 0;

async function pollData() {
    const fetchId = ++currentFetchId;
    try {
        // Refresh managers if not focused
        const active = document.activeElement;
        if (!active || (!active.classList.contains('portfolio-input') && active !== dAddWatchlistTicker)) {
            fetchPortfolio();
            fetchWatchlist();
        }

        const res = await fetch(`${API_BASE}/data`);
        const state = await res.json();
        
        // Ignore out-of-order stale responses
        if (fetchId < lastProcessedFetchId) {
            return;
        }
        lastProcessedFetchId = fetchId;

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
                // Phase 2 maps to 50% to 95%
                overallPercent = Math.round(50 + (progress / total) * 45);
                phaseTitle = 'Phase 2: Compiling Option GEX Profiles';
                phaseDesc = `Fetching option chains & computing synthetic Gamma curves`;
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
                if (ticker && ticker !== 'SYSTEM') {
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
            if (state.tickers && Array.isArray(state.tickers)) {
                renderTable(state.tickers, state);
                
                if (typeof loadingScoutSectors !== 'undefined' && loadingScoutSectors.size > 0) {
                    const loadedCats = state.scout_categories_loaded || [];
                    loadingScoutSectors.forEach(sector => {
                        // Clear sector from loading list only if backend has successfully loaded it,
                        // or if the user has deselected it (not in activeScoutCategories)
                        if (loadedCats.includes(sector) || !activeScoutCategories.includes(sector)) {
                            loadingScoutSectors.delete(sector);
                        }
                    });
                    renderScoutCategories(activeScoutCategories);
                }
            }
            
            // Render Macro HUD dynamic cards
            if (state.tickers && Array.isArray(state.tickers)) {
                let hudHtml = '';
                
                currentMacroTickers.forEach(tickerStr => {
                    const row = state.tickers.find(t => t.ticker === tickerStr);
                    const label = MACRO_LABELS[tickerStr] || tickerStr;
                    const title = label === tickerStr ? tickerStr : `${label} (${tickerStr})`;
                    
                    if (row) {
                        latestMacroData[tickerStr] = row;
                        const gapStr = (row.gap_percent > 0 ? '+' : '') + (row.gap_percent || 0).toFixed(2) + '%';
                        const gapColor = row.gap_percent > 0 ? 'text-green' : 'text-red';
                        
                        hudHtml += `
                            <div class="macro-card glass-panel" id="macro-card-${tickerStr.replace(/[^a-zA-Z0-9]/g, '')}">
                                <h3>${title}</h3>
                                <div class="macro-val">${(row.price || 0).toFixed(2)}</div>
                                <div class="macro-gap ${gapColor}">${gapStr}</div>
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
        dStatus.textContent = 'ERR: ' + (e.message ? e.message.substring(0, 150) : 'DISCONNECTED');
        dStatus.style.color = 'var(--red)';
        
        // Stop polling so the error message stays visible
        if (window._pollInterval) {
            clearInterval(window._pollInterval);
        }
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
    const cashUsd = data.unallocated_cash_usd || 0;
    const rate = data.eurusd_rate || 1.08;
    currentEurUsdRate = rate; // Update global rate
    
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
        <tr class="cash-row" style="background: rgba(0, 255, 148, 0.03);">
            <td style="color: var(--green); font-weight: 700; font-size: 0.7rem;">CASH (€)</td>
            <td colspan="3"><input type="number" step="0.01" class="portfolio-input" id="cash-input-eur" value="${cash}" style="color: var(--green); width: 100%;"></td>
        </tr>
        <tr class="cash-row" style="background: rgba(0, 180, 255, 0.03);">
            <td style="color: var(--accent); font-weight: 700; font-size: 0.7rem;">CASH ($)</td>
            <td colspan="3"><input type="number" step="0.01" class="portfolio-input" id="cash-input-usd" value="${cashUsd}" style="color: var(--accent); width: 100%;"></td>
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
        const shares = parseFloat(row.querySelector('[data-key="shares"]').value) || 0;
        if (shares > 0) {
            portfolio.push({
                ticker: row.cells[0].textContent,
                shares: shares,
                wac: parseFloat(row.querySelector('[data-key="wac"]').value) || 0
            });
        }
    });
    return portfolio;
}

function getCurrentCash() {
    const cashInputEur = document.getElementById('cash-input-eur');
    const cashInputUsd = document.getElementById('cash-input-usd');
    return {
        unallocated_cash_eur: cashInputEur ? parseFloat(cashInputEur.value) || 0 : 0,
        unallocated_cash_usd: cashInputUsd ? parseFloat(cashInputUsd.value) || 0 : 0
    };
}
async function savePortfolio(portfolioArr) {
    const btn = dSavePortfolioBtn;
    const originalText = btn.innerHTML;
    btn.innerHTML = "WAIT...";
    btn.disabled = true;
    try {
        const pArray = portfolioArr || getCurrentPortfolio();
        const cashObj = getCurrentCash();
        const payload = {
            portfolio: pArray,
            unallocated_cash_eur: cashObj.unallocated_cash_eur,
            unallocated_cash_usd: cashObj.unallocated_cash_usd
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
        } else {
            let errMsg = 'Failed to update portfolio.';
            try {
                const data = await res.json();
                errMsg = data.detail || data.message || errMsg;
            } catch (err) {}
            alert(`Validation Error: ${errMsg}`);
            await fetchPortfolio(); // rollback inputs in UI
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
    const rows = dPortfolioBody.querySelectorAll('tr.portfolio-item-row');
    if (rows[index]) {
        rows[index].remove();
    }
    const portfolio = getCurrentPortfolio();
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
        } else {
            let errMsg = 'Failed to update watchlist.';
            try {
                const data = await res.json();
                errMsg = data.detail || data.message || errMsg;
            } catch (err) {}
            alert(`Validation Error: ${errMsg}`);
            await fetchWatchlist(); // rollback in UI
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
let verifiedScoutSectors = [];
let activeScoutCategories = [];
let loadingScoutSectors = new Set();

async function fetchScoutCategories() {
    try {
        const sectorsRes = await fetch(`${API_BASE}/scout_sectors`);
        const sectorsData = await sectorsRes.json();
        if (sectorsData && sectorsData.length > 0) {
            verifiedScoutSectors = sectorsData;
        } else {
            verifiedScoutSectors = [
                "Technology", "Healthcare", "Financials", "Energy", "Industrials", 
                "Consumer Discretionary", "Consumer Staples", "Utilities", 
                "Real Estate", "Materials", "Communication Services",
                "AI & Data", "Aerospace & Defense", "Biotech", "Semiconductors"
            ];
        }
        
        const res = await fetch(`${API_BASE}/scout`);
        activeScoutCategories = await res.json();
        renderScoutCategories(activeScoutCategories);
    } catch (e) { console.error("Scout categories fetch failed", e); }
}

function renderScoutCategories(activeList) {
    if (!dScoutContainer) return;
    const activeSet = new Set(activeList);
    
    let html = '';
    verifiedScoutSectors.forEach(sector => {
        const isActive = activeSet.has(sector);
        const isLoading = loadingScoutSectors.has(sector);
        
        let style = '';
        let label = sector;
        
        if (isLoading) {
            style = 'background: rgba(255, 193, 7, 0.2); border-color: var(--yellow); color: var(--yellow); animation: pulse 1.5s infinite;';
            label = sector + ' 📡';
        } else if (isActive) {
            style = 'background: rgba(0, 255, 148, 0.2); border-color: var(--green); color: var(--green);';
        } else {
            style = 'background: rgba(255, 255, 255, 0.03); border-color: var(--panel-border); color: var(--text-dim);';
        }
        
        html += `
            <button class="scout-toggle-btn" 
                    onclick="toggleScoutCategory('${sector}')" 
                    style="padding: 4px 8px; border-radius: 4px; border: 1px solid; font-size: 0.65rem; font-weight: 600; cursor: pointer; transition: all 0.2s; ${style}">
                ${label}
            </button>
        `;
    });
    dScoutContainer.innerHTML = `<div style="display: flex; flex-wrap: wrap; gap: 6px;">${html}</div>`;
}

async function toggleScoutCategory(sector) {
    try {
        if (activeScoutCategories.includes(sector)) {
            activeScoutCategories = activeScoutCategories.filter(c => c !== sector);
            loadingScoutSectors.delete(sector);
        } else {
            activeScoutCategories.push(sector);
            loadingScoutSectors.add(sector);
        }
        
        // Optimistically render instantly with zero lag!
        renderScoutCategories(activeScoutCategories);
        
        // POST to the backend asynchronously in the background
        fetch(`${API_BASE}/scout`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(activeScoutCategories)
        }).catch(err => {
            console.error("Scout categories POST failed, rolling back to SSoT", err);
            loadingScoutSectors.delete(sector);
            fetchScoutCategories(); // rollback on connection/server failure
        });
        
    } catch (e) { 
        console.error("Toggle failed", e); 
        loadingScoutSectors.delete(sector);
        fetchScoutCategories();
    }
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


// ─── Mobile Quick Action Bridge ───
const dMobileCopyBtn = document.getElementById('mobile-copy-json-btn');
const dMobilePasteBtn = document.getElementById('mobile-paste-payload-btn');
const dMobileCopySessionBtn = document.getElementById('mobile-copy-session-btn');
const dMobileReviewBtn = document.getElementById('mobile-btn-session-review');

if (dMobileCopyBtn) dMobileCopyBtn.addEventListener('click', () => copyMarketSnapshot(dMobileCopyBtn, dMobileStatus));
if (dMobilePasteBtn) dMobilePasteBtn.addEventListener('click', () => ingestExecutionPayload(dMobilePasteBtn, dMobileStatus));
if (dMobileCopySessionBtn) dMobileCopySessionBtn.addEventListener('click', () => copySessionBoot(dMobileCopySessionBtn, dMobileStatus));
if (dMobileReviewBtn) dMobileReviewBtn.addEventListener('click', () => copySessionReviewPayload(dMobileReviewBtn, dMobileStatus));
