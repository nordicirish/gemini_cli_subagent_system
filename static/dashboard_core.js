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

        console.log("Dashboard Core v40 Initialized");
        
        // Initial setup
        await this.fetchConfig();
        this.poll();
        setInterval(() => this.poll(), 3000);
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
            const res = await fetch('/api/data');
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
                    <td class="ticker-cell">${sym}</td>
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

window.addEventListener('load', () => Dashboard.init());
