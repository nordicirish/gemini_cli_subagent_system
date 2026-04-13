const API_BASE = '/api';

// DOM Elements
const dTableBody = document.getElementById('table-body');
const dStatus = document.getElementById('market-status');
const dUpdated = document.getElementById('last-updated');
const dIndicator = document.getElementById('live-indicator');
const dtInput = document.getElementById('ticker-input');
const dUpdateBtn = document.getElementById('update-tickers-btn');
const dTStatus = document.getElementById('ticker-status');

const dIndicesInput = document.getElementById('indices-input');
const dUpdateIndicesBtn = document.getElementById('update-indices-btn');
const dIStatus = document.getElementById('indices-status');
const dDynamicMacroCards = document.getElementById('dynamic-macro-cards');

let currentMacroTickers = [];
let MACRO_LABELS = {};

const dCopyBtn = document.getElementById('copy-json-btn');
const dCopyAllBtn = document.getElementById('copy-all-btn');
const dPasteBtn = document.getElementById('paste-payload-btn');
const dDataStatus = document.getElementById('data-status');

// Modal elements
const dTickerModalOverlay = document.getElementById('ticker-modal-overlay');
const dOpenModalBtn = document.getElementById('open-ticker-modal-btn');
const dCloseModalBtn = document.getElementById('close-ticker-modal');
const dAlertsPanel = document.getElementById('alerts-panel');

const dIndicesModalOverlay = document.getElementById('indices-modal-overlay');
const dOpenIndicesBtn = document.getElementById('open-indices-modal-btn');
const dCloseIndicesBtn = document.getElementById('close-indices-modal');
const dIndicesGrid = document.getElementById('indices-grid');

// Store latest macro data for indices modal
let latestMacroData = {};

// Generic modal helpers
function openModal(overlay) { overlay.classList.add('active'); }
function closeModal(overlay) { overlay.classList.remove('active'); }

// Ticker Modal
dOpenModalBtn.addEventListener('click', () => {
    openModal(dTickerModalOverlay);
    dtInput.focus();
});
dCloseModalBtn.addEventListener('click', () => closeModal(dTickerModalOverlay));
dTickerModalOverlay.addEventListener('click', (e) => {
    if (e.target === dTickerModalOverlay) closeModal(dTickerModalOverlay);
});

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
        closeModal(dTickerModalOverlay);
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
    pollData();
    setInterval(pollData, 3000); // 3 sec polling
}

// Fetch current ticker list
async function fetchTickers() {
    try {
        const res = await fetch(`${API_BASE}/tickers`);
        const data = await res.json();
        dtInput.value = data.tickers.join(', ');
        dIndicesInput.value = data.macro.join(', ');
        currentMacroTickers = data.macro;
        if (data.macro_labels) MACRO_LABELS = data.macro_labels;
    } catch (e) {
        console.error("Failed to fetch tickers", e);
    }
}

// Update tickers list via POST
dUpdateBtn.addEventListener('click', async () => {
    const raw = dtInput.value;
    const items = raw.split(/[\s,]+/).map(t => t.trim()).filter(t => t);
    
    dUpdateBtn.disabled = true;
    dUpdateBtn.textContent = 'Updating...';
    
    try {
        const res = await fetch(`${API_BASE}/tickers`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({tickers: items})
        });
        const data = await res.json();
        if(data.status === 'success') {
            dTStatus.textContent = 'Tracked tickers updated successfully.';
            dTStatus.className = 'status-message text-green';
            setTimeout(() => { dTStatus.textContent = ''; }, 3000);
        }
    } catch (e) {
        console.error(e);
        dTStatus.textContent = 'Failed to update tickers.';
        dTStatus.className = 'status-message text-red';
    } finally {
        dUpdateBtn.disabled = false;
        dUpdateBtn.textContent = 'Update Tickers';
    }
});

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

// Copy Turn Data Handler (lightweight per-turn LLM payload — no SSOT, no lessons)
dCopyBtn.addEventListener('click', async () => {
    try {
        const res = await fetch(`${API_BASE}/data`);
        const state = await res.json();
        
        // Build lightweight ticker snapshots
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
            signal: t.signal
        }));
        
        // Extract compact mutable_state from full SSOT
        const ssot = state.local_storage_state || {};
        const ms = ssot.mutable_state || {};
        const fi = ms.forensic_intelligence || {};
        const portfolioFull = ms.portfolio_snapshot || [];
        
        const slimPortfolio = portfolioFull.map(p => {
            const entry = { ticker: p.ticker };
            if (p.shares !== undefined) entry.shares = p.shares;
            if (p.wac !== undefined) entry.wac = p.wac;
            if (p.status) entry.status = p.status;
            if (p.limit !== undefined) entry.limit = p.limit;
            if (p.action) entry.action = p.action;
            if (p.trade_state) entry.trade_state = p.trade_state;
            return entry;
        });
        
        const turnPayload = {
            _meta: state._meta,
            timestamp: state.timestamp,
            status: state.status,
            tickers: slimTickers,
            mutable_state: {
                unallocated_cash_eur: fi.unallocated_cash_eur || 0,
                total_liquidity_eur: fi.total_liquidity_eur || 0,
                risk_regime: (ms.state_context || {}).risk_regime || '',
                portfolio_snapshot: slimPortfolio
            }
        };
        
        const jsonString = JSON.stringify(turnPayload, null, 2);
        await navigator.clipboard.writeText(jsonString);
        
        dDataStatus.textContent = 'Turn data copied (lightweight)!';
        dDataStatus.className = 'status-message text-green';
        setTimeout(() => { dDataStatus.textContent = ''; }, 3000);
    } catch (e) {
        console.error(e);
        dDataStatus.textContent = 'Failed to copy data.';
        dDataStatus.className = 'status-message text-red';
        setTimeout(() => { dDataStatus.textContent = ''; }, 3000);
    }
});

// Copy All Data Handler (SSOT + Ticker Data + Trade Lessons)
dCopyAllBtn.addEventListener('click', async () => {
    try {
        const res = await fetch(`${API_BASE}/data`);
        const state = await res.json();
        
        const jsonString = JSON.stringify(state, null, 2);
        
        await navigator.clipboard.writeText(jsonString);
        
        dDataStatus.textContent = 'All data copied (SSOT + Tickers + Lessons)!';
        dDataStatus.className = 'status-message text-green';
        setTimeout(() => { dDataStatus.textContent = ''; }, 3000);
    } catch (e) {
        console.error(e);
        dDataStatus.textContent = 'Failed to copy data.';
        dDataStatus.className = 'status-message text-red';
        setTimeout(() => { dDataStatus.textContent = ''; }, 3000);
    }
});

// Paste Payload Handler
dPasteBtn.addEventListener('click', async () => {
    dPasteBtn.disabled = true;
    dPasteBtn.textContent = 'Ingesting...';
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
            dDataStatus.textContent = 'Payload successfully ingested!';
            dDataStatus.className = 'status-message text-green';
        } else {
            throw new Error(data.message);
        }
    } catch (e) {
        console.error("Paste Error: ", e);
        dDataStatus.textContent = e.message || 'Failed to paste/parse payload.';
        dDataStatus.className = 'status-message text-red';
    } finally {
        setTimeout(() => { dDataStatus.textContent = ''; }, 3000);
        dPasteBtn.disabled = false;
        dPasteBtn.textContent = 'Paste Execution Payload';
    }
});

// Format large numbers (Volume)
function formatVol(vol) {
    if (vol >= 1000000) return (vol / 1000000).toFixed(2) + 'M';
    if (vol >= 1000) return (vol / 1000).toFixed(1) + 'K';
    return vol.toString();
}

// Render the data table
function renderTable(tickers) {
    // If no tickers are processed yet, skip.
    if (!tickers || !tickers.length) return;

    // Filter out Macro trackers from main table to keep it focused on equities
    // Uses the dynamically fetched list from the backend instead of hardcoding
    const MACRO_TICKERS = currentMacroTickers && currentMacroTickers.length > 0 
        ? currentMacroTickers 
        : ['SPY', '^VIX', 'UUP', 'IEF', 'GLD', 'GDX'];
    
    const html = tickers.map(row => {
        if (!row) return '';
        
        const sym = row.ticker;
        const p = row.price.toFixed(2);
        
        // Determine price flashes
        let pClass = '';
        if(prevPrices[sym]) {
            if(p > prevPrices[sym]) pClass = 'flash-up';
            else if(p < prevPrices[sym]) pClass = 'flash-down';
        }
        prevPrices[sym] = p;

        // Colors logic matching python rules
        let gapColor = row.gap_percent > 0 ? 'text-green' : (row.gap_percent < 0 ? 'text-red' : 'text-white');
        
        let rsiColor = 'text-white';
        if (row.rsi >= 70) rsiColor = 'text-red';
        else if (row.rsi <= 30) rsiColor = 'text-green';

        // Score badge
        let scoreStr = row.score > 0 ? `+${row.score}` : `${row.score}`;
        let scoreBadge = 'neutral';
        if (row.score >= 5) scoreBadge = 'positive';
        else if (row.score <= -5) scoreBadge = 'negative';
        
        // Note tag
        const noteHtml = row.note ? `<span class="note-tag">${row.note}</span>` : '';
        
        // Trend tag
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

        // Hide macro tickers from main table
        if(MACRO_TICKERS.includes(sym)) return '';

        return `
            <tr>
                <td class="ticker-cell">${sym}</td>
                <td class="${pClass}">${p}</td>
                <td class="${gapColor}">${row.gap_percent > 0 ? '+' : ''}${row.gap_percent.toFixed(2)}%</td>
                <td>${formatVol(row.volume)}</td>
                <td>${row.atr_percent.toFixed(2)}%</td>
                <td class="${rsiColor}">${row.rsi.toFixed(1)}</td>
                <td>${row.vwap > 0 ? row.vwap.toFixed(2) : '—'}</td>
                <td>${trendHtml}</td>
                <td class="score-col">
                    <span class="score-badge ${scoreBadge}">${scoreStr}</span>${noteHtml}
                </td>
            </tr>
        `;
    }).join('');

    dTableBody.innerHTML = html;
}

async function pollData() {
    try {
        const res = await fetch(`${API_BASE}/data`);
        const state = await res.json();

        if (state && Object.keys(state).length > 0) {
            dIndicator.classList.add('active');
            dStatus.textContent = state.status || 'UNKNOWN';
            
            let statColor = 'var(--green)';
            if(state.status === 'PRE-MARKET') statColor = 'var(--accent)';
            else if(state.status === 'AFTER-HOURS') statColor = 'var(--purple)';
            else if(state.status === 'CLOSED') statColor = 'var(--red)';
            dStatus.style.color = statColor;

            // Updated time
            const ts = new Date(state._meta?.timestamp_iso || Date.now());
            dUpdated.textContent = ts.toLocaleTimeString();

            // Heavy Refresh indicator
            const refreshContainer = document.getElementById('refresh-status-container');
            if (refreshContainer) {
                refreshContainer.style.display = state.is_heavy_refresh ? 'flex' : 'none';
            }

            // Render table
            renderTable(state.tickers);
            
            // Render Macro HUD dynamic cards
            if (state.tickers) {
                let hudHtml = '';
                
                currentMacroTickers.forEach(tickerStr => {
                    const row = state.tickers.find(t => t.ticker === tickerStr);
                    const label = MACRO_LABELS[tickerStr] || tickerStr;
                    const title = label === tickerStr ? tickerStr : `${label} (${tickerStr})`;
                    
                    if (row) {
                        latestMacroData[tickerStr] = row;
                        const gapStr = (row.gap_percent > 0 ? '+' : '') + row.gap_percent.toFixed(2) + '%';
                        const gapColor = row.gap_percent > 0 ? 'text-green' : 'text-red';
                        
                        hudHtml += `
                            <div class="macro-card glass-panel" id="macro-card-${tickerStr.replace(/[^a-zA-Z0-9]/g, '')}">
                                <h3>${title}</h3>
                                <div class="macro-val">${row.price.toFixed(2)}</div>
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
                const alertsContainer = document.getElementById('alerts-container');
                const vix = state.tickers.find(t => t.ticker === '^VIX');
                const ief = state.tickers.find(t => t.ticker === 'IEF');
                let alertsHtml = '';
                if(vix && vix.price > 20 && vix.gap_percent > 2.0) {
                    alertsHtml += `<div class="alert-item risk">FEAR ALERT: VIX Volatility elevated (Gap +${vix.gap_percent.toFixed(2)}%)</div>`;
                }
                if(ief && ief.gap_percent < -0.15) {
                    alertsHtml += `<div class="alert-item risk">BOND ALERT: Yields rising rapidly. High Risk Environment.</div>`;
                }
                if(alertsHtml) {
                    alertsContainer.innerHTML = alertsHtml;
                    dAlertsPanel.classList.add('has-alerts');
                } else {
                    alertsContainer.innerHTML = '<p class="empty-state">All clear — no active alerts.</p>';
                    dAlertsPanel.classList.remove('has-alerts');
                }
            }

        } else {
             dIndicator.classList.remove('active');
             dStatus.textContent = 'BOOTING / AWAITING TICK...';
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
