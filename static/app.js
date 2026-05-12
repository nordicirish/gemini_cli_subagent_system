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

const dCopyBtn = document.getElementById('copy-json-btn');
const dCopySessionBtn = document.getElementById('copy-session-btn');
const dPasteBtn = document.getElementById('paste-payload-btn');
const dDataStatus = document.getElementById('data-status');

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
function showFeedback(btn, btnText, statusMsg, isError = false) {
    if (btn.dataset.isFeedback === "true") return; // Prevent re-triggering during active feedback
    
    btn.dataset.isFeedback = "true";
    const originalHtml = btn.innerHTML;
    btn.innerHTML = btnText;
    btn.classList.add(isError ? 'btn-error' : 'btn-success');
    
    dDataStatus.textContent = statusMsg;
    dDataStatus.className = `status-message ${isError ? 'text-red' : 'text-green'}`;
    
    setTimeout(() => {
        btn.innerHTML = originalHtml;
        btn.classList.remove('btn-error', 'btn-success');
        dDataStatus.textContent = '';
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
            if (p.historical_context) entry.historical_context = p.historical_context;
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
        
        const jsonString = "```json\n" + JSON.stringify(turnPayload, null, 2) + "\n```";
        await navigator.clipboard.writeText(jsonString);
        showFeedback(dCopyBtn, "✅ Copied!", "Turn data copied (lightweight)!");
    } catch (e) {
        console.error(e);
        showFeedback(dCopyBtn, "❌ Error", "Failed to copy data.", true);
    }
});

// Copy Session Init Handler (Full SSOT + Tickers + Lessons)
dCopySessionBtn.addEventListener('click', async () => {
    try {
        // Wire clearing to session init as requested
        await fetch(`${API_BASE}/clear_decision_log`, { method: 'POST' });

        const res = await fetch(`${API_BASE}/data`);
        const state = await res.json();
        
        const ssot = state.local_storage_state || {};
        const ms = ssot.mutable_state || {};
        const portfolio = ms.portfolio_snapshot || [];
        const heldTickers = new Set(portfolio.map(p => p.ticker.toUpperCase()));
        
        // Filter tickers to only Held + Macro to prevent LLM context overload
        const filteredTickers = (state.tickers || []).filter(t => {
            const sym = t.ticker.toUpperCase();
            const isHeld = heldTickers.has(sym);
            const isMacro = (currentMacroTickers || []).map(m => m.toUpperCase()).includes(sym);
            return isHeld || isMacro;
        });

        const sessionPayload = {
            _meta: state._meta,
            timestamp: state.timestamp,
            status: state.status,
            tickers: filteredTickers
        };
        
        const bootPrompt = "SYSTEM BOOT: Initialize Council with the provided Session context. Execute Stage 0 Data Sync and provide a top-level market posture assessment.";
        const jsonString = bootPrompt + "\n\n```json\n" + JSON.stringify(sessionPayload, null, 2) + "\n```";
        
        await navigator.clipboard.writeText(jsonString);
        showFeedback(dCopySessionBtn, "✅ Copied!", "Session context copied & Log cleared!");
    } catch (e) {
        console.error(e);
        showFeedback(dCopySessionBtn, "❌ Error", "Failed to copy session data.", true);
    }
});



// Paste Payload Handler
dPasteBtn.addEventListener('click', async () => {
    dPasteBtn.disabled = true;
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
            showFeedback(dPasteBtn, "✅ Ingested!", "Payload successfully ingested!");
        } else {
            throw new Error(data.message);
        }
    } catch (e) {
        console.error("Paste Error: ", e);
        showFeedback(dPasteBtn, "❌ Error", e.message || "Failed to ingest payload.", true);
    } finally {
        dPasteBtn.disabled = false;
    }
});

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
        if (MACRO_TICKERS.includes(sym)) return;

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

        const scoutBadge = row._isScout ? `<span class="scout-badge">SCOUT</span>` : '';
        return `
            <tr>
                <td class="ticker-cell">${sym}${scoutBadge}</td>
                <td class="${pClass}">${p}</td>
                <td class="${gapColor}">${row.gap_percent > 0 ? '+' : ''}${row.gap_percent.toFixed(2)}%</td>
                <td>${formatVol(row.volume)}</td>
                <td>${row.atr_percent.toFixed(2)}%</td>
                <td class="${rsiColor}">${row.rsi.toFixed(1)}</td>
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
            <td colspan="10">${label}</td>
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
                    <td colspan="10">Scout Intelligence Suggestions</td>
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
                const alertsContent = document.getElementById('alerts-content');
                const topBar = document.getElementById('top-alert-bar');
                const vix = state.tickers.find(t => t.ticker === '^VIX');
                const ief = state.tickers.find(t => t.ticker === 'IEF');
                let alertsHtml = '';
                
                if(vix && vix.price > 20 && vix.gap_percent > 2.0) {
                    alertsHtml += `<div class="alert-item critical">⚠️ FEAR ALERT: VIX SPIKING (+${vix.gap_percent.toFixed(2)}%)</div>`;
                }
                if(ief && ief.gap_percent < -0.15) {
                    alertsHtml += `<div class="alert-item warning">📉 BOND ALERT: YIELDS RISING (BOND PRICE DROP)</div>`;
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

function renderPortfolio(portfolio) {
    if (!dPortfolioBody) return;
    let html = '';
    portfolio.forEach((item, index) => {
        html += `
            <tr data-index="${index}">
                <td style="color: var(--accent); font-weight: 700; font-size: 0.8rem;">${item.ticker}</td>
                <td><input type="number" class="portfolio-input" data-key="shares" value="${item.shares || 0}"></td>
                <td><input type="number" step="0.01" class="portfolio-input" data-key="wac" value="${item.wac || 0}"></td>
                <td><button class="delete-btn" onclick="deleteFromPortfolio(${index})">&times;</button></td>
            </tr>
        `;
    });
    dPortfolioBody.innerHTML = html;
}

async function addToPortfolio() {
    const ticker = dAddPortfolioTicker.value.trim().toUpperCase();
    if (!ticker) return;
    const portfolio = getCurrentPortfolio();
    if (portfolio.find(i => i.ticker === ticker)) return;
    portfolio.push({ ticker, shares: 0, wac: 0 });
    dAddPortfolioTicker.value = '';
    await savePortfolio(portfolio);
}

function getCurrentPortfolio() {
    const rows = dPortfolioBody.querySelectorAll('tr');
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

async function savePortfolio(portfolio) {
    const btn = dSavePortfolioBtn;
    const originalText = btn.innerHTML;
    btn.innerHTML = "WAIT...";
    btn.disabled = true;
    try {
        const res = await fetch(`${API_BASE}/basket`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(portfolio || getCurrentPortfolio())
        });
        if (res.ok) {
            btn.innerHTML = "SYNC ✅";
            await fetchPortfolio();
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
        if (res.ok) await fetchWatchlist();
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
        }
    } catch (e) { console.error("Scout categories update failed", e); }
}

// Global Exports
window.deleteFromPortfolio = deleteFromPortfolio;
window.deleteFromWatchlist = deleteFromWatchlist;
window.toggleScoutCategory = toggleScoutCategory;
window.toggleSection = toggleSection;

dAddToPortfolioBtn.addEventListener('click', addToPortfolio);
dSavePortfolioBtn.addEventListener('click', () => savePortfolio());
dAddToWatchlistBtn.addEventListener('click', addToWatchlist);

async function copyEODReviewPayload() {
    const btn = document.getElementById('btn-eod-review');
    try {
        const response = await fetch('/api/eod_review_payload');
        const data = await response.json();
        
        await navigator.clipboard.writeText(data.payload);
        showFeedback(btn, "✅ EOD Copied!", "EOD Review Payload copied to clipboard!");
    } catch (error) {
        console.error("Failed to copy EOD payload:", error);
        showFeedback(btn, "❌ Error", "Failed to fetch EOD log.", true);
    }
}

// Clear Decision Log Handler
const dClearLogBtn = document.getElementById('clear-log-btn');
if (dClearLogBtn) {
    dClearLogBtn.addEventListener('click', async () => {
        if (!confirm("Are you sure you want to clear the entire Decision Log? This cannot be undone.")) return;
        
        dClearLogBtn.disabled = true;
        try {
            const res = await fetch(`${API_BASE}/clear_decision_log`, {
                method: 'POST'
            });
            const data = await res.json();
            if (data.status === 'success') {
                showFeedback(dClearLogBtn, "✅ Cleared!", "Decision log successfully wiped!");
            } else {
                throw new Error(data.message);
            }
        } catch (e) {
            console.error("Clear Log Error: ", e);
            showFeedback(dClearLogBtn, "❌ Error", e.message || "Failed to clear log.", true);
        } finally {
            dClearLogBtn.disabled = false;
        }
    });
}


// ─── Mobile Quick Action Bridge ───
const dMobileCopyBtn = document.getElementById('mobile-copy-json-btn');
const dMobilePasteBtn = document.getElementById('mobile-paste-payload-btn');
const dMobileCopySessionBtn = document.getElementById('mobile-copy-session-btn');
const dMobileEODBtn = document.getElementById('mobile-btn-eod-review');

if (dMobileCopyBtn) dMobileCopyBtn.addEventListener('click', () => dCopyBtn.click());
if (dMobilePasteBtn) dMobilePasteBtn.addEventListener('click', () => dPasteBtn.click());
if (dMobileCopySessionBtn) dMobileCopySessionBtn.addEventListener('click', () => dCopySessionBtn.click());
if (dMobileEODBtn) dMobileEODBtn.addEventListener('click', () => copyEODReviewPayload());
