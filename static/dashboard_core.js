/* DASHBOARD CORE LOGIC - MATCHING ORIGINAL LOOK */
const Dashboard = {
    tableBody: null,
    marketStatus: null,
    lastUpdated: null,
    indicator: null,
    macroGrid: null,
    macroList: [],
    macroLabels: {},

    async init() {
        this.tableBody = document.getElementById('table-body');
        this.marketStatus = document.getElementById('market-status');
        this.lastUpdated = document.getElementById('last-updated');
        this.indicator = document.getElementById('live-indicator');
        this.macroGrid = document.getElementById('dynamic-macro-cards');
        this.basketBody = document.getElementById('basket-body');
        this.watchContainer = document.getElementById('watch-list-container');

        console.log("Dashboard Core v41 Initialized");
        
        // Initial setup
        await this.fetchConfig();
        await this.fetchBasket();
        await this.fetchWatchList();
        
        if (document.getElementById('save-basket-btn')) {
            document.getElementById('save-basket-btn').onclick = () => this.saveBasket();
        }

        if (document.getElementById('add-to-basket-btn')) {
            document.getElementById('add-to-basket-btn').onclick = () => this.addToBasket();
        }

        if (document.getElementById('add-to-watch-btn')) {
            document.getElementById('add-to-watch-btn').onclick = () => this.addToWatchList();
        }

        this.poll();
        setInterval(() => this.poll(), 3000);
    },

    async fetchBasket() {
        try {
            const res = await fetch('/api/get_basket');
            const data = await res.json();
            this.renderBasket(data);
        } catch (e) { console.error("Basket fetch failed", e); }
    },

    renderBasket(basket) {
        if (!this.basketBody) return;
        let html = '';
        basket.forEach((item, index) => {
            html += `
                <tr data-index="${index}">
                    <td style="padding: 4px; color: var(--accent-blue); font-weight: 700;">${item.ticker}</td>
                    <td style="padding: 4px;"><input type="number" class="basket-input" data-key="shares" value="${item.shares}" style="width: 50px; background: transparent; border: 1px solid rgba(255,255,255,0.1); color: white; font-size: 0.8rem; border-radius: 4px;"></td>
                    <td style="padding: 4px;"><input type="number" step="0.01" class="basket-input" data-key="wac" value="${item.wac}" style="width: 60px; background: transparent; border: 1px solid rgba(255,255,255,0.1); color: white; font-size: 0.8rem; border-radius: 4px;"></td>
                    <td style="padding: 4px;"><button onclick="Dashboard.deleteFromBasket(${index})" style="background: transparent; border: none; color: #da3633; cursor: pointer; font-size: 1rem;">×</button></td>
                </tr>
            `;
        });
        this.basketBody.innerHTML = html;
    },

    async addToBasket() {
        const input = document.getElementById('add-basket-ticker');
        const ticker = input.value.trim().toUpperCase();
        if (!ticker) return;

        // Get current basket
        const rows = this.basketBody.querySelectorAll('tr');
        const basket = [];
        rows.forEach(row => {
            basket.push({
                ticker: row.cells[0].textContent,
                shares: parseFloat(row.querySelector('[data-key="shares"]').value),
                wac: parseFloat(row.querySelector('[data-key="wac"]').value)
            });
        });

        if (basket.find(i => i.ticker === ticker)) {
            alert("Ticker already in basket.");
            return;
        }

        basket.push({ ticker, shares: 0, wac: 0 });
        input.value = '';
        await this.pushBasketUpdate(basket);
    },

    async deleteFromBasket(index) {
        const rows = this.basketBody.querySelectorAll('tr');
        const basket = [];
        rows.forEach((row, idx) => {
            if (idx === index) return;
            basket.push({
                ticker: row.cells[0].textContent,
                shares: parseFloat(row.querySelector('[data-key="shares"]').value),
                wac: parseFloat(row.querySelector('[data-key="wac"]').value)
            });
        });
        await this.pushBasketUpdate(basket);
    },

    async saveBasket() {
        const rows = this.basketBody.querySelectorAll('tr');
        const basket = [];
        rows.forEach(row => {
            basket.push({
                ticker: row.cells[0].textContent,
                shares: parseFloat(row.querySelector('[data-key="shares"]').value),
                wac: parseFloat(row.querySelector('[data-key="wac"]').value)
            });
        });
        await this.pushBasketUpdate(basket);
    },

    async pushBasketUpdate(basket) {
        const btn = document.getElementById('save-basket-btn');
        const originalText = btn ? btn.textContent : 'SYNC';
        if (btn) {
            btn.textContent = 'SYNCING...';
            btn.disabled = true;
        }

        try {
            const res = await fetch('/api/save_basket', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(basket)
            });
            if (res.ok) {
                if (btn) btn.textContent = 'SAVED ✅';
                await this.fetchBasket();
                this.poll(); // Refresh dashboard
            } else {
                if (btn) btn.textContent = 'ERROR ❌';
            }
        } catch (e) { 
            console.error("Basket update failed", e);
            if (btn) btn.textContent = 'ERROR ❌';
        } finally {
            setTimeout(() => {
                if (btn) {
                    btn.textContent = originalText;
                    btn.disabled = false;
                }
            }, 2000);
        }
    },

    async fetchWatchList() {
        try {
            const res = await fetch('/api/get_watch_list');
            const data = await res.json();
            this.renderWatchList(data);
        } catch (e) { console.error("Watch list fetch failed", e); }
    },

    renderWatchList(list) {
        if (!this.watchContainer || !Array.isArray(list)) return;
        let html = '';
        list.forEach((ticker, index) => {
            html += `
                <div class="watch-item" style="background: rgba(255,255,255,0.05); padding: 2px 8px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.1); display: flex; align-items: center; gap: 5px; font-size: 0.75rem;">
                    <span style="font-weight: 600; color: #8b949e;">${ticker}</span>
                    <button onclick="Dashboard.deleteFromWatchList(${index})" style="background: transparent; border: none; color: #da3633; cursor: pointer; padding: 0; font-size: 0.9rem;">×</button>
                </div>
            `;
        });
        this.watchContainer.innerHTML = html;
    },

    async addToWatchList() {
        const input = document.getElementById('add-watch-ticker');
        const ticker = input.value.trim().toUpperCase();
        if (!ticker) return;

        try {
            const res = await fetch('/api/get_watch_list');
            const list = await res.json();
            if (list.includes(ticker)) return;
            list.push(ticker);
            await this.pushWatchListUpdate(list);
            input.value = '';
        } catch (e) {}
    },

    async deleteFromWatchList(index) {
        try {
            const res = await fetch('/api/get_watch_list');
            const list = await res.json();
            list.splice(index, 1);
            await this.pushWatchListUpdate(list);
        } catch (e) {}
    },

    async pushWatchListUpdate(list) {
        try {
            const res = await fetch('/api/save_watch_list', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(list)
            });
            if (res.ok) {
                await this.fetchWatchList();
                this.poll(); // Refresh dashboard
            }
        } catch (e) { console.error("Watch list update failed", e); }
    },

    async fetchConfig() {
        try {
            const res = await fetch('/api/tickers');
            const data = await res.json();
            this.macroList = data.macro || [];
            this.macroLabels = data.macro_labels || {};
        } catch (e) {
            console.error("Config fetch failed", e);
        }
    },

    async poll() {
        try {
            // Only refresh basket if user isn't currently typing in it
            const activeInput = document.activeElement;
            if (!activeInput || !activeInput.classList.contains('basket-input')) {
                await this.fetchBasket();
                await this.fetchWatchList();
            }

            const res = await fetch('/api/data');
            if (!res.ok) throw new Error("Data fetch failed");
            const state = await res.json();
            
            if (state && state.tickers) {
                this.renderTable(state.tickers);
                this.renderMacroHud(state.tickers);
                if (this.marketStatus) this.marketStatus.textContent = state.status || 'OPEN';
                if (this.lastUpdated) this.lastUpdated.textContent = new Date().toLocaleTimeString();
                if (this.indicator) this.indicator.classList.add('active');
            }
        } catch (e) {
            console.error("Dashboard Poll Error:", e);
        }
    },

    renderMacroHud(allTickers) {
        if (!this.macroGrid || !this.macroList.length) return;
        
        let html = '';
        this.macroList.forEach(sym => {
            const ticker = allTickers.find(t => t.ticker === sym);
            if (!ticker) return;

            const label = this.macroLabels[sym] || sym;
            const price = ticker.price ? ticker.price.toFixed(2) : '—';
            const change = ticker.session_change_pct || 0;
            const changeStr = (change >= 0 ? '+' : '') + change.toFixed(2) + '%';
            const changeClass = change >= 0 ? 'up' : 'down';

            html += `
                <div class="macro-card">
                    <span class="macro-label">${label}</span>
                    <span class="macro-val">${price}</span>
                    <div class="macro-sub ${changeClass}">${changeStr}</div>
                </div>
            `;
        });
        this.macroGrid.innerHTML = html;
    },

    formatVolume(v) {
        if (!v) return '—';
        if (v >= 1000000) return (v / 1000000).toFixed(2) + 'M';
        if (v >= 1000) return (v / 1000).toFixed(1) + 'K';
        return v.toString();
    },

    renderTable(tickers) {
        if (!this.tableBody) return;
        
        const tableData = tickers.filter(t => !this.macroList.includes(t.ticker));

        let html = '';
        tableData.forEach(row => {
            const sym = row.ticker;
            const ssrBadge = row.ssr_active ? '<span class="tag down" style="font-size: 0.6rem; margin-left: 5px; padding: 1px 4px;">SSR</span>' : '';
            const p = row.price ? row.price.toFixed(2) : '—';
            
            const gap = row.gap_percent || row.session_change_pct || 0;
            const gapStr = (gap >= 0 ? '+' : '') + gap.toFixed(2) + '%';
            const gapClass = gap >= 0 ? 'up' : 'down';

            // Trend mapping
            let trendIcon = '—';
            let trendClass = 'flat';
            if (row.trend === 'UP') { trendIcon = '▲'; trendClass = 'up'; }
            else if (row.trend === 'DOWN') { trendIcon = '▼'; trendClass = 'down'; }
            
            // Posture mapping
            let postureClass = 'neutral';
            if (row.dealer_posture?.includes('LONG')) postureClass = 'up';
            else if (row.dealer_posture?.includes('SHORT')) postureClass = 'down';

            const score = row.score || 0;
            let scoreClass = 'flat';
            if (score > 0) scoreClass = 'up';
            else if (score < 0) scoreClass = 'down';

            html += `
                <tr>
                    <td class="ticker-cell">${sym}${ssrBadge}</td>
                    <td class="price-cell">${p}</td>
                    <td class="${gapClass}">${gapStr}</td>
                    <td>${this.formatVolume(row.volume)}</td>
                    <td>${row.atr_percent ? row.atr_percent.toFixed(2) + '%' : '—'}</td>
                    <td>${row.rsi ? row.rsi.toFixed(1) : '—'}</td>
                    <td>${row.vwap ? row.vwap.toFixed(2) : '—'}</td>
                    <td><span class="tag ${trendClass}">${trendIcon} ${row.trend || 'FLAT'}</span></td>
                    <td><span class="posture-tag ${postureClass}">${row.dealer_posture || 'NEUTRAL'}</span></td>
                    <td>
                        <span class="score-badge ${scoreClass}">${score > 0 ? '+' : ''}${score}</span>
                        ${row.status_note ? `<span style="font-size: 0.7rem; color: var(--text-secondary); margin-left: 5px;">(${row.status_note})</span>` : ''}
                    </td>
                </tr>
            `;
        });
        this.tableBody.innerHTML = html;
    }
};

// Global Exposure
window.Dashboard = Dashboard;
window.addEventListener('load', () => Dashboard.init());
