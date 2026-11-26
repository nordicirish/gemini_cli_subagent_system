// Portfolio panel: the single owner of the portfolio manager (holdings, cash, live-FX totals,
// draft rows, degraded/stale state, saving). Loaded before app.js and modern_ui.js.
// Before this file existed, app.js and modern_ui.js each had their own fetchPortfolio /
// renderPortfolio, and modern_ui.js silently replaced app.js's copy through window.*.
//
// Uses from app.js, only inside functions that run after page load: latestTickersMap (live
// prices, updated by pollData), showFeedback (button feedback) and pollData (refresh after save).

const API_BASE_PORTFOLIO = '/api';
const dPortfolioBody = document.getElementById('portfolio-manager-body');
const dAddPortfolioTicker = document.getElementById('add-portfolio-ticker');
const dAddToPortfolioBtn = document.getElementById('add-to-portfolio-btn');
const dSavePortfolioBtn = document.getElementById('save-portfolio-btn');

// Live EUR/USD (USD per EUR) from /api/basket. null = no fresh quote: conversions show "—"
// instead of assuming a rate. USD cash itself is derived server-side.
let currentEurUsdRate = null;
const FX_UNAVAILABLE_TEXT = '—';
const FX_UNAVAILABLE_TITLE = ' title="Live EUR/USD rate unavailable"';

function liveEurUsdRate(candidate) {
    const n = Number(candidate);
    return candidate !== null && candidate !== undefined && Number.isFinite(n) && n > 0 ? n : null;
}

// Rate for a render: an explicit eurusd_rate key (even null) wins; otherwise keep the current one.
function resolveRenderRate(data) {
    return (data && Object.prototype.hasOwnProperty.call(data, 'eurusd_rate'))
        ? liveEurUsdRate(data.eurusd_rate)
        : currentEurUsdRate;
}

function formatUsdCash(cashEur, rate) {
    return rate ? `$${(cashEur * rate).toFixed(2)}` : FX_UNAVAILABLE_TEXT;
}

// positionsUsd: market value of holdings (USD). Cash is EUR; totals need the live rate.
function formatPortfolioTotals(cashEur, positionsUsd, rate) {
    if (!rate) return { eur: FX_UNAVAILABLE_TEXT, usd: FX_UNAVAILABLE_TEXT };
    const totalUsd = cashEur * rate + positionsUsd;
    return {
        eur: `€${Math.round(totalUsd / rate).toLocaleString()}`,
        usd: `$${Math.round(totalUsd).toLocaleString()}`,
    };
}

// Degraded / stale state and the authoritative fetch + render (formerly ModernChat methods)
const PortfolioPanel = {
    lastValidPortfolioBasket: null,
    portfolioDegraded: false,
    portfolioDegradedDetail: null,

    markPortfolioDegraded(detail) {
        const msg = detail || 'Authoritative portfolio persistence unavailable';
        const isStale = this.lastValidPortfolioBasket !== null;
        this.portfolioDegraded = true;
        this.portfolioDegradedDetail = msg;

        const tbody = document.getElementById('portfolio-manager-body');
        const container =
            document.getElementById('portfolio-manager-section')
            || document.getElementById('portfolio-content')
            || tbody;

        if (tbody && typeof tbody.setAttribute === 'function') {
            tbody.setAttribute('data-portfolio-state', 'degraded');
            tbody.setAttribute('data-portfolio-stale', isStale ? 'true' : 'false');
        }
        if (container && typeof container.setAttribute === 'function') {
            container.setAttribute('data-portfolio-state', 'degraded');
            container.setAttribute('data-portfolio-stale', isStale ? 'true' : 'false');
        }

        if (container) {
            let banner = document.getElementById('portfolio-degraded-banner');
            if (!banner) {
                banner = document.createElement('div');
                banner.id = 'portfolio-degraded-banner';
                banner.className = 'portfolio-degraded-banner';
                banner.style.cssText = 'margin: 8px 0; padding: 8px 10px; border-radius: 6px; border: 1px solid #f85149; background: rgba(248, 81, 73, 0.12); color: #ff7b72; font-size: 0.78rem; font-weight: 700;';
                if (typeof container.insertBefore === 'function' && container.firstChild) {
                    container.insertBefore(banner, container.firstChild);
                } else if (typeof container.appendChild === 'function') {
                    container.appendChild(banner);
                }
            }
            banner.style.display = 'block';
            banner.setAttribute('data-portfolio-stale', isStale ? 'true' : 'false');
            banner.textContent = isStale
                ? `⚠️ STALE PORTFOLIO DATA — ${msg} (Showing last known valid portfolio)`
                : `⚠️ PORTFOLIO UNAVAILABLE — ${msg}`;
        }
    },

    clearPortfolioDegraded() {
        this.portfolioDegraded = false;
        this.portfolioDegradedDetail = null;

        const tbody = document.getElementById('portfolio-manager-body');
        const container =
            document.getElementById('portfolio-manager-section')
            || document.getElementById('portfolio-content')
            || tbody;

        if (tbody && typeof tbody.setAttribute === 'function') {
            tbody.setAttribute('data-portfolio-state', 'authoritative');
            tbody.setAttribute('data-portfolio-stale', 'false');
        }
        if (container && typeof container.setAttribute === 'function') {
            container.setAttribute('data-portfolio-state', 'authoritative');
            container.setAttribute('data-portfolio-stale', 'false');
        }

        const banner = document.getElementById('portfolio-degraded-banner');
        if (banner) {
            banner.style.display = 'none';
            banner.textContent = '';
            banner.setAttribute('data-portfolio-stale', 'false');
        }
    },

    async fetchPortfolio(force = false) {
        const tbody = document.getElementById('portfolio-manager-body');
        const hasDraft = tbody && typeof tbody.querySelector === 'function'
            && Boolean(tbody.querySelector('tr.portfolio-item-row[data-draft="true"]'));
        if (!force && hasDraft) {
            return;
        }
        if (
            !force
            && document.activeElement
            && document.activeElement.classList
            && document.activeElement.classList.contains('portfolio-input')
        ) {
            return;
        }
        try {
            const res = await fetch('/api/basket');
            const statusOk = res && (res.ok === true || (res.ok === undefined && (!res.status || res.status < 400)));
            if (!statusOk) {
                let detail = `Authoritative portfolio persistence unavailable (HTTP ${(res && res.status) || 'error'})`;
                try {
                    const errBody = await res.json();
                    if (errBody && (errBody.detail || errBody.message)) {
                        detail = errBody.detail || errBody.message;
                    }
                } catch (_) {}
                this.markPortfolioDegraded(detail);
                return;
            }
            const data = await res.json();
            if (!data || data.status === 'error' || !Array.isArray(data.portfolio)) {
                this.markPortfolioDegraded((data && (data.detail || data.message)) || 'Invalid portfolio payload');
                return;
            }
            this.renderPortfolio(data);
        } catch (e) {
            this.markPortfolioDegraded((e && e.message) || 'Portfolio fetch failed');
        }
    },

    renderPortfolio(data) {
        if (!data || data.status === 'error' || !Array.isArray(data.portfolio)) {
            this.markPortfolioDegraded(
                (data && (data.detail || data.message)) || 'Authoritative portfolio persistence unavailable'
            );
            return false;
        }

        const hasDraft = Boolean(data._hasDraft);
        if (!hasDraft) {
            this.lastValidPortfolioBasket = JSON.parse(JSON.stringify(data));
            this.clearPortfolioDegraded();
        }

        const tbody = document.getElementById('portfolio-manager-body');
        if (!tbody) return true;

        const portfolio = data.portfolio;
        const cashRaw = data.unallocated_cash_eur !== undefined && data.unallocated_cash_eur !== null ? data.unallocated_cash_eur : 0;
        const cashNum = Number(cashRaw) || 0;
        // Live EUR/USD only, never an assumed rate. An explicit eurusd_rate key (even null) wins.
        const rate = resolveRenderRate(data);
        currentEurUsdRate = rate;
        const fxTitle = rate ? '' : FX_UNAVAILABLE_TITLE;
        const cashUsd = formatUsdCash(cashNum, rate);
        const tickerMap = (typeof latestTickersMap !== 'undefined' && latestTickersMap) ? latestTickersMap : {};

        let totalUsd = 0; // positions only; cash is added below via the live rate
        let html = '';
        portfolio.forEach((item, index) => {
            const sharesVal = item.shares !== undefined && item.shares !== null ? item.shares : 0;
            const wacVal = item.wac !== undefined && item.wac !== null ? item.wac : 0;
            const isDraft = Boolean(item._draft);
            const wacNum = Number(wacVal);
            const showEmptyWac = isDraft && !(wacNum > 0);
            const livePrice = (tickerMap[item.ticker] && tickerMap[item.ticker].price) || (wacNum > 0 ? wacNum : 0);
            totalUsd += (Number(sharesVal) || 0) * livePrice;
            const draftAttr = isDraft ? ' data-draft="true"' : '';
            const wacInputVal = showEmptyWac ? '' : wacVal;
            const wacPlaceholderAttr = showEmptyWac ? ' placeholder="WAC > 0" style="border-color: var(--yellow, #d29922);"' : '';
            const escTicker = String(item.ticker || '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
            html += `
            <tr data-index="${index}"${draftAttr} class="portfolio-item-row">
                <td style="color: var(--accent); font-weight: 700; font-size: 0.8rem;">${escTicker}</td>
                <td><input type="number" class="portfolio-input" data-key="shares" value="${sharesVal}" oninput="updatePortfolioTotals()" onchange="savePortfolio()"></td>
                <td><input type="number" step="0.01" class="portfolio-input" data-key="wac" value="${wacInputVal}"${wacPlaceholderAttr} oninput="updatePortfolioTotals()" onchange="savePortfolio()"></td>
                <td><button class="delete-btn" onclick="deleteFromPortfolio(${index})">&times;</button></td>
            </tr>
        `;
        });

        const totals = formatPortfolioTotals(cashNum, totalUsd, rate);
        const totalEurText = totals.eur;
        const totalUsdText = totals.usd;
        html += `
        <tr class="cash-row" style="background: rgba(0, 255, 148, 0.05);">
            <td style="color: var(--green); font-weight: 700; font-size: 0.75rem;">CASH (€)</td>
            <td><input type="number" step="0.01" class="portfolio-input" id="cash-input-eur" value="${cashRaw}" style="color: var(--green);" oninput="updatePortfolioTotals()" onchange="savePortfolio()"></td>
            <td colspan="2" id="cash-val-usd"${fxTitle} style="font-size: 0.75rem; font-weight: 700; color: #fff; text-align: left; padding-left: 8px; font-family: var(--font-mono);">${cashUsd}</td>
        </tr>
        <tr class="total-row" style="background: rgba(88, 166, 255, 0.08);">
            <td style="color: var(--accent); font-weight: 700; font-size: 0.75rem;">TOTAL</td>
            <td id="total-val-eur"${fxTitle} style="font-size: 0.75rem; font-weight: 700; color: #fff; font-family: var(--font-mono);">${totalEurText}</td>
            <td colspan="2" id="total-val-usd"${fxTitle} style="font-size: 0.75rem; font-weight: 700; color: #fff; text-align: left; padding-left: 8px; font-family: var(--font-mono);">${totalUsdText}</td>
        </tr>
    `;

        tbody.innerHTML = html;
        return true;
    }
};

function fetchPortfolio(force = false) {
    return PortfolioPanel.fetchPortfolio(force);
}

function renderPortfolio(data) {
    return PortfolioPanel.renderPortfolio(data);
}

// Portfolio Logic
function getPortfolioFeedbackEl() {
    return document.getElementById('portfolio-feedback');
}

function setPortfolioHint(message, colorClass = 'text-yellow') {
    const feedbackEl = getPortfolioFeedbackEl();
    if (!feedbackEl) return;
    if (!message) {
        feedbackEl.textContent = '';
        feedbackEl.className = 'status-message inline-feedback';
        return;
    }
    feedbackEl.textContent = message;
    feedbackEl.className = `status-message inline-feedback active ${colorClass}`;
}

function updatePortfolioTotals() {
    const portfolio = getCurrentPortfolio();
    const cashEur = getCurrentCash();
    const rate = currentEurUsdRate;

    const cashUsdEl = document.getElementById('cash-val-usd');
    if (cashUsdEl) {
        cashUsdEl.textContent = formatUsdCash(cashEur, rate);
    }

    let positionsUsd = 0;
    portfolio.forEach(item => {
        const livePrice = (latestTickersMap[item.ticker] && latestTickersMap[item.ticker].price) || item.wac || 0;
        positionsUsd += (item.shares || 0) * livePrice;
    });
    const totals = formatPortfolioTotals(cashEur, positionsUsd, rate);

    const totalEurEl = document.getElementById('total-val-eur');
    const totalUsdEl = document.getElementById('total-val-usd');

    if (totalEurEl) totalEurEl.textContent = totals.eur;
    if (totalUsdEl) totalUsdEl.textContent = totals.usd;
}

async function addToPortfolio() {
    const ticker = dAddPortfolioTicker.value.trim().toUpperCase();
    if (!ticker) return;
    const portfolio = getCurrentPortfolio();
    if (portfolio.find(i => i.ticker === ticker)) {
        setPortfolioHint(`${ticker} is already in your portfolio.`, 'text-yellow');
        return;
    }

    const livePriceRaw = latestTickersMap[ticker] && Number(latestTickersMap[ticker].price);
    const hasLivePrice = Number.isFinite(livePriceRaw) && livePriceRaw > 0;
    dAddPortfolioTicker.value = '';

    const rate = Number(currentEurUsdRate);
    const hasLiveFx = Number.isFinite(rate) && rate > 0;

    if (hasLivePrice && hasLiveFx) {
        // WAC is stored in EUR; seed it from the USD quote only when live EUR/USD FX is known.
        const seedWac = livePriceRaw / rate;
        portfolio.push({
            ticker,
            shares: 1,
            wac: Number(seedWac.toFixed(2)),
            _draft: false
        });
        const hasDraft = portfolio.some(i => Boolean(i._draft));
        renderPortfolio({
            portfolio: portfolio,
            unallocated_cash_eur: getCurrentCash(),
            eurusd_rate: currentEurUsdRate,
            _hasDraft: hasDraft
        });
        if (!hasDraft) setPortfolioHint('');
        updatePortfolioTotals();
        await savePortfolio(portfolio, { explicit: true });
        return;
    }

    portfolio.push({ ticker, shares: 1, wac: 0, _draft: true });
    renderPortfolio({
        portfolio: portfolio,
        unallocated_cash_eur: getCurrentCash(),
        eurusd_rate: currentEurUsdRate,
        _hasDraft: true
    });
    updatePortfolioTotals();
    const wacHint = hasLivePrice
        ? `Live EUR/USD FX unavailable — enter EUR WAC (> 0) for ${ticker} and press Enter or SYNC to save.`
        : `Enter WAC (> 0) for ${ticker} and press Enter or SYNC to save.`;
    setPortfolioHint(wacHint, 'text-yellow');
    if (dPortfolioBody) {
        const draftRow = dPortfolioBody.querySelector('tr.portfolio-item-row[data-draft="true"]:last-of-type')
            || dPortfolioBody.querySelector('tr.portfolio-item-row[data-draft="true"]');
        const wacInput = draftRow && draftRow.querySelector('[data-key="wac"]');
        if (wacInput) wacInput.focus();
    }
}

function getCurrentPortfolio() {
    const rows = dPortfolioBody.querySelectorAll('tr.portfolio-item-row');
    const portfolio = [];
    rows.forEach(row => {
        portfolio.push({
            ticker: (row.cells[0].textContent || '').trim().toUpperCase(),
            shares: parseFloat(row.querySelector('[data-key="shares"]').value) || 0,
            wac: parseFloat(row.querySelector('[data-key="wac"]').value) || 0,
            _draft: row.getAttribute('data-draft') === 'true'
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

async function savePortfolio(portfolioArr, options = {}) {
    const pArray = portfolioArr || getCurrentPortfolio();
    const missingWac = pArray.find(i => (Number(i.shares) || 0) > 0 && !(Number(i.wac) > 0));
    const btn = dSavePortfolioBtn;
    const feedbackEl = getPortfolioFeedbackEl();

    if (missingWac) {
        const msg = `Enter a positive WAC (> 0) for ${missingWac.ticker} to save.`;
        if (options.explicit && btn) {
            showFeedback(btn, "WAC?", msg, true, feedbackEl);
        } else {
            setPortfolioHint(msg, 'text-yellow');
        }
        if (dPortfolioBody) {
            const rows = dPortfolioBody.querySelectorAll('tr.portfolio-item-row');
            for (const row of rows) {
                const rowTicker = (row.cells[0].textContent || '').trim().toUpperCase();
                if (rowTicker === missingWac.ticker) {
                    const wacInput = row.querySelector('[data-key="wac"]');
                    if (wacInput) wacInput.focus();
                    break;
                }
            }
        }
        return;
    }

    if (isSavingPortfolio) {
        pendingPortfolioSave = pArray;
        return;
    }
    isSavingPortfolio = true;
    if (btn) {
        btn.innerHTML = "WAIT...";
        btn.disabled = true;
    }
    try {
        const cVal = getCurrentCash();
        const rate = currentEurUsdRate;
        const cleanPortfolio = pArray.map(({ ticker, shares, wac }) => ({ ticker, shares, wac }));
        // USD cash is derived server-side from EUR at the live rate (unchanged if no fresh rate)
        const payload = {
            portfolio: cleanPortfolio,
            unallocated_cash_eur: cVal
        };
        const res = await fetch(`${API_BASE_PORTFOLIO}/basket`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            const resData = await res.json();
            if (resData && resData.portfolio) {
                renderPortfolio({
                    portfolio: resData.portfolio,
                    unallocated_cash_eur: resData.unallocated_cash_eur !== undefined ? resData.unallocated_cash_eur : cVal,
                    eurusd_rate: rate
                });
            }
            setPortfolioHint('');
            if (btn) {
                btn.innerHTML = "SYNC";
                btn.disabled = false;
                showFeedback(btn, "SYNC ✅", "Portfolio saved!", false, feedbackEl);
            }
            updatePortfolioTotals();
            // Trigger an immediate background poll to update the main dashboard table
            await pollData();
        } else {
            let detail = `Failed to save portfolio (HTTP ${res.status})`;
            try {
                const errBody = await res.json();
                if (errBody && (errBody.detail || errBody.message)) {
                    detail = errBody.detail || errBody.message;
                }
            } catch (_) {}
            if (btn) {
                btn.innerHTML = "SYNC";
                btn.disabled = false;
                showFeedback(btn, "SYNC ❌", detail, true, feedbackEl);
            } else {
                setPortfolioHint(detail, 'text-red');
            }
        }
    } catch (e) { 
        console.error("Portfolio update failed", e); 
        const msg = (e && e.message) || "Portfolio update failed";
        if (btn) {
            btn.innerHTML = "SYNC";
            btn.disabled = false;
            showFeedback(btn, "SYNC ❌", msg, true, feedbackEl);
        } else {
            setPortfolioHint(msg, 'text-red');
        }
    } finally {
        isSavingPortfolio = false;
        if (btn) {
            btn.disabled = false;
            if (btn.dataset.isFeedback !== "true") {
                btn.innerHTML = "SYNC";
            }
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
        const hasDraft = portfolio.some(i => Boolean(i._draft));
        renderPortfolio({
            portfolio: portfolio,
            unallocated_cash_eur: getCurrentCash(),
            eurusd_rate: currentEurUsdRate,
            _hasDraft: hasDraft
        });
        if (!hasDraft) setPortfolioHint('');
        updatePortfolioTotals();
        await savePortfolio(portfolio);
    }
}

// --- Event wiring -------------------------------------------------------------------
if (dAddPortfolioTicker) {
    dAddPortfolioTicker.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addToPortfolio();
    });
}
if (dAddToPortfolioBtn) dAddToPortfolioBtn.addEventListener('click', addToPortfolio);
if (dSavePortfolioBtn) dSavePortfolioBtn.addEventListener('click', () => savePortfolio(null, { explicit: true }));

// Real-time conversion feedback as user types cash value
if (dPortfolioBody) dPortfolioBody.addEventListener('input', (e) => {
    if (e.target.id === 'cash-input-eur') {
        const val = parseFloat(e.target.value) || 0;
        const usdDisplay = e.target.closest('tr').querySelector('td[colspan="2"]');
        if (usdDisplay) {
            usdDisplay.textContent = formatUsdCash(val, currentEurUsdRate);
        }
    }
});

// Save immediately on Enter inside portfolio table inputs (replaces duplicate change listener)
if (dPortfolioBody) dPortfolioBody.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter' && e.target.classList && e.target.classList.contains('portfolio-input')) {
        e.preventDefault();
        await savePortfolio(null, { explicit: true });
    }
});

// Inline handlers in rendered rows (oninput/onchange/onclick) and other scripts use these
window.PortfolioPanel = PortfolioPanel;
window.fetchPortfolio = fetchPortfolio;
window.renderPortfolio = renderPortfolio;
window.savePortfolio = savePortfolio;
window.deleteFromPortfolio = deleteFromPortfolio;
window.updatePortfolioTotals = updatePortfolioTotals;
